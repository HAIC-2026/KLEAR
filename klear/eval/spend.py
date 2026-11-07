# Spend ledger that caps total live eval cost across runs and processes, with per-call reservations under an exclusive file lock.
import json
import math
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import yaml

from klear.eval.golden import ROOT
from klear.filelock import BINARY, exclusive

PRICES = ROOT / "eval" / "prices.yaml"
CAP = 60.0
# Fixed per-user locations, shared by every checkout; tests monkeypatch these module constants.
LEDGER = Path.home() / ".config" / "klear" / "eval_spend.jsonl"
LOCK = Path.home() / ".config" / "klear" / "eval_spend.lock"
UNCERTAIN = Path.home() / ".config" / "klear" / "eval_spend.uncertain"
MIRROR = Path.home() / ".config" / "klear" / "eval_spend_total"
HW_WIDTH = 32
EPS = 1e-9


class SpendCapError(RuntimeError):
    pass


class LedgerError(RuntimeError):
    pass


def load_prices(path: Path = PRICES) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _model(prices: dict, model: str) -> dict:
    if model not in prices["models"]:
        raise KeyError(f"unknown model {model!r}, known models: {', '.join(sorted(prices['models']))}")
    return prices["models"][model]


def estimate_cost(model: str, n_questions: int, prices: Path = PRICES) -> float:
    p = load_prices(prices)
    rate, tokens = _model(p, model), p["assumed_tokens_per_question"]
    return n_questions * (tokens["input"] * rate["input"] + tokens["output"] * rate["output"]) / 1e6


def usage_problem(usage) -> str | None:
    """Describe the first invalid field of a usage record, or None when all are valid."""
    for name in ("input_tokens", "output_tokens"):
        v = getattr(usage, name, None)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            return f"{name} must be a non-negative integer, got {v!r}"
    sec = getattr(usage, "seconds", None)
    if isinstance(sec, bool) or not isinstance(sec, int | float) or not math.isfinite(sec) or sec < 0:
        return f"seconds must be finite and non-negative, got {sec!r}"
    return None


def request_cost(model: str, usage, prices: Path = PRICES) -> float:
    if (problem := usage_problem(usage)) is not None:
        raise ValueError(problem)
    rate = _model(load_prices(prices), model)
    return (usage.input_tokens * rate["input"] + usage.output_tokens * rate["output"]) / 1e6


def _amount(name: str, v) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or v < 0:
        raise ValueError(f"{name} must be a finite amount of at least 0, got {v!r}")
    return float(v)


class SpendLog:
    def __init__(self, cap: float = CAP):
        self.cap = cap

    path = property(lambda self: LEDGER)
    mirror = property(lambda self: MIRROR)
    lock = property(lambda self: LOCK)

    @contextmanager
    def _locked(self):
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        # No O_APPEND: the record must overwrite offset 0.
        fd = os.open(self.lock, os.O_RDWR | os.O_CREAT | BINARY, 0o600)
        try:
            with exclusive(fd):
                self._fd = fd
                try:
                    yield
                finally:
                    self._fd = None
        finally:
            os.close(fd)

    def _read_hw(self) -> float:
        """Highest record in the lock file; older appending versions may have left several."""
        size = os.fstat(self._fd).st_size
        os.lseek(self._fd, 0, os.SEEK_SET)  # os.pread and os.pwrite do not exist on Windows
        lines = os.read(self._fd, size).decode("ascii", "replace").split("\n")
        try:
            return max((_amount("lock high-water", float(x)) for x in lines if x.strip()), default=0.0)
        except ValueError as e:
            raise LedgerError(f"spend lock file {self.lock} is corrupt: {e}") from e

    def _write_hw(self, total: float) -> None:
        record = f"{max(total, self._read_hw()):.6f}".ljust(HW_WIDTH - 1).encode("ascii") + b"\n"
        if len(record) != HW_WIDTH:
            raise LedgerError(f"spend high-water {total!r} does not fit the {HW_WIDTH}-byte lock record")
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, record)
        os.ftruncate(self._fd, HW_WIDTH)
        os.fsync(self._fd)

    def _read_mirror(self) -> float:
        try:
            return _amount("mirror total", float(self.mirror.read_text(encoding="utf-8").strip()))
        except ValueError as e:
            raise LedgerError(f"spend total copy {self.mirror} is corrupt: {e}") from e

    def _write_mirror(self, total: float) -> None:
        tmp = self.mirror.with_name(self.mirror.name + f".{os.getpid()}.tmp")
        try:
            self.mirror.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(repr(total) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.mirror)
        except OSError as e:
            raise LedgerError(f"spend total copy {self.mirror} cannot be written: {e}") from e

    def _records(self) -> list[dict]:
        out = []
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if r["type"] == "reserve":
                    _amount("estimate", r["estimate"])
                elif r["type"] == "settle":
                    _amount("cost", r["cost"])
                else:
                    raise ValueError(f"unknown type {r['type']!r}")
                if not isinstance(r["id"], str):
                    raise TypeError("id must be a string")
            except (ValueError, KeyError, TypeError) as e:
                raise LedgerError(f"spend ledger {self.path} line {n} is corrupt: {e}") from e
            out.append(r)
        return out

    def _load(self) -> tuple[float, float]:
        """Settled total (max of ledger, outside copy and lock high-water) and open holds; fail closed."""
        led, mir, hw = self.path.exists(), self.mirror.exists(), self._read_hw()
        if led and not mir:
            raise LedgerError(f"spend total copy {self.mirror} is missing but the ledger exists")
        if mir and not led and self._read_mirror() > 0:
            raise LedgerError(f"spend ledger {self.path} is missing but the total copy says it had spend")
        if hw > 0 and not (led and mir):
            raise LedgerError(
                f"spend ledger and total copy are missing but the lock {self.lock} records spend"
            )
        recs = self._records() if led else []
        settled = {r["id"] for r in recs if r["type"] == "settle"}
        spent = sum(r["cost"] for r in recs if r["type"] == "settle")
        held = sum(r["estimate"] for r in recs if r["type"] == "reserve" and r["id"] not in settled)
        return max(spent, self._read_mirror() if mir else 0.0, hw), held

    def _append(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def total(self) -> float:
        """Settled spend."""
        with self._locked():
            return self._load()[0]

    def snapshot(self) -> tuple[float, float]:
        """Settled spend and open holds, whose sum is what the cap counts."""
        with self._locked():
            return self._load()

    def committed(self) -> float:
        return sum(self.snapshot())

    def check(self, estimate: float) -> None:
        with self._locked():
            self._check(self._load(), _amount("estimate", estimate))

    def _check(self, state: tuple[float, float], amount: float) -> None:
        spent, held = state
        if spent + held + amount > self.cap + EPS:
            raise SpendCapError(
                f"${amount:.2f} plus ${spent:.2f} spent and ${held:.2f} reserved would cross the ${self.cap:.2f} cap"
            )

    def _ensure(self) -> None:
        if not self.mirror.exists():
            self._write_mirror(0.0)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch()

    def reserve(self, estimate: float) -> str:
        amount = _amount("estimate", estimate)
        with self._locked():
            self._check(self._load(), amount)
            self._ensure()
            rid = uuid.uuid4().hex
            self._append({"type": "reserve", "id": rid, "estimate": amount})
            return rid

    def settle(self, reservation: str, model: str, usage, cost: float) -> None:
        amount = _amount("cost", cost)
        with self._locked():
            spent, _ = self._load()
            self._ensure()
            self._write_mirror(spent + amount)
            self._write_hw(spent + amount)
            self._append(
                {
                    "type": "settle",
                    "id": reservation,
                    "model": model,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cost": amount,
                }
            )

    def uncertain(self) -> str:
        """Reason the budget is uncertain, or an empty string."""
        with self._locked():
            return UNCERTAIN.read_text(encoding="utf-8").strip() if UNCERTAIN.exists() else ""

    def mark_uncertain(self, reason: str) -> None:
        with self._locked():
            UNCERTAIN.parent.mkdir(parents=True, exist_ok=True)
            tmp = UNCERTAIN.with_name(UNCERTAIN.name + f".{os.getpid()}.tmp")
            tmp.write_text(reason + "\n", encoding="utf-8")
            os.replace(tmp, UNCERTAIN)
