"""Job briefs (spec 2026-10-26): each job in plain words, written on the proposal-brief machinery, never in the way."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from embry0.services import job_briefs
from embry0.services.job_briefs import (
    JobBrief,
    JobBriefWriter,
    brief_view,
    input_hash,
    parse_job_brief,
    user_prompt,
    write_job_brief,
)
from embry0.services.proposal_briefs import RETRY_SECONDS, Endpoint

GOOD = {
    "title": "Find why the worker crash-looped and stop it",
    "what": "Both worker copies failed to boot 22 times on Oct 15; its pages errored.",
    "why": "A boot crash takes every page down, and the logs kept no cause.",
    "done": "The boot error is logged in full. You get a pull request.",
    "size": "medium",
}


def _job(job_id: str = "job-1", **over: Any) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "task": "[ENG-3075] [watcher] Diagnose the worker boot crash-loop\n\n## Evidence\n...",
        "repo": "acme/backend",
        "area": "Workers",
        "job_kind": "code",
        "pipeline_template": "full-magnum-opus",
        "work_nature": None,
        "issue_title": "Diagnose the worker boot crash-loop",
        "status": "running",
        "brief_hash": None,
        **over,
    }


def test_a_good_answer_is_a_brief() -> None:
    assert parse_job_brief(json.dumps(GOOD)) == JobBrief(**GOOD)


def test_tags_ids_and_tracker_keys_are_taken_out_not_refused() -> None:
    noisy = {
        **GOOD,
        "title": "[watcher] ENG-3075: Find why the worker crash-looped",
        "done": "See job-1a2b3c4d5e6f. You get a pull request.",
    }
    brief = parse_job_brief(json.dumps(noisy))
    assert brief.title == "Find why the worker crash-looped"
    assert brief.done == "See. You get a pull request."
    assert "job-1a2b" not in brief.done and "ENG-3075" not in brief.title


@pytest.mark.parametrize(
    "answer, why",
    [
        ("nope", "not JSON"),
        (json.dumps({**GOOD, "extra": 1}), "exactly"),
        (json.dumps({k: v for k, v in GOOD.items() if k != "why"}), "exactly"),
        (json.dumps({**GOOD, "size": "xl"}), "size"),
        (json.dumps({**GOOD, "what": "[watcher]"}), "what is empty"),
        (json.dumps({**GOOD, "why": 3}), "why is not text"),
        (json.dumps({**GOOD, "done": "x " * 300}), "over"),
    ],
)
def test_anything_else_is_refused(answer: str, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        parse_job_brief(answer)


def test_the_hash_follows_the_inputs_and_the_prompt_version(monkeypatch: pytest.MonkeyPatch) -> None:
    base = _job()
    assert input_hash(base) == input_hash({**base, "status": "paused", "brief_hash": "x"}), "status is not an input"
    assert input_hash(base) != input_hash({**base, "area": "Billing"})
    assert input_hash(base) != input_hash({**base, "work_nature": {"nature": "slop", "slop_score": 80}})
    tail = "y" * (job_briefs.TASK_CHARS + 10)
    assert input_hash({**base, "task": tail + "a"}) == input_hash({**base, "task": tail + "b"}), (
        "past the cap is not read"
    )
    before = input_hash(base)
    monkeypatch.setattr(job_briefs, "PROMPT_VERSION", job_briefs.PROMPT_VERSION + 1)
    assert input_hash(base) != before, "a new prompt rewrites every brief"


def test_the_prompt_says_what_the_job_delivers_and_carries_no_job_id() -> None:
    prompt = user_prompt(_job(work_nature={"nature": "slop", "slop_score": 72, "kinds": ["dead code"]}))
    assert "Kind of work: a code change" in prompt and "Repository: backend, area Workers" in prompt
    assert "Ticket title: Diagnose the worker boot crash-loop" in prompt
    assert "Classified as: slop, slop score 72" in prompt
    assert "What it delivers: a pull request with the change" in prompt
    assert "job-1" not in prompt
    watcher = user_prompt(_job(job_kind="analysis", pipeline_template="analysis-default"))
    assert "an analysis of the production logs, and a ticket when it finds a problem worth one" in watcher


async def test_a_local_model_is_held_to_the_job_brief_schema_without_thinking() -> None:
    sent: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(GOOD)}}]})

    endpoint = Endpoint("custom/local-27b", "http://models.test/v1", "k", "local-27b", local=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        brief = await write_job_brief(_job(), endpoint, client)
    assert brief.size == "medium"
    [body] = sent
    assert body["response_format"]["json_schema"]["name"] == "job_brief"
    assert body["response_format"]["json_schema"]["schema"]["required"] == ["title", "what", "why", "done", "size"]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_a_stored_brief_is_shown_with_its_model_and_time() -> None:
    from datetime import UTC, datetime

    view = brief_view({**GOOD, "model": "m", "written_at": datetime(2026, 10, 26, tzinfo=UTC)})
    assert view == {**GOOD, "model": "m", "written_at": "2026-10-26T00:00:00+00:00"}


class FakeStore:
    def __init__(self, jobs: list[dict[str, Any]]) -> None:
        self.jobs = jobs
        self.saved: dict[str, tuple[str, JobBrief, str]] = {}

    async def candidates(self) -> list[dict[str, Any]]:
        return [
            {**j, "brief_hash": self.saved[j["job_id"]][0] if j["job_id"] in self.saved else j.get("brief_hash")}
            for j in self.jobs
        ]

    async def save(self, job_id: str, digest: str, brief: JobBrief, *, model: str) -> None:
        self.saved[job_id] = (digest, brief, model)


class FakeResolver:
    async def resolve_llm(self, repo: str, provider: str) -> Any:
        return SimpleNamespace(secret="k", metadata={"base_url": "http://models.test/v1"})


def _writer(
    store: FakeStore, answers: list[Any], *, model: str = "custom/local-27b", clock: Any = None, per_tick: int = 3
) -> JobBriefWriter:
    def answer(request: httpx.Request) -> httpx.Response:
        content = answers.pop(0) if answers else json.dumps(GOOD)
        if isinstance(content, httpx.Response):
            return content
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async def chosen() -> str:
        return model

    return JobBriefWriter(
        store=store,
        model=chosen,
        resolver=FakeResolver(),
        per_tick=per_tick,
        client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
        clock=clock or (lambda: 0.0),
    )


@pytest.fixture
def custom_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "custom")


async def test_the_writer_takes_jobs_in_the_store_s_order_a_few_a_tick(custom_enabled: None) -> None:
    jobs = [_job(f"job-{n}") for n in range(5)] + [_job("job-empty", task="  ")]
    store = FakeStore(jobs)
    writer = _writer(store, [], per_tick=3)
    assert await writer.tick() == ["job-0", "job-1", "job-2"], "live jobs first, as the store orders them"
    assert await writer.tick() == ["job-3", "job-4"], "a job with no task has nothing to brief"
    assert await writer.tick() == []
    digest, brief, model = store.saved["job-0"]
    assert digest == input_hash(jobs[0]) and brief.title == GOOD["title"] and model == "custom/local-27b"


async def test_a_bad_answer_waits_before_the_job_is_tried_again(custom_enabled: None) -> None:
    now = [0.0]
    store = FakeStore([_job()])
    writer = _writer(store, ["not json", httpx.Response(500)], clock=lambda: now[0])
    assert await writer.tick() == []
    now[0] = RETRY_SECONDS - 1
    assert await writer.tick() == []
    now[0] = RETRY_SECONDS + 1
    assert await writer.tick() == [], "a server error is a failure too"
    now[0] = 3 * RETRY_SECONDS
    assert await writer.tick() == ["job-1"]


async def test_without_a_usable_model_nothing_is_written(custom_enabled: None) -> None:
    store = FakeStore([_job()])
    assert await _writer(store, [], model="").tick() == []
    assert await _writer(store, [], model="claude-sonnet-5").tick() == [], "only the chat-completions family is called"
    assert store.saved == {}
