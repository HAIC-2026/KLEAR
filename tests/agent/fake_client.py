# Scripted stand-in for the Anthropic client that records every request and never touches the network.
import copy
from types import SimpleNamespace

import httpx2


class FakeAPIError(Exception):
    pass


def api_error(cls, status: int):
    """A typed SDK status error as the real client raises it, built without any network."""
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(f"status {status}", response=httpx2.Response(status, request=request), body=None)


def tool(name: str, args: dict, uid: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=uid or f"tu_{name}", name=name, input=args)


def text(body: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=body)


def turn(*blocks, stop_reason: str | None = None, usage: dict | None = None) -> SimpleNamespace:
    """One assistant response; stop_reason defaults from the blocks."""
    if stop_reason is None:
        stop_reason = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    u = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    return SimpleNamespace(
        content=list(blocks), stop_reason=stop_reason, usage=SimpleNamespace(**(u | (usage or {})))
    )


class _Messages:
    def __init__(self, owner):
        self.owner = owner

    def count_tokens(self, **kw):
        o = self.owner
        o.counts.append(copy.deepcopy(kw))
        if o.count_failures:
            raise o.count_failures.pop(0)
        i = len(o.counts) - 1
        return SimpleNamespace(input_tokens=o.token_counts[i] if i < len(o.token_counts) else 100)

    def create(self, **kw):
        o = self.owner
        o.requests.append(copy.deepcopy(kw))
        if o.error is not None:
            raise o.error
        if o.failures:
            raise o.failures.pop(0)
        if not o.script:
            raise AssertionError("fake client script exhausted")
        step = o.script.pop(0)
        return step if isinstance(step, SimpleNamespace) and hasattr(step, "content") else turn(step)


class FakeClient:
    def __init__(
        self, script=(), token_counts=(), error: Exception | None = None, failures=(), count_failures=()
    ):
        self.script = list(script)
        self.token_counts = list(token_counts)
        self.error = error
        self.failures = list(failures)
        self.count_failures = list(count_failures)
        self.requests: list[dict] = []
        self.counts: list[dict] = []
        self.messages = _Messages(self)

    @property
    def calls(self) -> int:
        return len(self.requests)
