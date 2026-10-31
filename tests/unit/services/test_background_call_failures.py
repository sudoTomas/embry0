"""Background calls outside a job when something goes wrong (#287 review): never past the caps, never past the writers.

- Usage a provider sends badly (a string count, an overflowing one, a negative or boolean one) is read as no
  usage: the call is still recorded, with no tokens, a warning says so, and nothing raises past the writers.
- A body that is not JSON is recorded the same way and is a ``ValueError``, which the writers handle.
- A call whose spend cannot be stored fails closed: the error is logged, and the feature's hosted calls are
  refused until the row is stored. A local model's calls go on.
- A model with no price says so at warning level once an hour, not on every tick.

``embry0.services.background_models`` is imported inside each test, so each fails with its own message on a
tree that has none.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from embry0.services.proposals import Proposal
from tests.fixtures.leads import brief_reader

BRIEF = {"title": "Stop retrying a saved email", "problem": "It retries.", "fix": "Stop it.", "size": "small"}


def _module() -> Any:
    try:
        from embry0.services import background_models
    except ImportError:
        pytest.fail("embry0.services.background_models makes every background call outside a job (#164)")
    return background_models


class Resolver:
    async def resolve_llm(self, repo: str, provider: str) -> Any:
        return SimpleNamespace(secret="k", metadata={"base_url": "http://models.test/v1"})


class Spend:
    """The spend table: ``failing`` makes every insert raise, as a database that refuses it would."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.failing = False

    async def record(self, **row: Any) -> None:
        if self.failing:
            raise OSError("the database refused the insert")
        self.rows.append(row)


class Gate:
    def __init__(self) -> None:
        self.checks = 0

    async def check(self, **kwargs: Any) -> Any:
        self.checks += 1
        return SimpleNamespace(allowed=True, limit="", message="")


def _raw_client(sent: list[str], body: str) -> httpx.AsyncClient:
    """A provider answering ``body`` as written, so it can hold what JSON encoders refuse to write (1e999)."""

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["model"])
        return httpx.Response(200, content=body.encode(), headers={"content-type": "application/json"})

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


def _body(usage: str | None) -> str:
    text = json.dumps({"choices": [{"message": {"content": json.dumps(BRIEF)}}]})
    return text if usage is None else text[:-1] + ', "usage": ' + usage + "}"


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openai,custom")


async def _ask(calls: Any, model: str, client: httpx.AsyncClient, feature: str = "job_brief") -> str:
    """What one call came to, as words: ``answered``, or the exception it raised."""
    endpoint = await calls.endpoint(feature, model, "acme/widgets")
    assert endpoint is not None, f"{model} can be called"
    try:
        await calls.ask(feature, endpoint, client, repo="acme/widgets", system="s", user="u", schema={}, name="b")
    except Exception as exc:  # noqa: BLE001 - the outcome is what the test reads
        return f"{type(exc).__name__}: {exc}"
    return "answered"


@pytest.mark.parametrize(
    "usage",
    [
        '{"prompt_tokens": 1e999, "completion_tokens": 1}',
        '{"prompt_tokens": "lots", "completion_tokens": 1}',
        '{"prompt_tokens": 10, "completion_tokens": -5}',
        '{"prompt_tokens": true, "completion_tokens": 1}',
        '{"prompt_tokens": 10, "completion_tokens": 99999999999999}',
        '{"prompt_tokens": 10, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": "x"}}',
    ],
    ids=["overflow", "string", "negative", "boolean", "past-any-count", "string-cached"],
)
async def test_bad_usage_is_read_as_none_and_the_call_is_still_recorded(providers: None, usage: str) -> None:
    module = _module()
    spend = Spend()
    calls = module.BackgroundCalls(resolver=Resolver(), spend=spend, gate=Gate())
    sent: list[str] = []
    async with _raw_client(sent, _body(usage)) as client:
        with capture_logs() as logs:
            outcome = await _ask(calls, "gpt-6-luna", client)
    assert outcome == "answered", f"usage a provider sent badly must never raise past the call: {outcome}"
    assert [(r["input_tokens"], r["output_tokens"], r["cache_read_tokens"], r["cost_usd"]) for r in spend.rows] == [
        (0, 0, 0, 0.0)
    ], f"the call is recorded, with no tokens: {spend.rows}"
    warned = [e for e in logs if e["event"] == "background_model_usage_missing"]
    assert [(e["log_level"], e["problem"]) for e in warned] == [("warning", "malformed")], logs


async def test_a_cached_count_past_the_prompt_is_held_to_it(providers: None) -> None:
    module = _module()
    spend = Spend()
    calls = module.BackgroundCalls(resolver=Resolver(), spend=spend, gate=Gate())
    usage = '{"prompt_tokens": 10, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 50}}'
    async with _raw_client([], _body(usage)) as client:
        assert await _ask(calls, "gpt-6-luna", client) == "answered"
    assert [(r["input_tokens"], r["cache_read_tokens"]) for r in spend.rows] == [(0, 10)], spend.rows


async def test_bad_usage_never_raises_past_a_writer(providers: None) -> None:
    module = _module()
    from embry0.services.proposal_briefs import ProposalBriefWriter

    rows: dict[str, Any] = {}

    class Briefs:
        async def all(self) -> dict[str, Any]:
            return dict(rows)

        async def save(self, pid: str, digest: str, brief: Any, *, model: str, prompt_hash: str = "") -> None:
            rows[pid] = {"input_hash": digest}

        async def prune(self, keep: set[str]) -> int:
            return 0

    async def feed() -> list[Proposal]:
        return [
            Proposal(
                id="case:hc-1",
                kind="case",
                repo="acme/widgets",
                title="Problem",
                summary="…",
                details="",
                action="Run the fix pipeline",
                created_at=datetime(2026, 10, 30, tzinfo=UTC),
                extra={},
            )
        ]

    async def models() -> Any:
        return lambda repo: "gpt-6-luna"

    spend = Spend()
    writer = ProposalBriefWriter(
        proposals=feed,
        settings=brief_reader(models),
        briefs=Briefs(),
        calls=module.BackgroundCalls(resolver=Resolver(), spend=spend, gate=Gate()),
        client=_raw_client([], _body('{"prompt_tokens": 1e999, "completion_tokens": 1}')),
    )
    try:
        written = await writer.tick()
    except Exception as exc:  # noqa: BLE001 - the outcome is what the test reads
        pytest.fail(f"an overflowing usage count raised past the writer: {type(exc).__name__}: {exc}")
    assert written == ["case:hc-1"] and len(spend.rows) == 1, "the brief is written and the call recorded"


async def test_a_body_that_is_not_json_is_recorded_and_refused_as_an_answer(providers: None) -> None:
    module = _module()
    spend = Spend()
    calls = module.BackgroundCalls(resolver=Resolver(), spend=spend, gate=Gate())
    async with _raw_client([], "<html>bad gateway</html>") as client:
        with capture_logs() as logs:
            outcome = await _ask(calls, "gpt-6-luna", client)
    assert outcome.startswith("ValueError"), f"the writers handle a ValueError, and only that: {outcome}"
    assert len(spend.rows) == 1, "the call was made, so it is recorded"
    assert [e["problem"] for e in logs if e["event"] == "background_model_usage_missing"] == ["unreadable"]


async def test_a_spend_that_cannot_be_stored_refuses_the_feature_s_hosted_calls_until_it_is(providers: None) -> None:
    module = _module()
    spend, gate = Spend(), Gate()
    calls = module.BackgroundCalls(resolver=Resolver(), spend=spend, gate=gate)
    usage = '{"prompt_tokens": 1000, "completion_tokens": 100}'
    sent: list[str] = []
    async with _raw_client(sent, _body(usage)) as client:
        spend.failing = True
        with capture_logs() as logs:
            first = await _ask(calls, "gpt-6-luna", client)
            second = await _ask(calls, "gpt-6-luna", client)
            other = await _ask(calls, "gpt-6-luna", client, feature="proposal_brief")
            local = await _ask(calls, "custom/qwen", client)
        assert first == "answered", f"the call was made; only its record failed: {first}"
        assert second.startswith("BackgroundCallRefused"), (
            f"the feature's next hosted call must wait until its spend is stored: {second}"
        )
        assert other == "answered", "another feature's calls are not held by this one's record"
        assert local == "answered", "a local model bills nothing: its calls go on"
        assert sent == ["gpt-6-luna", "gpt-6-luna", "qwen"], f"nothing was sent for the refused call: {sent}"
        errors = [e for e in logs if e["event"] == "background_spend_record_failed"]
        assert errors and errors[0]["log_level"] == "error", logs
        refused = [e for e in logs if e["event"] == "background_model_refused"]
        assert [e["limit"] for e in refused] == ["spend_unrecorded"], refused

        spend.failing = False
        again = await _ask(calls, "gpt-6-luna", client)
        later = await _ask(calls, "gpt-6-luna", client, feature="proposal_brief")
    assert (again, later) == ("answered", "answered"), f"once the spend can be stored, the calls go on: {again}"
    assert [(r["feature"], r["model"]) for r in spend.rows] == [
        ("job_brief", "gpt-6-luna"),
        ("job_brief", "custom/qwen"),
        ("job_brief", "gpt-6-luna"),
        ("proposal_brief", "gpt-6-luna"),
        ("proposal_brief", "gpt-6-luna"),
    ], f"every call made is stored in the end, the held ones first: {spend.rows}"


async def test_a_model_with_no_price_warns_once_an_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openrouter")
    module = _module()
    now = [0.0]
    calls = module.BackgroundCalls(resolver=Resolver(), spend=Spend(), gate=Gate(), clock=lambda: now[0])
    with capture_logs() as logs:
        for _ in range(3):
            assert await calls.endpoint("job_brief", "openrouter/acme/unlisted", "acme/widgets") is None
        now[0] = 3601.0
        assert await calls.endpoint("job_brief", "openrouter/acme/unlisted", "acme/widgets") is None
    levels = [e["log_level"] for e in logs if e["event"] == "background_model_unpriced"]
    assert levels == ["warning", "info", "info", "warning"], f"a warning once an hour, not on every tick: {levels}"


async def test_past_the_held_limit_the_rows_that_cost_nothing_are_dropped_first(
    providers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A $0 row changes no cap: when held rows must be dropped, they go before any priced one (#287)."""
    module = _module()
    monkeypatch.setattr(module, "MAX_UNRECORDED", 3)
    spend = Spend()
    spend.failing = True
    calls = module.BackgroundCalls(resolver=Resolver(), spend=spend, gate=Gate())
    usage = '{"prompt_tokens": 1000000, "completion_tokens": 0}'
    async with _raw_client([], _body(usage)) as client:
        assert await _ask(calls, "gpt-6-luna", client) == "answered"
        with capture_logs() as logs:
            for _ in range(4):
                assert await _ask(calls, "custom/qwen", client) == "answered"
    held = calls._unrecorded["job_brief"]  # noqa: SLF001 - what the feature holds is what the test reads
    assert [row["model"] for row in held] == ["gpt-6-luna", "custom/qwen", "custom/qwen"], (
        f"the priced row is kept and the $0 ones are dropped: {[row['model'] for row in held]}"
    )
    dropped = [e for e in logs if e["event"] == "background_spend_rows_dropped"]
    assert dropped and all(e["cost_usd"] == 0 for e in dropped), dropped


async def test_a_held_row_keeps_its_call_s_id_and_time_when_it_is_stored_later(providers: None) -> None:
    """Stored again after a driver error that hid its commit, a row is the same row (its id), and it is dated
    when the call was made, not when it was finally stored (#287)."""
    module = _module()
    import inspect

    assert "wall" in inspect.signature(module.BackgroundCalls).parameters, (
        "a call's row is dated by the clock of the call, which the calls take (#287)"
    )
    spend = Spend()
    wall = [datetime(2026, 10, 30, 9, 0, tzinfo=UTC)]
    calls = module.BackgroundCalls(resolver=Resolver(), spend=spend, gate=Gate(), wall=lambda: wall[0])
    attempts: list[str] = []
    record = spend.record

    async def counting(**row: Any) -> None:
        attempts.append(str(row.get("call_id")))
        await record(**row)

    spend.record = counting  # type: ignore[method-assign]
    async with _raw_client([], _body('{"prompt_tokens": 10, "completion_tokens": 1}')) as client:
        spend.failing = True
        assert await _ask(calls, "gpt-6-luna", client) == "answered"
        spend.failing = False
        wall[0] = datetime(2026, 10, 30, 11, 0, tzinfo=UTC)
        assert await _ask(calls, "gpt-6-luna", client) == "answered"
    assert len(set(attempts)) == 2 and attempts[0] == attempts[1], (
        f"the held row is stored again under the id it was first tried with: {attempts}"
    )
    assert [row["at"] for row in spend.rows] == [
        datetime(2026, 10, 30, 9, 0, tzinfo=UTC),
        datetime(2026, 10, 30, 11, 0, tzinfo=UTC),
    ], "each row is dated when its call was made"


async def test_a_failing_insert_is_an_error_once_a_minute_not_on_every_call(providers: None) -> None:
    module = _module()
    now = [0.0]
    spend = Spend()
    spend.failing = True
    calls = module.BackgroundCalls(resolver=Resolver(), spend=spend, gate=Gate(), clock=lambda: now[0])
    async with _raw_client([], _body('{"prompt_tokens": 10, "completion_tokens": 1}')) as client:
        with capture_logs() as logs:
            for at in (0.0, 10.0, 30.0, 61.0):
                now[0] = at
                await _ask(calls, "custom/qwen", client)
    levels = [e["log_level"] for e in logs if e["event"] == "background_spend_record_failed"]
    assert len(levels) > 4 and levels[0] == "error" and levels.count("error") == 2, (
        f"the first failure and then once a minute at error level, in between at info: {levels}"
    )
