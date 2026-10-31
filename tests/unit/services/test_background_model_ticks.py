"""Each background feature reads its model at every tick, never once (#164).

A loop that read the section once, at startup, would run happily and ignore
every change to it: the failure the settings document exists to end. Each
loop here is handed a reader whose answer changes between two ticks, and the
second tick must run on the new model. ``tests/unit/api/test_background_models_settings.py``
pins that production hands each loop a reader of the real section.
"""

from __future__ import annotations

import inspect
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from embry0.services.job_briefs import JobBriefWriter
from embry0.services.proposal_briefs import ProposalBriefWriter
from embry0.services.scout import ScoutLoop
from embry0.services.slop_meter.loop import SlopMeterLoop
from tests.fixtures.leads import brief_reader

BRIEF = {
    "title": "Stop retrying a saved email",
    "problem": "It retries forever.",
    "fix": "Stop. You get a PR.",
    "size": "small",
}
JOB_BRIEF = {
    "title": "Find why it crashed",
    "what": "It crashed.",
    "why": "Pages fail.",
    "done": "You get a PR.",
    "size": "small",
}


class Reader:
    """``background_model(store, feature)`` as a test drives it: ``set`` changes the model before the next tick."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.reads = 0

    def set(self, model: str) -> None:
        self.model = model

    async def __call__(self) -> Any:
        self.reads += 1
        model = self.model
        return lambda repo: model


def _takes_a_reader(loop: type, parameter: str = "models") -> None:
    assert parameter in inspect.signature(loop).parameters, (
        f"{loop.__name__} takes a reader of the settings document's background_models section (#164)"
    )


def _calls() -> Any:
    from embry0.services.background_models import BackgroundCalls

    class Resolver:
        async def resolve_llm(self, repo: str, provider: str) -> Any:
            return SimpleNamespace(secret="k", metadata={"base_url": "http://models.test/v1"})

    class Spend:
        async def record(self, **row: Any) -> None:
            return None

    class Gate:
        async def check(self, **kwargs: Any) -> Any:
            return SimpleNamespace(allowed=True)

    return BackgroundCalls(resolver=Resolver(), spend=Spend(), gate=Gate())


def _client(sent: list[str], content: dict[str, Any]) -> httpx.AsyncClient:
    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


@pytest.fixture(autouse=True)
def custom_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "custom")


async def test_the_proposal_briefs_read_their_model_at_every_tick() -> None:
    # Its reader gives the model and the leads section from one snapshot (brief_settings).
    _takes_a_reader(ProposalBriefWriter, "settings")
    from datetime import UTC, datetime

    from embry0.services.proposals import Proposal

    proposals = [
        Proposal(
            id=f"case:hc-{n}",
            kind="case",
            repo="acme/widgets",
            title=f"Problem {n}",
            summary="…",
            details="",
            action="Run the fix pipeline",
            created_at=datetime(2026, 10, 30, tzinfo=UTC),
            extra={},
        )
        for n in range(2)
    ]
    rows: dict[str, Any] = {}

    class Briefs:
        async def all(self) -> dict[str, Any]:
            return dict(rows)

        async def save(self, pid: str, digest: str, brief: Any, *, model: str) -> None:
            rows[pid] = {"input_hash": digest, "model": model}

        async def prune(self, keep: set[str]) -> int:
            return 0

    async def feed() -> list[Proposal]:
        return proposals

    sent: list[str] = []
    reader = Reader("custom/first")
    writer = ProposalBriefWriter(
        proposals=feed,
        settings=brief_reader(reader),
        briefs=Briefs(),
        calls=_calls(),
        per_tick=1,
        client=_client(sent, BRIEF),
    )
    await writer.tick()
    reader.set("custom/second")
    await writer.tick()
    assert sent == ["first", "second"], "the next tick writes on the model the section names now"
    assert reader.reads == 2


async def test_the_job_briefs_read_their_model_at_every_tick() -> None:
    _takes_a_reader(JobBriefWriter)
    jobs = [{"job_id": f"job-{n}", "repo": "acme/widgets", "task": f"Fix crash {n}"} for n in range(2)]

    class Store:
        async def candidates(self) -> list[dict[str, Any]]:
            return [j for j in jobs if "brief_hash" not in j]

        async def save(self, job_id: str, digest: str, brief: Any, *, model: str) -> None:
            next(j for j in jobs if j["job_id"] == job_id)["brief_hash"] = digest

    sent: list[str] = []
    reader = Reader("custom/first")
    writer = JobBriefWriter(store=Store(), models=reader, calls=_calls(), per_tick=1, client=_client(sent, JOB_BRIEF))
    await writer.tick()
    reader.set("custom/second")
    await writer.tick()
    assert sent == ["first", "second"], "the next tick writes on the model the section names now"


async def test_the_slop_judge_reads_its_model_at_every_tick() -> None:
    from embry0.services.background_scope import Enrollment
    from embry0.services.slop_meter.loop import MeterRead
    from tests.unit.services.slop_meter import test_loop as fakes

    assert "models" not in inspect.signature(SlopMeterLoop).parameters, (
        "the meter reads its judge's model with its enrollment, from one snapshot a tick (MeterRead, #165), "
        "not by a reader of its own"
    )
    records, order = fakes._history(40)
    records["big"] = fakes._record("big", fakes.START + timedelta(days=200), [("src/drop.py", 9_000, 0)])
    order.append("big")
    calls = fakes.FakeCalls()
    loop = fakes._loop(fakes.FakeMirror(records, order), fakes.FakeStore(), per_tick=100, model="", calls=calls)
    reader = Reader("")

    async def read() -> MeterRead:
        return MeterRead(models=await reader(), enrollment=Enrollment(fakes._EveryRepository(), frozenset()))

    loop._read = read  # noqa: SLF001 - the reader the loop was built with, swapped for one the test drives
    await loop.tick()
    assert calls.endpoints == [], "no model: nothing is asked"
    reader.set("custom/judge")
    await loop.tick()
    assert calls.endpoints == [("slop_judge", "custom/judge", fakes.REPO)], (
        "the next tick judges on the model the section names now"
    )


async def test_the_scout_pins_each_job_to_the_model_read_at_its_tick() -> None:
    from tests.unit.services import test_scout as fakes

    assert "models" not in inspect.signature(ScoutLoop).parameters, (
        "the scout reads its model with its section, from one snapshot a tick (TickSettings), not by a reader of its own"
    )
    loop, _, jobs, _, _ = fakes.make_loop(repos=["acme/a", "acme/b"], max_in_flight=4, templates=("garden",))
    models = loop.settings.models
    models.set({"acme/a": "custom/first"})
    first = (await loop.tick())["started"]
    assert [jobs.rows[j]["created_with"]["repo"] for j in first] == ["acme/a"], (
        "a repository with no model is not scouted"
    )
    models.set({"acme/a": "custom/first", "acme/b": "custom/second"})
    second = (await loop.tick())["started"]
    assert [
        (jobs.rows[j]["created_with"]["repo"], jobs.rows[j]["created_with"]["pipeline_config"]["agent_models_override"])
        for j in second
    ] == [("acme/b", {"*": "custom/second"})], "the next tick pins its job to the model its repository reads now"
    assert models.reads == 2, "one read of the section per tick"
