# Bounded Claude tool loop behind KlearAgent: tools fill facts, one submit_answer ends the question.
import math
import time
from pathlib import Path

import anthropic

from klear.agent.answer import (
    SUBMIT_SPEC,
    build_answer,
    required_comparisons,
    required_facts,
    required_programs,
    stored_district,
    template_answer,
)
from klear.agent.config import (
    CLIENT_MAX_RETRIES,
    CLIENT_TIMEOUT_S,
    DEFAULT_MODEL,
    INPUT_CAP,
    MAX_CALLS,
    MAX_TOKENS,
    REQUEST_SETTINGS,
    RETRY_DELAYS_S,
    REVIEW_CALLS,
    REVIEW_INPUT_CAP,
    REVIEW_MAX_TOKENS,
)
from klear.agent.prompt import SYSTEM
from klear.agent.review import REVIEW_FIX, REVIEW_SPEC, REVIEW_SYSTEM, review_flags, review_messages
from klear.agent.tools import TOOL_SPECS, Session
from klear.config import load_secret
from klear.eval.agents import Usage
from klear.eval.contract import Answer, geo_display, render
from klear.eval.spend import _model, load_prices

SUBMIT = SUBMIT_SPEC["name"]
TOOLS = [*TOOL_SPECS, SUBMIT_SPEC]
EPHEMERAL = {"type": "ephemeral"}
SYSTEM_BLOCKS = [{"type": "text", "text": SYSTEM, "cache_control": EPHEMERAL}]
NUDGE = "Use the tools, then call submit_answer with your answer. Do not reply with plain text."
CUT = "Your reply hit the output limit before this call finished, so it was not run."
ONE_SUBMIT = "Only one submit_answer call per reply is evaluated; this one was skipped."
AUTO = {"type": "auto"}
FORCE_SUBMIT = {"type": "tool", "name": SUBMIT}
FORCE_REVIEW = {"type": "tool", "name": REVIEW_SPEC["name"]}
DECLINES = {"partisan": "decline_partisan", "individual": "decline_individual"}


def _result(uid: str, content: str, error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": uid, "content": content}
    return block | {"is_error": True} if error else block


def _text(body: str) -> list[dict]:
    return [{"type": "text", "text": body}]


def _marked(messages: list[dict]) -> list[dict]:
    """Copy of the messages with a cache breakpoint on the last block of the newest message."""
    *head, last = messages
    content = list(last["content"])
    content[-1] = {**content[-1], "cache_control": EPHEMERAL}
    return [*head, {**last, "content": content}]


def _input_tokens(u) -> int:
    write = getattr(u, "cache_creation_input_tokens", None) or 0
    return u.input_tokens + math.ceil(1.25 * write) + (getattr(u, "cache_read_input_tokens", None) or 0)


def _call_record(block) -> dict:
    """Name and input of a tool call, or the text of a text block."""
    if block.type == "tool_use":
        return {"tool": block.name, "input": block.input}
    return {"text": getattr(block, "text", "")}


class KlearAgent:
    name = "klear"

    def __init__(self, con, model: str = DEFAULT_MODEL, client=None, sleep=time.sleep, review: bool = False):
        if model not in REQUEST_SETTINGS:
            raise ValueError(f"unsupported model {model!r}, expected one of {sorted(REQUEST_SETTINGS)}")
        self.con, self.model, self._client, self._sleep = con, model, client, sleep
        self.reviews = REVIEW_CALLS if review else 0
        if review:
            self.name = "klear-review"

    def preflight(self) -> None:
        """Raise MissingSecretError when the live client would have no API key; call before reserving spend."""
        if self._client is None:
            load_secret("ANTHROPIC_API_KEY")

    @property
    def client(self):
        if self._client is None:
            self._client = anthropic.Anthropic(
                api_key=load_secret("ANTHROPIC_API_KEY"),
                max_retries=CLIENT_MAX_RETRIES,
                timeout=CLIENT_TIMEOUT_S,
            )
        return self._client

    def max_call_cost(self, model, question, prices: Path | dict) -> float:
        if model != self.model:
            raise ValueError(f"agent runs {self.model!r} but the cost bound was asked for {model!r}")
        rate = _model(prices if isinstance(prices, dict) else load_prices(prices), model)
        answer = MAX_CALLS * (INPUT_CAP * 1.25 * rate["input"] + MAX_TOKENS * rate["output"])
        review = self.reviews * (REVIEW_INPUT_CAP * rate["input"] + REVIEW_MAX_TOKENS * rate["output"])
        return (answer + review) / 1e6

    def _prompt(self, question: str, context: tuple[str, str] | None) -> str:
        if context is None:
            return question
        row = self.con.execute(
            "SELECT name FROM geography WHERE geo_type = ? AND geo_id = ?", list(context)
        ).fetchone()
        shown = geo_display(*context, row[0]) if row else f"{context[0]}:{context[1]}"
        return f"{question}\n\nSelected district: {shown} ({context[0]}:{context[1]})"

    @staticmethod
    def _fallback(session: Session, context, tried: dict) -> Answer:
        districts = [g for g in session.resolved if g[0] in ("house", "senate")]
        picked = stored_district(session, tried.get("resolved"))
        picked = picked if picked in session.resolved else None
        resolved = picked or context or (districts[0] if len(districts) == 1 else None)
        programs = tried.get("programs") if isinstance(tried.get("programs"), list) else []
        programs = [p for p in programs if isinstance(p, str)] + required_programs(session)
        if resolved is not None and (needed := required_facts(session, resolved)):
            session.run("get_facts", {"indicator_ids": needed, "geos": [":".join(resolved)]})
        if resolved is not None and (pairs := required_comparisons(session, resolved)):
            for place in dict.fromkeys(p for _, p in pairs):
                ids = [i for i, p in pairs if p == place]
                session.run("get_facts", {"indicator_ids": ids, "geos": [":".join(place)]})
        return template_answer(session, resolved, "none", context, programs)

    def _review(self, session: Session, answer: Answer, totals: list[int]) -> list[tuple[str, str]]:
        """Flags from one reviewer call on the rendered answer; none when the call fails or runs too long."""
        settings = REQUEST_SETTINGS[self.model]
        req = {
            "model": self.model,
            "system": REVIEW_SYSTEM,
            "tools": [REVIEW_SPEC],
            "tool_choice": FORCE_REVIEW if settings["force_submit"] else AUTO,
            "messages": review_messages(session.question, render(answer, self.con), answer.refusal),
            **settings["params"],
        }
        try:
            if self._call(self.client.messages.count_tokens, **req).input_tokens > REVIEW_INPUT_CAP:
                self.trace.append({"stop": "review_cap"})
                return []
            resp = self._call(self.client.messages.create, **req, max_tokens=REVIEW_MAX_TOKENS)
        except anthropic.APIError as e:
            self.trace.append({"review_error": type(e).__name__})
            return []
        totals[0] += _input_tokens(resp.usage)
        totals[1] += resp.usage.output_tokens
        call = next((b for b in resp.content or [] if b.type == "tool_use"), None)
        flags = review_flags(call.input if call else None, answer.refusal)
        self.trace.append({"stop": "review", "flags": [f for f, _ in flags]})
        return flags

    def _final(self, session: Session, answer: Answer, flags: list[tuple[str, str]]) -> Answer:
        """Answer kept after the last review: a decline for a partisan or personal flag, else as is."""
        decline = next((DECLINES[f] for f, _ in flags if f in DECLINES), None)
        return template_answer(session, answer.resolved, decline) if decline else answer

    def _call(self, fn, **req):
        """Call the free count_tokens fn, retrying only 429 and 529 errors after each RETRY_DELAYS_S wait."""
        for delay in RETRY_DELAYS_S:
            try:
                return fn(**req)
            except (anthropic.RateLimitError, anthropic.OverloadedError):
                self._sleep(delay)
        return fn(**req)

    def _run(self, session: Session, question: str, context) -> tuple[Answer, list[int]]:
        messages = [{"role": "user", "content": _text(self._prompt(question, context))}]
        totals = [0, 0]
        tried: dict = {}
        settings = REQUEST_SETTINGS[self.model]
        used = strikes = reviews = 0
        while used < MAX_CALLS:
            last = used == MAX_CALLS - 1
            req = {
                "model": self.model,
                "system": SYSTEM_BLOCKS,
                "tools": TOOLS,
                "tool_choice": FORCE_SUBMIT if last and settings["force_submit"] else AUTO,
                "messages": _marked(messages),
                **settings["params"],
            }
            if self._call(self.client.messages.count_tokens, **req).input_tokens > INPUT_CAP:
                self.trace.append({"stop": "input_cap"})
                break
            # Count every create attempt, retried or not, toward MAX_CALLS.
            used += 1
            try:
                resp = self.client.messages.create(**req, max_tokens=MAX_TOKENS)
            except (anthropic.RateLimitError, anthropic.OverloadedError) as e:
                self.trace.append({"error": type(e).__name__})
                if strikes == len(RETRY_DELAYS_S):
                    raise
                self._sleep(RETRY_DELAYS_S[strikes])
                strikes += 1
                continue
            strikes = 0
            totals[0] += _input_tokens(resp.usage)
            totals[1] += resp.usage.output_tokens
            step = {"stop": resp.stop_reason, "calls": [_call_record(b) for b in resp.content or []]}
            self.trace.append(step)
            if resp.stop_reason == "refusal":
                break
            if not resp.content:
                continue
            messages.append({"role": "assistant", "content": resp.content})
            uses = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason == "max_tokens" or not uses:
                reply = [_result(b.id, CUT, True) for b in uses]
                messages.append({"role": "user", "content": [*reply, *_text(NUDGE)]})
                continue
            submit = next((b for b in uses if b.name == SUBMIT), None)
            results = {
                b.id: _result(b.id, ONE_SUBMIT, True)
                if b.name == SUBMIT
                else _result(b.id, session.run(b.name, b.input))
                for b in uses
                if b is not submit
            }
            if submit is not None:
                tried = submit.input if isinstance(submit.input, dict) else {}
                answer, problems = build_answer(session, submit.input, context)
                if answer is not None:
                    flags = self._review(session, answer, totals) if reviews < self.reviews else []
                    reviews += 1
                    if not flags:
                        return answer, totals
                    if reviews >= self.reviews or used >= MAX_CALLS:
                        return self._final(session, answer, flags), totals
                    problems = [REVIEW_FIX[f].format(reason=r) for f, r in flags]
                step["problems"] = problems
                body = "Fix these problems and call submit_answer again:\n" + "\n".join(
                    f"- {p}" for p in problems
                )
                results[submit.id] = _result(submit.id, body, True)
            messages.append({"role": "user", "content": [results[b.id] for b in uses]})
        self.trace.append({"stop": "fallback"})
        answer = self._fallback(session, context, tried)
        flags = self._review(session, answer, totals) if reviews < self.reviews else []
        return self._final(session, answer, flags), totals

    def answer(self, golden_id: str, question: str, context: tuple[str, str] | None) -> tuple[Answer, Usage]:
        start = time.monotonic()
        self.trace: list[dict] = []
        session = Session(self.con)
        session.context = context
        session.question = question
        answer, (inp, out) = self._run(session, question, context)
        return answer, Usage(inp, out, time.monotonic() - start)
