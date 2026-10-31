"""Proposal briefs (spec 2026-10-25): each owner proposal in plain words, written by a model, never in the feed's way."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from embry0.services.brief_writer import RETRY_SECONDS
from embry0.services.proposal_briefs import (
    Brief,
    ProposalBriefWriter,
    held,
    held_by,
    input_hash,
    parse_brief,
    user_prompt,
    with_briefs,
)
from embry0.services.proposals import Proposal
from embry0.settings.sections.leads import LeadsSettings
from tests.fixtures.leads import MemorySettings, brief_reader, leads_policy, leads_section

T0 = datetime(2026, 10, 25, 12, tzinfo=UTC)
GOOD = {
    "title": "Stop retrying an email the lead already has",
    "problem": "Saving a sent email that is already on a lead fails as a duplicate, and the backend retries it forever.",
    "fix": "Treat the duplicate as saved and stop retrying. You get a pull request.",
    "size": "small",
}


def _proposal(
    pid: str = "case:hc-1", kind: str = "case", *, details: str = "Severity medium, seen 3 times", **extra: Any
) -> Proposal:
    return Proposal(
        id=pid,
        kind=kind,
        repo="acme/api",
        title="Stop retrying mail.outbound-stored on duplicate lead activity id",
        summary="The watcher keeps seeing this in production (3x, medium): …",
        details=details,
        action="Run the fix pipeline on the draft ticket",
        created_at=T0,
        extra=extra,
    )


def test_a_good_answer_is_a_brief() -> None:
    brief = parse_brief(json.dumps(GOOD))
    assert brief == Brief(GOOD["title"], GOOD["problem"], GOOD["fix"], "small")


def test_a_fenced_answer_is_read_and_a_long_title_is_cut_at_a_word() -> None:
    long = {
        **GOOD,
        "title": "Stop retrying an email the lead already has on every single poll of the outbox loop",
        "size": "Medium",
    }
    brief = parse_brief("```json\n" + json.dumps(long) + "\n```")
    assert len(brief.title) <= 70 and brief.title.endswith("…") and " " in brief.title
    assert brief.size == "medium"


@pytest.mark.parametrize(
    "answer, why",
    [
        ("not json", "not JSON"),
        (json.dumps({**GOOD, "extra": "x"}), "exactly"),
        (json.dumps({k: v for k, v in GOOD.items() if k != "fix"}), "exactly"),
        (json.dumps({**GOOD, "size": "huge"}), "size"),
        (json.dumps({**GOOD, "problem": ""}), "problem"),
        (json.dumps({**GOOD, "fix": "Accept case:hc-1 to start."}), "proposal id"),
        (json.dumps({**GOOD, "problem": "x " * 300}), "over"),
        (json.dumps([GOOD]), "exactly"),
    ],
)
def test_anything_else_is_refused(answer: str, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        parse_brief(answer)


def test_a_count_going_up_keeps_the_brief_and_a_changed_ticket_does_not() -> None:
    base = _proposal(details="Severity medium, seen 3 times since 2026-10-20")
    assert input_hash(base) == input_hash(_proposal(details="Severity medium, seen 41 times since 2026-10-20"))
    assert input_hash(base) != input_hash(_proposal(details="Severity high, seen 3 times since 2026-10-20"))


def test_the_prompt_carries_the_proposal_and_no_id() -> None:
    prompt = user_prompt(_proposal(paths=["src/outbox.py"], severity="medium"))
    assert "a problem the log watcher keeps seeing in production" in prompt
    assert "Repository: api" in prompt and "Files: src/outbox.py" in prompt
    assert "What a yes starts: the agents investigate the problem, fix it and open a pull request" in prompt
    assert "case:hc-1" not in prompt


def test_a_ticket_below_the_floor_is_held_and_nothing_else_is() -> None:
    policy = LeadsSettings(ask_min=60)
    assert held(_proposal("ticket:a", "ticket", source={"readiness": 40}), policy)
    assert not held(_proposal("ticket:b", "ticket", source={"readiness": 60}), policy)
    assert not held(_proposal("ticket:c", "ticket"), policy), "a ticket with no score is not held"
    assert not held(_proposal(), policy)


def test_which_severities_reach_chat_is_the_leads_section_s_not_the_catalogue_s() -> None:
    """``leads.ask_severities`` (#166): a kind it names is asked only at the severities it lists; one it leaves
    out keeps the default's entry. A ticket below ``ask_min`` stays held whatever the mapping says."""
    every = ["low", "medium", "high", "critical"]
    policy = leads_policy(
        {"ask_severities": {"ci": ["critical"], "case": ["high", "critical"], "ticket": ["high"], "crack": every}}
    )
    asked = {
        "a critical failing check": _proposal("ci:a", "ci", severity="critical"),
        "a medium crack, every severity named": _proposal("crack:a", "crack", severity="medium"),
        "a high ticket ready 70": _proposal("ticket:a", "ticket", severity="high", source={"readiness": 70}),
    }
    kept = {
        "a high failing check": _proposal("ci:b", "ci", severity="high"),
        "a case that came with no severity": _proposal("case:u", "case", severity="unrated"),
        "a high ticket ready 40": _proposal("ticket:b", "ticket", severity="high", source={"readiness": 40}),
        "a low ticket ready 70": _proposal("ticket:c", "ticket", severity="low", source={"readiness": 70}),
    }
    assert [what for what, p in asked.items() if held_by(p, policy)] == [], "each of these is asked in chat"
    assert [what for what, p in kept.items() if not held_by(p, policy)] == [], "each of these is held from chat"
    default = leads_policy()
    assert held_by(_proposal("crack:b", "crack", severity="medium"), default), "by default a medium crack is held"
    assert not held_by(_proposal("crack:c", "crack", severity="high"), default), "and a high one is asked"


def test_the_feed_shows_a_brief_only_while_it_matches_the_proposal() -> None:
    p = _proposal()
    row = {"input_hash": input_hash(p), "model": "custom/local", "written_at": T0, **GOOD}
    [shown] = with_briefs([p], {p.id: row}, leads_policy())
    assert shown["brief"]["title"] == GOOD["title"] and shown["brief"]["written_at"] == T0.isoformat()
    assert shown["held"] is False and shown["summary"] == p.summary
    [stale] = with_briefs([p], {p.id: {**row, "input_hash": "old"}}, leads_policy())
    assert stale["brief"] is None


async def test_a_local_model_is_asked_for_json_without_thinking(custom_enabled: None) -> None:
    sent: list[httpx.Request] = []
    briefs = FakeBriefs()
    assert await _writer(briefs, [_proposal()], [], sent=sent).tick() == ["case:hc-1"]
    assert briefs.rows["case:hc-1"]["size"] == "small"
    [call] = [{"url": str(r.url), "auth": r.headers["authorization"], "body": json.loads(r.content)} for r in sent]
    assert call["url"] == "http://models.test/v1/chat/completions" and call["auth"] == "Bearer k"
    assert call["body"]["model"] == "local-27b"
    assert call["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert call["body"]["response_format"]["type"] == "json_schema"
    assert call["body"]["response_format"]["json_schema"]["schema"]["required"] == ["title", "problem", "fix", "size"]


class FakeBriefs:
    def __init__(self, rows: dict[str, dict[str, Any]] | None = None) -> None:
        self.rows = dict(rows or {})
        self.pruned: list[set[str]] = []

    async def all(self) -> dict[str, dict[str, Any]]:
        return dict(self.rows)

    async def save(self, pid: str, digest: str, brief: Brief, *, model: str, prompt_hash: str = "") -> None:
        self.rows[pid] = {"input_hash": digest, "model": model, **brief.to_dict()}

    async def prune(self, keep: set[str]) -> int:
        self.pruned.append(set(keep))
        gone = [k for k in self.rows if k not in keep]
        for k in gone:
            del self.rows[k]
        return len(gone)


class FakeAgents:
    """``AgentDefinitionsRepository`` as the brief writer reads it: the built-in rows, as the seeder stores them."""

    def __init__(self, **prompts: str) -> None:
        from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

        self.rows = {t: {"type": t, **BUILTIN_SEED[t]} for t in ("proposal-brief", "job-brief")}
        for agent_type, prompt in prompts.items():
            self.rows[agent_type.replace("_", "-")]["system_prompt"] = prompt
        self.reads = 0

    async def get(self, agent_type: str) -> dict[str, Any] | None:
        self.reads += 1
        row = self.rows.get(agent_type)
        return dict(row) if row is not None else None


class FakeResolver:
    async def resolve_llm(self, repo: str, provider: str) -> Any:
        return SimpleNamespace(secret="k", metadata={"base_url": "http://models.test/v1"})


class NoSpend:
    async def record(self, **kwargs: Any) -> None:
        return None


class OpenGate:
    async def check(self, **kwargs: Any) -> Any:
        return SimpleNamespace(allowed=True)


def _calls() -> Any:
    """The metered call path (#164), imported here so this file still collects on a tree without it."""
    from embry0.services.background_models import BackgroundCalls

    return BackgroundCalls(resolver=FakeResolver(), spend=NoSpend(), gate=OpenGate())


def _writer(
    briefs: FakeBriefs,
    proposals: list[Proposal],
    answers: list[Any],
    *,
    model: str = "custom/local-27b",
    clock: Any = None,
    per_tick: int = 3,
    settings: MemorySettings | None = None,
    sent: list[httpx.Request] | None = None,
) -> ProposalBriefWriter:
    def answer(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.append(request)
        content = answers.pop(0) if answers else json.dumps(GOOD)
        if isinstance(content, httpx.Response):
            return content
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async def feed() -> list[Proposal]:
        return proposals

    async def chosen() -> Any:
        return lambda repo: model

    return ProposalBriefWriter(
        proposals=feed,
        settings=brief_reader(chosen, settings),
        briefs=briefs,
        calls=_calls(),
        per_tick=per_tick,
        client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
        clock=clock or (lambda: 0.0),
    )


@pytest.fixture
def custom_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "custom")


async def test_the_writer_fills_missing_briefs_a_few_a_tick_cases_first(custom_enabled: None) -> None:
    proposals = [
        _proposal("finding:f1", "finding"),
        _proposal("ticket:held", "ticket", source={"readiness": 30}),
        _proposal("issue:i1", "issue"),
        _proposal("case:c1", "case"),
        _proposal("ticket:t1", "ticket", source={"readiness": 80}),
    ]
    briefs = FakeBriefs({"case:gone": {"input_hash": "x", **GOOD}})
    writer = _writer(briefs, proposals, [], per_tick=3)
    assert await writer.tick() == ["case:c1", "ticket:t1", "issue:i1"]
    assert "case:gone" not in briefs.rows, "a proposal no longer asked loses its brief"
    assert await writer.tick() == ["finding:f1", "ticket:held"], "held tickets come last"
    assert await writer.tick() == [], "every brief matches: nothing to write"


async def test_held_tickets_come_last_by_the_ask_min_the_section_holds_at_each_tick(custom_enabled: None) -> None:
    """A ticket ready 70 is held under ask_min 80 and asked under 60; the writer reads the section every tick."""
    leads_section()
    proposals = [
        _proposal("ticket:t70", "ticket", source={"readiness": 70}),
        _proposal("finding:f1", "finding"),
        _proposal("finding:f2", "finding"),
    ]
    settings = MemorySettings({"ask_min": 80})
    writer = _writer(FakeBriefs(), proposals, [], per_tick=1, settings=settings)
    assert await writer.tick() == ["finding:f1"], "ask_min 80: the ticket is held, so its brief waits behind the rest"
    settings.deployment["ask_min"] = 60
    assert await writer.tick() == ["ticket:t70"], (
        "ask_min 60 by the next tick: the ticket is asked now, so it goes before the finding left"
    )


async def test_held_tickets_come_last_by_their_organization_s_ask_min(custom_enabled: None) -> None:
    """The deployment asks from 80, acme's layer from 60: acme's ticket ready 70 is asked, so it goes first."""
    leads_section()
    proposals = [_proposal("finding:f1", "finding"), _proposal("ticket:t70", "ticket", source={"readiness": 70})]
    assert {p.repo for p in proposals} == {"acme/api"}
    settings = MemorySettings({"ask_min": 80}, {"acme": {"ask_min": 60}})
    writer = _writer(FakeBriefs(), proposals, [], per_tick=1, settings=settings)
    assert await writer.tick() == ["ticket:t70"], (
        "the ticket's repository's organization asks from 60: not held, so it is written before the finding"
    )


async def test_a_changed_proposal_gets_a_new_brief(custom_enabled: None) -> None:
    p = _proposal()
    briefs = FakeBriefs({p.id: {"input_hash": "written-from-an-older-ticket", **GOOD}})
    assert await _writer(briefs, [p], []).tick() == [p.id]
    assert briefs.rows[p.id]["input_hash"] == input_hash(p)


async def test_a_bad_answer_waits_before_the_proposal_is_tried_again(custom_enabled: None) -> None:
    now = [0.0]
    p = _proposal()
    briefs = FakeBriefs()
    writer = _writer(briefs, [p], ["not json", httpx.Response(503)], clock=lambda: now[0])
    assert await writer.tick() == [] and p.id not in briefs.rows
    now[0] = RETRY_SECONDS - 1
    assert await writer.tick() == [], "still waiting out the failure"
    now[0] = RETRY_SECONDS + 1
    assert await writer.tick() == [], "a server error is a failure too"
    now[0] = 3 * RETRY_SECONDS
    assert await writer.tick() == [p.id]


async def test_without_a_model_nothing_is_written_and_nothing_is_called(custom_enabled: None) -> None:
    briefs = FakeBriefs({"case:gone": {"input_hash": "x", **GOOD}})
    writer = _writer(briefs, [_proposal()], [httpx.Response(500)], model="")
    assert await writer.tick() == []
    assert briefs.rows == {}, "pruning still runs"


async def test_a_model_off_the_chat_completions_family_is_not_called(custom_enabled: None) -> None:
    briefs = FakeBriefs()
    writer = _writer(briefs, [_proposal()], [], model="claude-sonnet-5")
    assert await writer.tick() == [] and briefs.rows == {}


async def test_a_new_proposal_is_due_and_a_written_one_is_not(custom_enabled: None) -> None:
    p, q = _proposal("case:a"), _proposal("case:b")
    briefs = FakeBriefs({p.id: {"input_hash": input_hash(p), **GOOD}})
    assert await _writer(briefs, [p, q], []).tick() == ["case:b"]


async def test_newest_first_within_a_kind(custom_enabled: None) -> None:
    old = Proposal(**{**_proposal("case:old").__dict__, "created_at": T0 - timedelta(days=1)})
    new = _proposal("case:new")
    assert await _writer(FakeBriefs(), [old, new], [], per_tick=1).tick() == ["case:new"]


class ChangingDocuments:
    """A settings store whose documents change after every read: as if a write lands between two reads."""

    VERSIONS = (
        {"background_models": {"proposal_brief": "custom/first"}, "leads": {"ask_min": 80}},
        {"background_models": {"proposal_brief": "custom/second"}, "leads": {"ask_min": 60}},
    )

    def __init__(self) -> None:
        self.reads = 0

    async def snapshot(self) -> Any:
        from embry0.settings.scope import DEPLOYMENT
        from embry0.settings.sections import CATALOG
        from embry0.settings.store import build_snapshot

        data = self.VERSIONS[min(self.reads, len(self.VERSIONS) - 1)]
        self.reads += 1
        rows = [{"scope": DEPLOYMENT, "data": data, "generation": self.reads, "updated_at": None}]
        return build_snapshot(CATALOG, rows, token=(), log=False)


async def test_a_tick_reads_its_model_and_its_leads_from_one_version_of_the_documents(custom_enabled: None) -> None:
    """Version 1 writes on custom/first and asks from 80; version 2, written between two reads, on custom/second
    from 60. One tick takes version 1 whole: the ticket ready 70 is held, so the finding goes first, on first."""
    from embry0.services import proposal_briefs

    leads_section()
    reader = getattr(proposal_briefs, "brief_settings", None)
    assert reader is not None, "the brief writer's reader takes one snapshot of the settings documents per tick"
    sent: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(GOOD)}}]})

    async def feed() -> list[Proposal]:
        return [_proposal("ticket:t70", "ticket", source={"readiness": 70}), _proposal("finding:f1", "finding")]

    store = ChangingDocuments()
    writer = ProposalBriefWriter(
        proposals=feed,
        settings=reader(store, FakeAgents()),
        briefs=FakeBriefs(),
        calls=_calls(),
        per_tick=1,
        client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
    )

    written = await writer.tick()

    assert store.reads == 1, f"one tick reads the settings documents once: {store.reads}"
    assert (written, sent) == (["finding:f1"], ["first"]), (
        "the model and ask_min of one version: the ticket is held under 80, so the finding is written first, "
        f"on custom/first: {written}, {sent}"
    )
