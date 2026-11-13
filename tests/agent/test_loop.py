# Tests for the bounded tool loop and KlearAgent, driven by a scripted fake client.
import json
from types import SimpleNamespace

import anthropic
import pytest
import yaml

from klear import config as klear_config
from klear.agent import config as agent_config
from klear.agent import loop
from klear.agent.answer import template_answer
from klear.agent.config import INPUT_CAP, MAX_CALLS, MAX_TOKENS, REQUEST_SETTINGS, RETRY_DELAYS_S
from klear.agent.loop import NUDGE, KlearAgent
from klear.agent.review import REVIEW_SPEC
from klear.agent.tools import Session
from klear.config import MissingSecretError
from klear.eval import spend
from tests.agent.fake_client import (
    CachingClient,
    FakeAPIError,
    FakeClient,
    api_error,
    rendered_blocks,
    text,
    tool,
    turn,
)

PRICES = {"models": {"claude-haiku-5-5": {"input": 0.10, "output": 0.50}}}
EPHEMERAL = {"type": "ephemeral"}
GOOD = "{geo:house:022} has {fact:f1} {label:f1} ({vintage:f1})."
FACTS = tool("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:022"]})


def ok(**kw):
    base = {
        "resolved": "house:022",
        "refusal": "none",
        "blocks": [GOOD],
        "facts": ["f1"],
        "programs": [],
        "caveats": [],
    }
    return base | kw


BAD = ok(facts=["f9"], blocks=["{geo:house:022} has {fact:f9}."])


def submit(args, uid="tu_submit"):
    return tool("submit_answer", args, uid)


def run(con, script, context=None, **kw):
    fake = FakeClient(script, **kw)
    ans, usage = KlearAgent(con, client=fake).answer("x", "How many seniors live in House 22?", context)
    return ans, usage, fake


def _results(request):
    """Tool results in the last user message of a request, by tool_use id."""
    content = request["messages"][-1]["content"]
    return {b["tool_use_id"]: b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"}


def test_happy_path_two_calls(small_con):
    ans, usage, fake = run(small_con, [FACTS, submit(ok())])
    assert ans.refusal == "none" and fake.calls == 2
    assert ans.facts[0].value == 1000.0 and ans.resolved == ("house", "022")
    assert usage.input_tokens == 20 and usage.output_tokens == 10 and usage.seconds >= 0
    assert '"f1"' in _results(fake.requests[1])["tu_get_facts"]["content"]


def test_request_shape(small_con):
    _, _, fake = run(small_con, [FACTS, submit(ok())])
    req = fake.requests[0]
    assert req["max_tokens"] == MAX_TOKENS and req["tool_choice"] == {"type": "auto"}
    assert req["system"][-1]["cache_control"] == {"type": "ephemeral"}
    names = [t["name"] for t in req["tools"]]
    assert "submit_answer" in names and "get_facts" in names
    assert req["messages"][0]["content"][0]["text"] == "How many seniors live in House 22?"
    for count, sent in zip(fake.counts, fake.requests, strict=True):
        assert sent["thinking"] == count["thinking"] == {"type": "disabled"}
        assert "output_config" not in sent
        assert set(count) == {"model", "system", "tools", "tool_choice", "messages", "thinking"}
        assert all(count[k] == sent[k] for k in count)
    assert len(fake.counts) == fake.calls == 2


def _marks(req):
    return [
        (i, j)
        for i, m in enumerate(req["messages"])
        if isinstance(m["content"], list)
        for j, b in enumerate(m["content"])
        if isinstance(b, dict) and "cache_control" in b
    ]


def test_cache_breakpoint_on_newest_user_message(small_con):
    _, _, fake = run(small_con, [tool("search_indicators", {"words": "x"}), FACTS, submit(ok())])
    for req in fake.requests:
        last = len(req["messages"]) - 1
        assert req["messages"][last]["role"] == "user"
        assert _marks(req) == [(last, len(req["messages"][last]["content"]) - 1)]
        assert req["messages"][last]["content"][-1]["cache_control"] == {"type": "ephemeral"}
        assert sum("cache_control" in b for b in req["system"]) == 1
    assert len(fake.requests) == 3 and len(fake.requests[2]["messages"]) == 5


def test_context_line_in_user_message(small_con):
    _, _, fake = run(small_con, [FACTS, submit(ok())], context=("house", "022"))
    first = fake.requests[0]["messages"][0]["content"][0]["text"]
    assert first.endswith("Selected district: House District 22 (house:022)")


def test_fallback_prefers_resolved_district_over_context(small_con):
    place = tool("resolve_place", {"text": "House 23"})
    ans, _, _ = run(
        small_con, [place] + [submit(BAD | {"resolved": "house:023"})] * 5, context=("house", "022")
    )
    assert ans.resolved == ("house", "023")
    ans, _, _ = run(
        small_con, [submit(BAD | {"resolved": "house:023"})] * MAX_CALLS, context=("house", "022")
    )
    assert ans.resolved == ("house", "022")


def test_rejected_submits_retry_until_calls_run_out(small_con):
    ctx = ("house", "022")
    ans, _, fake = run(small_con, [submit(BAD)] * MAX_CALLS, context=ctx)
    s = Session(small_con)
    s.context = ctx
    assert ans == template_answer(s, ctx, "none", ctx) and fake.calls == MAX_CALLS
    err = _results(fake.requests[1])["tu_submit"]
    assert err["is_error"] is True and "f9" in err["content"]


def test_fallback_keeps_last_district_and_programs_and_traces(small_con):
    bad = BAD | {"programs": ["eitc_federal_ty2025"]}
    place = tool("resolve_place", {"text": "House 22"})
    agent = KlearAgent(small_con, client=FakeClient([place, FACTS] + [submit(bad)] * (MAX_CALLS - 2)))
    ans, _ = agent.answer("x", "q", None)
    assert ans.resolved == ("house", "022") and ans.programs == ["eitc_federal_ty2025"]
    assert agent.trace[1]["calls"][0]["tool"] == "get_facts"
    assert any("f9" in p for p in agent.trace[2]["problems"])
    assert agent.trace[-1] == {"stop": "fallback"} and len(agent.trace) == MAX_CALLS + 1


def test_fallback_fetches_facts_the_question_asks_for(small_con, monkeypatch):
    monkeypatch.setattr(agent_config, "REQUIRED_FACTS", ((("profile",), ("sites_lihtc",)),))
    place = tool("resolve_place", {"text": "House 22"})
    agent = KlearAgent(small_con, client=FakeClient([place, FACTS] + [submit(BAD)] * (MAX_CALLS - 2)))
    ans, _ = agent.answer("x", "Profile of House 22", None)
    assert agent.trace[-1] == {"stop": "fallback"}
    assert {f.indicator_id for f in ans.facts} == {"pop_65_plus", "sites_lihtc"}


def test_fallback_adds_required_comparisons_and_programs(small_con, monkeypatch):
    monkeypatch.setattr(agent_config, "REQUIRED_FACTS", ((("fixed",), ("pop_65_plus",)),))
    monkeypatch.setattr(agent_config, "REQUIRED_COMPARISONS", ((("fixed",), ("pop_65_plus",)),))
    monkeypatch.setattr(agent_config, "REQUIRED_PROGRAMS", ((("fixed",), ("snap_fy2027",)),))
    place = tool("resolve_place", {"text": "House 22"})
    agent = KlearAgent(small_con, client=FakeClient([place] + [submit(BAD)] * (MAX_CALLS - 1)))
    ans, _ = agent.answer("x", "Fixed incomes in House 22", None)
    assert agent.trace[-1] == {"stop": "fallback"}
    assert {(f.indicator_id, f.geo_type, f.geo_id) for f in ans.facts} == {
        ("pop_65_plus", "house", "022"),
        ("pop_65_plus", "county", "15003"),
        ("pop_65_plus", "state", "15"),
    }
    assert ans.programs == ["snap_fy2027"] and "SNAP" in ans.blocks[-1]


def test_bad_then_good_submit(small_con):
    ans, _, fake = run(small_con, [FACTS, submit(BAD), submit(ok())])
    assert ans.facts[0].ref == "f1" and fake.calls == 3


def test_never_more_than_max_calls(small_con):
    script = [tool("search_indicators", {"words": "x"})] * 20
    ans, _, fake = run(small_con, script)
    assert fake.calls == MAX_CALLS
    assert ans.refusal == "none" and ans.resolved is None


def test_exhausted_calls_template_uses_single_resolved_district(small_con):
    place = tool("resolve_place", {"text": "House 23"})
    ans, _, fake = run(small_con, [place] + [tool("search_indicators", {"words": "x"})] * 10)
    assert fake.calls == MAX_CALLS and ans.resolved == ("house", "023")


def test_input_cap_stops_before_call(small_con):
    ans, usage, fake = run(small_con, [submit(ok())], token_counts=[INPUT_CAP + 1])
    assert fake.calls == 0 and ans.refusal == "none" and usage.input_tokens == 0


def test_input_cap_mid_loop(small_con):
    ans, _, fake = run(
        small_con, [FACTS, submit(ok())], context=("house", "022"), token_counts=[100, INPUT_CAP + 1]
    )
    assert fake.calls == 1 and [f.ref for f in ans.facts] == ["f1"]


def test_api_error_propagates(small_con):
    fake = FakeClient(error=FakeAPIError("boom"))
    with pytest.raises(FakeAPIError):
        KlearAgent(small_con, client=fake).answer("x", "q", None)
    assert fake.calls == 1


def test_count_tokens_error_propagates(small_con):
    fake = FakeClient([submit(ok())])

    def boom(**kw):
        raise FakeAPIError("count")

    fake.messages.count_tokens = boom
    with pytest.raises(FakeAPIError):
        KlearAgent(small_con, client=fake).answer("x", "q", None)
    assert fake.calls == 0


def test_usage_keeps_cache_writes_and_reads_apart(small_con):
    u = {"input_tokens": 100, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 50}
    _, usage, _ = run(small_con, [turn(FACTS, usage=u), turn(submit(ok()), usage=u | {"output_tokens": 7})])
    assert (usage.input_tokens, usage.output_tokens) == (200, 12)
    assert (usage.cache_write_tokens, usage.cache_read_tokens) == (2000, 100)


def test_usage_tolerates_missing_cache_fields(small_con):
    u = {"cache_creation_input_tokens": None, "cache_read_input_tokens": None}
    _, usage, _ = run(small_con, [turn(FACTS, usage=u), submit(ok())])
    assert usage.input_tokens == 20 and usage.cache_write_tokens == usage.cache_read_tokens == 0


def test_cache_breakpoints_on_tools_system_and_newest_message(small_con):
    _, _, fake = run(small_con, [FACTS, submit(ok())])
    for req in fake.requests:
        assert [t.get("cache_control") for t in req["tools"]] == [None] * (len(req["tools"]) - 1) + [
            EPHEMERAL
        ]
        assert [b.get("cache_control") for b in req["system"]] == [EPHEMERAL]
        assert req["messages"][-1]["content"][-1]["cache_control"] == EPHEMERAL
        marks = sum(
            "cache_control" in b for m in req["messages"] for b in m["content"] if isinstance(b, dict)
        )
        assert marks == 1
    # The module-level tool list keeps its marker on the last tool only, and count_tokens sees the same request.
    assert loop.TOOLS[-1]["name"] == "submit_answer" and "cache_control" not in loop.SUBMIT_SPEC
    assert fake.counts[0]["tools"] == fake.requests[0]["tools"]


def test_cached_prefix_is_byte_identical_across_calls_and_questions(small_con):
    fake = FakeClient([FACTS, submit(ok()), FACTS, submit(ok())])
    agent = KlearAgent(small_con, client=fake)
    agent.answer("a", "How many seniors live in House 22?", None)
    agent.answer("b", "How many seniors live in House District 22?", None)
    prefixes = {json.dumps([r["tools"], r["system"]], sort_keys=True) for r in fake.requests}
    assert len(fake.requests) == 4 and len(prefixes) == 1


def test_second_question_reads_tools_and_system_from_the_cache(small_con):
    fake = CachingClient([FACTS, submit(ok()), FACTS, submit(ok())])
    agent = KlearAgent(small_con, client=fake)
    _, first = agent.answer("a", "How many seniors live in House 22?", None)
    _, second = agent.answer("b", "How many seniors live in House District 22?", None)
    prefix = len("".join(t for t, _ in rendered_blocks(fake.requests[0])[: len(loop.TOOLS) + 1]))
    # The first call writes everything; the next call in the question reads it.
    assert (
        fake.usages[0]["cache_read_input_tokens"] == 0 and fake.usages[1]["cache_read_input_tokens"] > prefix
    )
    # The second question's first call reads the tools and system prompt the first question wrote.
    assert fake.usages[2]["cache_read_input_tokens"] == prefix
    assert second.cache_read_tokens > first.cache_read_tokens > 0
    assert second.cache_write_tokens < first.cache_write_tokens
    prices = spend.load_prices()
    assert spend.request_cost(agent.model, second) < spend.request_cost(agent.model, first)
    rate = spend.rates(prices, agent.model)
    uncached = (second.input_tokens + second.cache_write_tokens + second.cache_read_tokens) * rate["input"]
    assert spend.request_cost(agent.model, second) < (uncached + second.output_tokens * rate["output"]) / 1e6


def test_text_only_reply_gets_nudge(small_con):
    ans, _, fake = run(small_con, [turn(text("Thinking about it.")), FACTS, submit(ok())])
    assert fake.calls == 3 and ans.facts
    nudge = fake.requests[1]["messages"][-1]
    assert nudge["role"] == "user" and [b["text"] for b in nudge["content"]] == [NUDGE]


def test_max_tokens_cut_tool_call_gets_error_and_nudge(small_con):
    cut = turn(tool("get_facts", {}, "tu_cut"), stop_reason="max_tokens")
    ans, _, fake = run(small_con, [cut, FACTS, submit(ok())])
    assert fake.calls == 3 and ans.facts
    last = fake.requests[1]["messages"][-1]["content"]
    assert _results(fake.requests[1])["tu_cut"]["is_error"] is True
    assert last[-1]["type"] == "text" and last[-1]["text"] == NUDGE


def test_every_tool_use_gets_a_result_and_submit_runs_after_tools(small_con):
    both = turn(FACTS, submit(ok()))
    ans, _, fake = run(small_con, [both])
    assert fake.calls == 1 and ans.facts[0].ref == "f1"


def test_parallel_tools_all_answered(small_con):
    two = turn(
        tool("search_indicators", {"words": "kupuna"}, "a"), tool("resolve_place", {"text": "Kailua"}, "b")
    )
    _, _, fake = run(small_con, [two, FACTS, submit(ok())])
    assert set(_results(fake.requests[1])) == {"a", "b"}


def test_second_submit_in_one_turn_is_rejected(small_con):
    dup = turn(FACTS, submit(BAD, "s1"), submit(ok(), "s2"))
    ans, _, fake = run(small_con, [dup, submit(ok())])
    res = _results(fake.requests[1])
    assert set(res) == {"tu_get_facts", "s1", "s2"} and res["s2"]["is_error"] is True
    assert fake.calls == 2 and ans.facts[0].ref == "f1"


def test_max_call_cost_matches_formula(small_con):
    expected = 6 * (30000 * 1.25 * 0.10 + 1500 * 0.50) / 1e6
    assert KlearAgent(small_con).max_call_cost("claude-haiku-5-5", "q", PRICES) == pytest.approx(expected)


def test_max_call_cost_reads_prices_file(small_con, tmp_path):
    path = tmp_path / "prices.yaml"
    path.write_text(yaml.safe_dump(PRICES), encoding="utf-8")
    expected = 6 * (30000 * 1.25 * 0.10 + 1500 * 0.50) / 1e6
    assert KlearAgent(small_con).max_call_cost("claude-haiku-5-5", "q", path) == pytest.approx(expected)


def test_max_call_cost_real_prices_per_model(small_con):
    want = {"claude-haiku-5-5": 0.027, "claude-sonnet-5-5": 0.540, "claude-opus-5-5": 1.080}
    for model, cost in want.items():
        assert KlearAgent(small_con, model=model).max_call_cost(model, "q", spend.PRICES) == pytest.approx(
            cost
        )


def test_max_call_cost_refuses_other_model(small_con):
    with pytest.raises(ValueError, match="claude-opus-5-5"):
        KlearAgent(small_con).max_call_cost("claude-opus-5-5", "q", PRICES)


def test_max_call_cost_model_missing_from_prices(small_con):
    with pytest.raises(KeyError):
        KlearAgent(small_con).max_call_cost("claude-haiku-5-5", "q", {"models": {}})


def test_unknown_model_refused_at_construction(small_con):
    with pytest.raises(ValueError, match="nope"):
        KlearAgent(small_con, model="nope")


@pytest.mark.parametrize(
    ("model", "settings"),
    [
        ("claude-haiku-5-5", {"thinking": {"type": "disabled"}}),
        ("claude-sonnet-5-5", {"thinking": {"type": "between_tools"}, "output_config": {"effort": "high"}}),
        ("claude-opus-5-5", {"output_config": {"effort": "low"}}),
    ],
)
def test_request_settings_per_model(small_con, model, settings):
    fake = FakeClient([FACTS, submit(ok())])
    KlearAgent(small_con, model=model, client=fake).answer("x", "q", None)
    extra = {"thinking", "output_config"}
    for count, sent in zip(fake.counts, fake.requests, strict=True):
        assert sent["model"] == count["model"] == model
        assert {k: sent[k] for k in extra & set(sent)} == settings
        assert {k: count[k] for k in extra & set(count)} == settings
    assert fake.calls == 2


def test_opus_thinking_blocks_passed_back_unchanged(small_con):
    thought = SimpleNamespace(type="thinking", thinking="", signature="sig-123")
    fake = FakeClient([turn(thought, FACTS), submit(ok())])
    ans, _ = KlearAgent(small_con, model="claude-opus-5-5", client=fake).answer("x", "q", None)
    sent = fake.requests[1]["messages"][1]
    assert sent["role"] == "assistant" and sent["content"][0] == thought
    assert [b.type for b in sent["content"]] == ["thinking", "tool_use"]
    assert ans.facts[0].ref == "f1"


def test_no_key_needed_to_construct(small_con, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(klear_config, "ENV_FILE", tmp_path / "missing.env")
    agent = KlearAgent(small_con)
    assert agent.name == "klear" and agent.model == "claude-haiku-5-5"
    with pytest.raises(MissingSecretError):
        _ = agent.client


def test_default_client_settings(small_con, monkeypatch):
    made, asked = [], []
    monkeypatch.setattr(loop.anthropic, "Anthropic", lambda **kw: made.append(kw) or "client")
    monkeypatch.setattr(loop, "load_secret", lambda name: asked.append(name) or "key-from-secret")
    agent = KlearAgent(small_con)
    assert agent.client == "client" and agent.client == "client"
    assert asked == ["ANTHROPIC_API_KEY"]
    assert made == [{"api_key": "key-from-secret", "max_retries": 0, "timeout": 60}]


def test_last_call_forces_submit_on_haiku_only(small_con):
    script = [tool("search_indicators", {"words": ["x"]})] * (MAX_CALLS - 1) + [submit(ok())]
    ans, _, fake = run(small_con, [FACTS, *script[1:]])
    choices = [r["tool_choice"] for r in fake.requests]
    assert choices == [{"type": "auto"}] * (MAX_CALLS - 1) + [{"type": "tool", "name": "submit_answer"}]
    assert [c["tool_choice"] for c in fake.counts] == choices
    assert ans.facts[0].ref == "f1" and fake.calls == MAX_CALLS


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "claude-opus-5-5"])
def test_last_call_stays_auto_where_forcing_is_a_400(small_con, model):
    fake = FakeClient([tool("search_indicators", {"words": ["x"]})] * MAX_CALLS)
    KlearAgent(small_con, model=model, client=fake).answer("x", "q", None)
    assert fake.calls == MAX_CALLS and all(r["tool_choice"] == {"type": "auto"} for r in fake.requests)


def test_only_haiku_runs_without_thinking_and_may_force():
    forced = {m for m, s in REQUEST_SETTINGS.items() if s["force_submit"]}
    assert forced == {"claude-haiku-5-5"}
    assert REQUEST_SETTINGS["claude-haiku-5-5"]["params"]["thinking"] == {"type": "disabled"}


def _agent(con, fake, waits):
    return KlearAgent(con, client=fake, sleep=waits.append)


@pytest.mark.parametrize(
    ("cls", "status"), [(anthropic.RateLimitError, 429), (anthropic.OverloadedError, 529)]
)
def test_rate_limit_and_overload_are_retried_with_backoff(small_con, cls, status):
    waits = []
    fake = FakeClient([FACTS, submit(ok())], failures=[api_error(cls, status)] * 2)
    ans, usage = _agent(small_con, fake, waits).answer("x", "q", None)
    assert waits == list(RETRY_DELAYS_S[:2]) and fake.calls == 4 and ans.facts[0].ref == "f1"
    assert usage.input_tokens == 20


def test_retries_count_toward_max_calls(small_con):
    waits = []
    script = [tool("search_indicators", {"words": ["x"]})] * MAX_CALLS
    fake = FakeClient(script, failures=[api_error(anthropic.RateLimitError, 429)] * 3)
    ans, _ = _agent(small_con, fake, waits).answer("x", "q", None)
    assert fake.calls == MAX_CALLS and len(fake.script) == 3 and waits == list(RETRY_DELAYS_S)
    assert ans.facts == []


def test_retry_exhaustion_propagates(small_con):
    waits = []
    fake = FakeClient([submit(ok())], failures=[api_error(anthropic.OverloadedError, 529)] * 4)
    with pytest.raises(anthropic.OverloadedError):
        _agent(small_con, fake, waits).answer("x", "q", None)
    assert fake.calls == 1 + len(RETRY_DELAYS_S) and waits == list(RETRY_DELAYS_S)


@pytest.mark.parametrize(
    "error",
    [
        api_error(anthropic.InternalServerError, 500),
        api_error(anthropic.ServiceUnavailableError, 503),
        api_error(anthropic.BadRequestError, 400),
        anthropic.APITimeoutError(request=None),
        anthropic.APIConnectionError(request=None),
    ],
)
def test_other_errors_are_not_retried(small_con, error):
    waits = []
    fake = FakeClient([submit(ok())], failures=[error])
    with pytest.raises(type(error)):
        _agent(small_con, fake, waits).answer("x", "q", None)
    assert fake.calls == 1 and waits == []


def test_count_tokens_is_retried_on_rate_limit(small_con):
    waits = []
    fake = FakeClient([FACTS, submit(ok())], count_failures=[api_error(anthropic.RateLimitError, 429)])
    ans, _ = _agent(small_con, fake, waits).answer("x", "q", None)
    assert waits == [RETRY_DELAYS_S[0]] and len(fake.counts) == 3 and fake.calls == 2
    assert ans.facts[0].ref == "f1"


@pytest.mark.parametrize(
    "refused",
    [
        turn(stop_reason="refusal"),
        turn(text("I cannot"), tool("get_facts", {}, "tu_cut"), stop_reason="refusal"),
    ],
)
def test_refusal_stops_without_resending_or_running_tools(small_con, refused):
    ctx = ("house", "022")
    ans, usage, fake = run(small_con, [refused, submit(ok())], context=ctx)
    s = Session(small_con)
    s.context = ctx
    assert fake.calls == 1 and ans == template_answer(s, ctx, "none", ctx)
    assert usage.input_tokens == 10 and usage.output_tokens == 5


def test_preflight_fails_without_key(small_con, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(klear_config, "ENV_FILE", tmp_path / "missing.env")
    with pytest.raises(MissingSecretError):
        KlearAgent(small_con).preflight()


def test_preflight_skips_key_with_injected_client(small_con, monkeypatch):
    monkeypatch.setattr(loop, "load_secret", lambda name: pytest.fail("key read"))
    KlearAgent(small_con, client=FakeClient()).preflight()


def review(partisan=False, individual=False, answers=True, reason="none"):
    args = {"partisan": partisan, "individual": individual, "answers_question": answers, "reason": reason}
    return tool("review", args, "tu_review")


DECLINE = ok(
    refusal="decline_partisan", facts=[], blocks=["KLEAR shares neutral public data about {geo:house:022}."]
)


def reviewed(con, script):
    fake = FakeClient(script)
    agent = KlearAgent(con, client=fake, review=True)
    ans, usage = agent.answer("x", "Talking points to flip House 22 for my party?", None)
    return ans, usage, fake, agent


def test_reviewer_flag_sends_answer_back_then_accepts_fix(small_con):
    ans, usage, fake, agent = reviewed(
        small_con,
        [FACTS, submit(ok()), review(partisan=True, reason="campaign help"), submit(DECLINE), review()],
    )
    assert ans.refusal == "decline_partisan" and fake.calls == 5 and usage.input_tokens == 50
    req = fake.requests[2]
    assert req["tools"] == [REVIEW_SPEC] and req["tool_choice"] == {"type": "tool", "name": "review"}
    assert "Talking points" in req["messages"][0]["content"][0]["text"]
    assert "campaign help" in _results(fake.requests[3])["tu_submit"]["content"]
    assert [s.get("flags") for s in agent.trace if s.get("stop") == "review"] == [["partisan"], []]


def test_second_partisan_flag_becomes_a_template_decline(small_con):
    ans, _, fake, _ = reviewed(
        small_con, [FACTS, submit(ok()), review(partisan=True), submit(ok()), review(partisan=True)]
    )
    assert ans.refusal == "decline_partisan" and ans.facts == [] and ans.resolved == ("house", "022")
    assert fake.calls == 5


def test_second_off_target_flag_keeps_the_answer(small_con):
    ans, _, _, _ = reviewed(
        small_con, [FACTS, submit(ok()), review(answers=False), submit(ok()), review(answers=False)]
    )
    assert ans.refusal == "none" and ans.facts[0].ref == "f1"


def test_reviewer_without_a_verdict_passes(small_con):
    ans, _, fake, _ = reviewed(small_con, [FACTS, submit(ok()), text("no tool call")])
    assert ans.refusal == "none" and fake.calls == 3


def test_max_call_cost_adds_review_calls(small_con):
    answer = 6 * (30000 * 1.25 * 0.10 + 1500 * 0.50)
    review_cost = 2 * (6000 * 0.10 + 300 * 0.50)
    got = KlearAgent(small_con, review=True).max_call_cost("claude-haiku-5-5", "q", PRICES)
    assert got == pytest.approx((answer + review_cost) / 1e6)
