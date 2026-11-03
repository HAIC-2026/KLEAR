# Shared result type for KLEAR validation checks.
from dataclasses import dataclass, field


@dataclass
class CheckResult:
    name: str
    passed: bool
    gate: bool
    n_compared: int
    n_mismatch: int
    details: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
