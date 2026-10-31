"""Background model calls outside a job (#164): keyed, capped and metered as a job's run is.

- The key is resolved with the repository the call is about, so its bindings apply.
- A hosted model's call asks the budget gate first; refused, nothing is sent, the refusal is logged and the
  loop goes on. The operator's own server bills nothing and is never refused.
- What a call spent is priced as a job's run is and recorded under its feature; a model with no price is never
  called.
- The import carries the old watcher model and the retired chain into the section without changing a model.

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

GOOD = {
    "title": "Stop retrying a saved email",
    "problem": "It retries forever.",
    "fix": "Stop. You get a PR.",
    "size": "small",
}
T0 = datetime(2026, 10, 30, 12, tzinfo=UTC)


def _module() -> Any:
    try:
        from embry0.services import background_models
    except ImportError:
        pytest.fail("embry0.services.background_models makes every background call outside a job (#164)")
    return background_models


class Resolver:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    async def resolve_llm(self, repo: str, provider: str) -> Any:
        self.asked.append((repo, provider))
        return SimpleNamespace(secret="k", metadata={"base_url": "http://models.test/v1"})


class Spend:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, **row: Any) -> None:
        self.rows.append(row)


class Gate:
    """The budget gate's answer: allowed, or refused by the daily cap."""

    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.checks = 0

    async def check(self, **kwargs: Any) -> Any:
        self.checks += 1
        if self.allowed:
            return SimpleNamespace(allowed=True, limit="", message="")
        return SimpleNamespace(
            allowed=False,
            limit="daily",
            message="Daily budget cap reached: $5.00 spent today against a $5.00 cap",
            public_message="dispatch refused: budget cap reached",
        )


def _answering(sent: list[dict[str, Any]], usage: dict[str, Any] | None) -> httpx.AsyncClient:
    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        body: dict[str, Any] = {"choices": [{"message": {"content": json.dumps(GOOD)}}]}
        if usage is not None:
            body["usage"] = usage
        return httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openai,custom")


def _proposal(repo: str = "acme/widgets") -> Proposal:
    return Proposal(
        id="case:hc-1",
        kind="case",
        repo=repo,
        title="Stop retrying a saved email",
        summary="…",
        details="Seen 3 times",
        action="Run the fix pipeline",
        created_at=T0,
        extra={},
    )


async def test_a_hosted_model_s_spend_is_priced_as_a_job_s_and_recorded_under_its_feature(providers: None) -> None:
    from embry0.agents.providers import price_usage

    module = _module()
    resolver, spend, gate = Resolver(), Spend(), Gate()
    calls = module.BackgroundCalls(resolver=resolver, spend=spend, gate=gate)
    endpoint = await calls.endpoint("proposal_brief", "gpt-6-luna", "acme/widgets")
    assert endpoint is not None and not endpoint.local
    assert resolver.asked == [("acme/widgets", "openai")], (
        "the key is resolved with the repository the call is about, so its repository and owner bindings apply"
    )
    sent: list[dict[str, Any]] = []
    usage = {"prompt_tokens": 1_200, "completion_tokens": 300, "prompt_tokens_details": {"cached_tokens": 200}}
    async with _answering(sent, usage) as client:
        text = await calls.ask(
            "proposal_brief", endpoint, client, repo="acme/widgets", system="s", user="u", schema={}, name="brief"
        )
    assert json.loads(text) == GOOD and len(sent) == 1
    assert gate.checks == 1, "a hosted model's call asks the budget gate first"
    [row] = spend.rows
    expected = price_usage("gpt-6-luna", input_tokens=1_000, output_tokens=300, cache_read_tokens=200)
    assert expected and expected > 0
    assert {k: v for k, v in row.items() if k not in ("call_id", "at")} == {
        "feature": "proposal_brief",
        "model": "gpt-6-luna",
        "repo": "acme/widgets",
        "input_tokens": 1_000,
        "output_tokens": 300,
        "cache_read_tokens": 200,
        "cost_usd": pytest.approx(expected),
    }, "the provider's usage, priced as a job's run is, recorded under the feature that spent it"


async def test_a_call_over_the_caps_is_refused_before_anything_is_sent(providers: None) -> None:
    module = _module()
    spend, gate = Spend(), Gate(allowed=False)
    calls = module.BackgroundCalls(resolver=Resolver(), spend=spend, gate=gate, clock=lambda: 0.0)
    endpoint = await calls.endpoint("job_brief", "gpt-6-luna", "acme/widgets")
    sent: list[dict[str, Any]] = []
    async with _answering(sent, {"prompt_tokens": 1, "completion_tokens": 1}) as client:
        with capture_logs() as logs, pytest.raises(module.BackgroundCallRefused) as refused:
            await calls.ask("job_brief", endpoint, client, repo=None, system="s", user="u", schema={}, name="b")
        with capture_logs() as again, pytest.raises(module.BackgroundCallRefused):
            await calls.ask("job_brief", endpoint, client, repo=None, system="s", user="u", schema={}, name="b")
    assert sent == [] and spend.rows == [], "nothing is sent and nothing is spent"
    assert refused.value.decision.limit == "daily"
    [warning] = [e for e in logs if e["event"] == "background_model_refused"]
    assert (warning["log_level"], warning["feature"], warning["limit"]) == ("warning", "job_brief", "daily")
    assert [e["log_level"] for e in again if e["event"] == "background_model_refused"] == ["info"], (
        "a refusal that lasts is said at warning level once an hour, not on every tick"
    )


async def test_the_operator_s_own_server_bills_nothing_and_is_never_refused(providers: None) -> None:
    module = _module()
    spend, gate = Spend(), Gate(allowed=False)
    calls = module.BackgroundCalls(resolver=Resolver(), spend=spend, gate=gate)
    endpoint = await calls.endpoint("slop_judge", "custom/qwen", "acme/widgets")
    assert endpoint is not None and endpoint.local
    sent: list[dict[str, Any]] = []
    async with _answering(sent, {"prompt_tokens": 900, "completion_tokens": 40}) as client:
        await calls.ask("slop_judge", endpoint, client, repo="acme/widgets", system="s", user="u", schema={}, name="j")
    assert gate.checks == 0 and len(sent) == 1, "a local model never asks the caps"
    assert [(r["feature"], r["input_tokens"], r["cost_usd"]) for r in spend.rows] == [("slop_judge", 900, 0.0)]


async def test_a_model_with_no_price_is_never_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openrouter")
    module = _module()
    resolver = Resolver()
    calls = module.BackgroundCalls(resolver=resolver, spend=Spend(), gate=Gate())
    with capture_logs() as logs:
        assert await calls.endpoint("job_brief", "openrouter/acme/unlisted", "acme/widgets") is None
    assert resolver.asked == [], "no key is even looked for"
    assert [e["event"] for e in logs] == ["background_model_unpriced"]


async def test_a_writer_refused_by_the_caps_stops_its_tick_and_writes_on_a_later_one(providers: None) -> None:
    module = _module()
    from embry0.services.proposal_briefs import ProposalBriefWriter

    gate, spend = Gate(allowed=False), Spend()
    rows: dict[str, Any] = {}

    class Briefs:
        async def all(self) -> dict[str, Any]:
            return dict(rows)

        async def save(self, pid: str, digest: str, brief: Any, *, model: str, prompt_hash: str = "") -> None:
            rows[pid] = {"input_hash": digest, "model": model}

        async def prune(self, keep: set[str]) -> int:
            return 0

    async def feed() -> list[Proposal]:
        return [_proposal()]

    async def models() -> Any:
        return lambda repo: "gpt-6-luna"

    sent: list[dict[str, Any]] = []
    writer = ProposalBriefWriter(
        proposals=feed,
        settings=brief_reader(models),
        briefs=Briefs(),
        calls=module.BackgroundCalls(resolver=Resolver(), spend=spend, gate=gate),
        client=_answering(sent, {"prompt_tokens": 10, "completion_tokens": 5}),
    )
    assert await writer.tick() == [], "over the caps: no brief, and no exception out of the tick"
    assert sent == [] and rows == {}
    gate.allowed = True
    assert await writer.tick() == ["case:hc-1"], "the refused proposal is not held back for a retry delay"
    assert [r["feature"] for r in spend.rows] == ["proposal_brief"]


async def test_each_feature_reads_its_own_key_per_repository_from_one_snapshot() -> None:
    module = _module()
    from embry0.settings.sections import CATALOG
    from embry0.settings.store import build_snapshot

    rows = [
        {
            "scope": "deployment",
            "data": {"background_models": {"default": "custom/local", "scout": False}},
            "generation": 3,
            "updated_at": None,
        },
        {"scope": "organization:acme", "data": {"background_models": {"proposal_brief": "gpt-6-luna"}}},
    ]
    snapshot = build_snapshot(CATALOG, rows, token=(), log=False)

    class Store:
        reads = 0

        async def snapshot(self) -> Any:
            Store.reads += 1
            return snapshot

    briefs = await module.background_model(Store(), "proposal_brief")()
    judge = await module.background_model(Store(), "slop_judge")()
    scout = await module.background_model(Store(), "scout")()
    assert Store.reads == 3, "one snapshot read per tick, whatever the number of repositories"
    assert (briefs("acme/widgets"), briefs("other/app"), briefs(None)) == (
        "gpt-6-luna",
        "custom/local",
        "custom/local",
    ), "an organization's own model for its repositories; the deployment's elsewhere"
    assert judge("acme/widgets") == "custom/local", "the judge reads its own key, not the proposal briefs'"
    assert scout("acme/widgets") == "", "false: no model"
    with pytest.raises(ValueError):
        module.background_model(Store(), "default")


def _config(**values: str) -> Any:
    return SimpleNamespace(
        scout_model=values.get("scout", ""),
        context_audit_local_model=values.get("audit", ""),
        proposal_brief_model=values.get("proposal", ""),
        job_brief_model=values.get("job", ""),
    )


@pytest.mark.parametrize(
    ("watcher", "env", "stored", "expected"),
    [
        ("", {}, {}, {"scout": False, "context_audit": False}),
        ("custom/w", {}, {}, {"default": "custom/w", "scout": False, "context_audit": False}),
        ("custom/w", {"scout": "custom/s"}, {"scout": "custom/s"}, {"default": "custom/w", "context_audit": False}),
        (
            "custom/w",
            {"proposal": "custom/p"},
            {"proposal_brief": "custom/p"},
            {
                "default": "custom/w",
                "scout": False,
                "context_audit": False,
                "job_brief": "custom/p",
                "slop_judge": "custom/p",
            },
        ),
        (
            "",
            {"job": "custom/j", "proposal": "custom/p"},
            {"proposal_brief": "custom/p", "job_brief": "custom/j"},
            {"scout": False, "context_audit": False, "slop_judge": "custom/j"},
        ),
        ("custom/w", {}, {"default": "custom/x"}, {"scout": False, "context_audit": False}),
        (
            "",
            {"audit": "custom/c", "scout": "custom/s"},
            {"context_audit": "custom/c", "scout": "custom/s"},
            {},
        ),
        # The environment import failed and stored nothing: each variable's key comes from the variable.
        (
            "custom/w",
            {"audit": "custom/c", "scout": "custom/s", "proposal": "custom/p", "job": "custom/j"},
            {},
            {
                "default": "custom/w",
                "context_audit": "custom/c",
                "scout": "custom/s",
                "proposal_brief": "custom/p",
                "job_brief": "custom/j",
                "slop_judge": "custom/j",
            },
        ),
        (
            "custom/w",
            {"proposal": "gpt-6-luna"},
            {},
            {
                "default": "custom/w",
                "context_audit": False,
                "scout": False,
                "proposal_brief": "gpt-6-luna",
                "job_brief": "gpt-6-luna",
                "slop_judge": "gpt-6-luna",
            },
        ),
        (
            "custom/w",
            {"audit": "gpt-6-luna", "scout": "two words"},
            {},
            {"default": "custom/w", "context_audit": False, "scout": False},
        ),
    ],
    ids=[
        "nothing-set",
        "watcher-only",
        "own-scout",
        "proposal-chain",
        "job-chain",
        "stored-wins",
        "own-both",
        "nothing-imported",
        "nothing-imported-hosted-briefs",
        "nothing-imported-refused",
    ],
)
def test_the_import_keeps_what_each_feature_ran_on(
    watcher: str, env: dict[str, str], stored: dict[str, Any], expected: dict[str, Any]
) -> None:
    """Before: the watcher on W; proposal briefs on P or W; job briefs on J or P or W; the judge on the job briefs'
    model; the scout and the context audit on their own variables only. So the scout and the context audit are
    false whenever the import gave them nothing of their own, whatever W holds: a default written later, on the
    Providers page, never starts the scout or moves the context audits (#287). Each key a variable feeds is
    decided from that variable too, never left to the default when the environment import stored nothing: a first
    boot whose import failed runs no feature on the default in its variable's place, until the import runs again."""
    module = _module()
    assert module.legacy_values(watcher, _config(**env), stored) == expected


async def test_the_import_writes_nothing_into_a_document_whose_catalog_has_no_such_section() -> None:
    """A store built with a catalog of its own must not gain a section it does not know (#287)."""
    module = _module()

    class Store:
        catalog: tuple[Any, ...] = ()

        def edit(self, **kwargs: Any) -> Any:
            raise AssertionError("the import opened a document whose catalog has no background_models section")

    assert await module.import_provider_settings_once(Store(), _config()) == {}


@pytest.mark.parametrize(
    ("env", "expected_false"),
    [
        ({}, {"scout", "context_audit"}),
        ({"scout": "custom/s", "audit": "custom/c"}, set()),
        ({"scout": "gpt-6-luna"}, {"context_audit"}),
        ({"audit": "gpt-6-luna"}, {"scout", "context_audit"}),
        ({"scout": "two words", "audit": " "}, {"scout", "context_audit"}),
    ],
    ids=["unset", "both-own", "hosted-scout", "hosted-audit", "refused-or-empty"],
)
def test_false_is_decided_from_the_variables_not_from_what_is_stored(
    env: dict[str, str], expected_false: set[str]
) -> None:
    """A variable that gives its feature a model keeps the feature off false even before the environment import
    stored it: a first boot whose import failed must not lose it to a stored false (#287)."""
    module = _module()
    values = module.legacy_values("custom/w", _config(**env), {})
    assert {key for key, value in values.items() if value is False} == expected_false, values
