"""Proposal briefs (spec 2026-10-25): each owner proposal in plain words, written by a model, never in the feed's way."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from embry0.services.proposal_briefs import (
    RETRY_SECONDS,
    Brief,
    Endpoint,
    ProposalBriefWriter,
    held,
    input_hash,
    parse_brief,
    user_prompt,
    with_briefs,
    write_brief,
)
from embry0.services.proposals import Proposal

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
    assert held(_proposal("ticket:a", "ticket", source={"readiness": 40}), 60)
    assert not held(_proposal("ticket:b", "ticket", source={"readiness": 60}), 60)
    assert not held(_proposal("ticket:c", "ticket"), 60), "a ticket with no score is not held"
    assert not held(_proposal(), 60)


def test_the_feed_shows_a_brief_only_while_it_matches_the_proposal() -> None:
    p = _proposal()
    row = {"input_hash": input_hash(p), "model": "custom/local", "written_at": T0, **GOOD}
    [shown] = with_briefs([p], {p.id: row}, 60)
    assert shown["brief"]["title"] == GOOD["title"] and shown["brief"]["written_at"] == T0.isoformat()
    assert shown["held"] is False and shown["summary"] == p.summary
    [stale] = with_briefs([p], {p.id: {**row, "input_hash": "old"}}, 60)
    assert stale["brief"] is None


async def test_a_local_model_is_asked_for_json_without_thinking() -> None:
    sent: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(
            {"url": str(request.url), "auth": request.headers["authorization"], "body": json.loads(request.content)}
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(GOOD)}}]})

    endpoint = Endpoint("custom/local-27b", "http://models.test/v1", "k", "local-27b", local=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        brief = await write_brief(_proposal(), endpoint, client, _calls())
    assert brief.size == "small"
    [call] = sent
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

    async def save(self, pid: str, digest: str, brief: Brief, *, model: str) -> None:
        self.rows[pid] = {"input_hash": digest, "model": model, **brief.to_dict()}

    async def prune(self, keep: set[str]) -> int:
        self.pruned.append(set(keep))
        gone = [k for k in self.rows if k not in keep]
        for k in gone:
            del self.rows[k]
        return len(gone)


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
) -> ProposalBriefWriter:
    def answer(request: httpx.Request) -> httpx.Response:
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
        models=chosen,
        briefs=briefs,
        calls=_calls(),
        min_readiness=60,
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


def test_a_new_proposal_is_due_and_a_written_one_is_not() -> None:
    p, q = _proposal("case:a"), _proposal("case:b")
    writer = _writer(FakeBriefs(), [], [])
    stored = {p.id: {"input_hash": input_hash(p)}}
    assert [x.id for x in writer.due([p, q], stored)] == ["case:b"]


def test_newest_first_within_a_kind() -> None:
    old = Proposal(**{**_proposal("case:old").__dict__, "created_at": T0 - timedelta(days=1)})
    new = _proposal("case:new")
    writer = _writer(FakeBriefs(), [], [])
    assert [x.id for x in writer.due([old, new], {})] == ["case:new", "case:old"]
