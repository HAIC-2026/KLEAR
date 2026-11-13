# Scripted stand-in for the Anthropic client that records every request and never touches the network.
import copy
import json
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
        resp = step if isinstance(step, SimpleNamespace) and hasattr(step, "content") else turn(step)
        if o.prompt_cache is not None:
            resp.usage = SimpleNamespace(**(vars(resp.usage) | o.bill(kw)))
            o.usages.append(vars(resp.usage))
        return resp


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
        # Prompt cache stand-in: off unless a CachingClient sets it.
        self.prompt_cache: set[str] | None = None
        self.usages: list[dict] = []

    @property
    def calls(self) -> int:
        return len(self.requests)


def rendered_blocks(req: dict) -> list[tuple[str, bool]]:
    """Request blocks in the order the API renders them (tools, system, message content), each as its text
    without the cache marker and whether it carried one."""
    raw = [*req.get("tools", []), *req.get("system", [])]
    raw += [b if isinstance(b, dict) else vars(b) for m in req["messages"] for b in m["content"]]
    return [
        (json.dumps({k: v for k, v in b.items() if k != "cache_control"}, sort_keys=True, default=vars),
         "cache_control" in b)
        for b in raw
    ]  # fmt: skip


class CachingClient(FakeClient):
    """Fake that bills input like the prompt cache, one token per character: each breakpoint writes the prefix
    ending there, and a request reads the longest written prefix that ends at or before its last breakpoint (the
    API looks back up to 20 blocks), then writes the rest up to the last breakpoint."""

    def __init__(self, script=(), **kw):
        super().__init__(script, **kw)
        self.prompt_cache = set()

    def bill(self, req: dict) -> dict:
        blocks = rendered_blocks(req)
        texts = [t for t, _ in blocks]
        marks = [i for i, (_, marked) in enumerate(blocks) if marked]
        prefixes = ["".join(texts[: i + 1]) for i in marks]
        seen = ["".join(texts[: i + 1]) for i in range(max(marks, default=-1) + 1)]
        read = max((len(p) for p in seen if p in self.prompt_cache), default=0)
        cached = len(prefixes[-1]) if prefixes else 0
        self.prompt_cache.update(prefixes)
        return {
            "input_tokens": len("".join(texts)) - cached,
            "cache_creation_input_tokens": cached - read,
            "cache_read_input_tokens": read,
        }
