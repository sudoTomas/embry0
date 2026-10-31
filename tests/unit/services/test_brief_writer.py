"""One brief writer (#166): both brief loops on one service, their prompts agent definitions.

- With the seeded prompts each loop sends what it sent before its prompt
  became an agent definition, byte for byte. ``tests/fixtures/brief_requests.json``
  holds what the two writers sent then, recorded by driving their own ticks
  on the tree before the change: each input, its input hash, and the
  request's method, URL, the headers it sets and its body.
- An edited prompt is what the next tick asks with; one that is missing or
  empty reads as the shipped one.
- Each tick reads the settings documents once and its prompt once, whatever
  the number of items.

Imports of the new parts are inside the tests, so this file collects on a
tree without them and each test fails there on its own assertion.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from embry0.services import job_briefs, proposal_briefs
from embry0.services.proposals import Proposal

GOLDEN = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "brief_requests.json").read_text())
#: The headers a brief request sets; the client library adds the rest.
SET_HEADERS = ("authorization", "content-length", "content-type")
GOOD_PROPOSAL = {
    "title": "Fix it",
    "problem": "It is broken.",
    "fix": "Fix it. You get a pull request.",
    "size": "small",
}
GOOD_JOB = {
    "title": "Fix it",
    "what": "It broke.",
    "why": "It matters.",
    "done": "You get a pull request.",
    "size": "small",
}


@pytest.fixture(autouse=True)
def custom_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "custom")


class Store:
    """The settings store: one deployment document naming the briefs' model, counting its snapshots."""

    def __init__(self, model: str = GOLDEN["model"]) -> None:
        self.model = model
        self.reads = 0

    async def snapshot(self) -> Any:
        from embry0.settings.scope import DEPLOYMENT
        from embry0.settings.sections import CATALOG
        from embry0.settings.store import build_snapshot

        self.reads += 1
        data = {"background_models": {"proposal_brief": self.model, "job_brief": self.model}}
        rows = [{"scope": DEPLOYMENT, "data": data, "generation": self.reads, "updated_at": None}]
        return build_snapshot(CATALOG, rows, token=(), log=False)


class Agents:
    """``AgentDefinitionsRepository`` as the brief writer reads it: the built-in rows as the seeder stores them."""

    def __init__(self) -> None:
        from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

        self.rows: dict[str, dict[str, Any]] = {
            t: {"type": t, **BUILTIN_SEED[t], "is_builtin": True} for t in ("proposal-brief", "job-brief")
        }
        self.reads = 0

    async def get(self, agent_type: str) -> dict[str, Any] | None:
        self.reads += 1
        row = self.rows.get(agent_type)
        return dict(row) if row is not None else None


class Resolver:
    async def resolve_llm(self, repo: str, provider: str) -> Any:
        return SimpleNamespace(secret="key-000000000001", metadata={"base_url": "http://models.test/v1"})


class NoSpend:
    async def record(self, **kwargs: Any) -> None:
        return None


class OpenGate:
    async def check(self, **kwargs: Any) -> Any:
        return SimpleNamespace(allowed=True)


def _calls() -> Any:
    from embry0.services.background_models import BackgroundCalls

    return BackgroundCalls(resolver=Resolver(), spend=NoSpend(), gate=OpenGate())


def _client(sent: list[httpx.Request], good: dict[str, str]) -> httpx.AsyncClient:
    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(good)}}]})

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


def _request(request: httpx.Request) -> dict[str, Any]:
    return {
        "method": request.method,
        "url": str(request.url),
        "headers": {k: v for k, v in sorted(request.headers.items()) if k in SET_HEADERS},
        "body": request.content.decode(),
    }


def _system(request: httpx.Request) -> str:
    return str(json.loads(request.content)["messages"][0]["content"])


class Briefs:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def all(self) -> dict[str, dict[str, Any]]:
        return dict(self.rows)

    async def save(self, pid: str, digest: str, brief: Any, *, model: str, prompt_hash: str) -> None:
        self.rows[pid] = {"input_hash": digest, "prompt_hash": prompt_hash, **brief.to_dict(), "model": model}

    async def prune(self, keep: set[str]) -> int:
        return 0


class Jobs:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = [dict(r) for r in rows]

    async def candidates(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows]

    async def save(self, job_id: str, digest: str, brief: Any, *, model: str, prompt_hash: str) -> None:
        row = next(r for r in self.rows if r["job_id"] == job_id)
        row.update(brief_hash=digest, brief_prompt_hash=prompt_hash, brief=brief.to_dict())


def _proposals(cases: list[dict[str, Any]]) -> list[Proposal]:
    return [Proposal(**{**c["input"], "created_at": datetime.fromisoformat(c["input"]["created_at"])}) for c in cases]


def _takes_its_prompt_from_the_agent_definitions() -> None:
    for reader in (proposal_briefs.brief_settings, getattr(job_briefs, "job_brief_settings", None)):
        assert reader is not None and "agents" in inspect.signature(reader).parameters, (
            "each brief loop reads its prompt from its agent definition, through its settings reader (#166)"
        )


def _voice_then_format() -> None:
    for module in (proposal_briefs, job_briefs):
        assert hasattr(module, "VOICE") and hasattr(module, "FORMAT"), (
            "a brief's prompt is a voice, the agent definition's, then the code's format (#166)"
        )


def _system_of(module: Any, voice: str) -> str:
    from embry0.services.brief_writer import system_prompt

    return system_prompt(voice, module.FORMAT)


def _proposal_writer(store: Any, agents: Any, feed: list[Proposal], sent: list[httpx.Request], **kw: Any) -> Any:
    async def proposals() -> list[Proposal]:
        return feed

    return proposal_briefs.ProposalBriefWriter(
        proposals=proposals,
        settings=proposal_briefs.brief_settings(store, agents),
        briefs=kw.pop("briefs", Briefs()),
        calls=_calls(),
        client=_client(sent, GOOD_PROPOSAL),
        **{"per_tick": 100, **kw},
    )


def _job_writer(store: Any, agents: Any, jobs: Jobs, sent: list[httpx.Request], **kw: Any) -> Any:
    return job_briefs.JobBriefWriter(
        store=jobs,
        settings=job_briefs.job_brief_settings(store, agents),
        calls=_calls(),
        client=_client(sent, GOOD_JOB),
        **{"per_tick": 100, **kw},
    )


async def test_the_seeded_prompts_send_byte_for_byte_what_the_writers_sent_before() -> None:
    """The differential test: the same inputs, the same requests and the same input hashes, so no stored brief
    is rewritten when this release starts and no model is asked anything new."""
    _takes_its_prompt_from_the_agent_definitions()
    cases = GOLDEN["proposals"]["cases"]
    sent: list[httpx.Request] = []
    feed = _proposals(cases)
    written = await _proposal_writer(Store(), Agents(), feed, sent).tick()
    assert written == GOLDEN["proposals"]["written_in_order"], "the proposals are briefed in the order they were"
    expected = {c["input"]["id"]: c for c in cases}
    differ = [pid for pid, r in zip(written, sent, strict=True) if _request(r) != expected[pid]["request"]]
    assert differ == [], f"the proposal brief requests differ from what the writer sent before: {differ}"
    hashes = {p.id: proposal_briefs.input_hash(p) for p in feed}
    assert hashes == {pid: c["input_hash"] for pid, c in expected.items()}, "no stored proposal brief goes stale"

    cases = GOLDEN["jobs"]["cases"]
    sent = []
    jobs = [c["input"] for c in cases]
    written = await _job_writer(Store(), Agents(), Jobs(jobs), sent).tick()
    assert written == GOLDEN["jobs"]["written_in_order"], "the jobs are briefed in the order they were"
    expected = {c["input"]["job_id"]: c for c in cases}
    differ = [jid for jid, r in zip(written, sent, strict=True) if _request(r) != expected[jid]["request"]]
    assert differ == [], f"the job brief requests differ from what the writer sent before: {differ}"
    assert {j["job_id"]: job_briefs.input_hash(j) for j in jobs} == {k: c["input_hash"] for k, c in expected.items()}


def test_the_check_of_the_requests_can_fail() -> None:
    """The comparison reads the whole body: one character of the prompt changed is a difference."""
    case = GOLDEN["proposals"]["cases"][0]
    body = json.loads(case["request"]["body"])
    body["messages"][0]["content"] = body["messages"][0]["content"].replace("yes or no", "yes  or no", 1)
    changed = httpx.Request(
        case["request"]["method"],
        case["request"]["url"],
        headers=case["request"]["headers"],
        content=json.dumps(body).encode(),
    )
    assert _request(changed) != case["request"]


GERMAN = "Du erklärst geplante Arbeit der Person, der der Code gehört. Schreibe jeden Text auf Deutsch."
GOOD_GERMAN = {
    "title": "Doppelte Wiederholungen stoppen",
    "problem": "Eine schon gespeicherte E-Mail wird endlos wiederholt.",
    "fix": "Die Wiederholung wird beendet. Du bekommst einen Pull Request.",
    "size": "small",
}


async def test_an_edited_voice_is_what_the_next_tick_asks_with_and_the_format_follows_it() -> None:
    """An operator changes a brief's language or tone in its agent definition (agent_update). The next request
    is that voice, then the code's format, unchanged: a German voice still gets the JSON the parser reads."""
    _takes_its_prompt_from_the_agent_definitions()
    _voice_then_format()
    agents, briefs = Agents(), Briefs()
    agents.rows["proposal-brief"]["system_prompt"] = GERMAN
    sent: list[httpx.Request] = []
    feed = _proposals(GOLDEN["proposals"]["cases"][:1])
    writer = _proposal_writer(Store(), agents, feed, sent, briefs=briefs)
    writer._client = _client(sent, GOOD_GERMAN)  # noqa: SLF001 - a model that answers in German
    assert await writer.tick() == [feed[0].id], "the German answer is read: the format is the code's"
    [system] = [_system(r) for r in sent]
    assert system == _system_of(proposal_briefs, GERMAN), "the request is the voice, then the format"
    assert system.endswith(proposal_briefs.FORMAT) and system.startswith(GERMAN)
    assert briefs.rows[feed[0].id]["title"] == GOOD_GERMAN["title"]

    agents.rows["job-brief"]["system_prompt"] = GERMAN
    sent = []
    jobs = Jobs([GOLDEN["jobs"]["cases"][0]["input"]])
    assert await _job_writer(Store(), agents, jobs, sent).tick() == ["job-1"]
    assert [_system(r) for r in sent] == [_system_of(job_briefs, GERMAN)]


async def test_a_changed_voice_rewrites_the_old_briefs_over_ticks_and_never_hides_one() -> None:
    """Each brief records the prompt it was written with. After an edit the writer rewrites the old briefs, a
    few a tick and after any missing or stale one, while the feed keeps showing each until it is replaced."""
    _takes_its_prompt_from_the_agent_definitions()
    _voice_then_format()
    from tests.fixtures.leads import leads_policy

    agents, briefs = Agents(), Briefs()
    cases = [c for c in GOLDEN["proposals"]["cases"] if not c["input"]["id"].startswith(("ticket:t2", "weed"))][:4]
    feed = _proposals(cases)
    sent: list[httpx.Request] = []
    writer = _proposal_writer(Store(), agents, feed, sent, briefs=briefs, per_tick=2)
    assert len(await writer.tick()) == 2 and len(await writer.tick()) == 2 and await writer.tick() == []
    agents.rows["proposal-brief"]["system_prompt"] = GERMAN
    fresh = _proposals([c for c in GOLDEN["proposals"]["cases"] if c["input"]["id"] == "omen:o1"])
    feed.extend(fresh)
    shown = {p["id"]: p["brief"] for p in proposal_briefs.with_briefs(feed, await briefs.all(), leads_policy())}
    assert all(shown[p.id] is not None for p in feed[:4]), "an edit hides no brief"
    first = await writer.tick()
    assert first[0] == fresh[0].id, f"a proposal with no brief is written before any rewrite: {first}"
    rewritten = set(first[1:])
    for _ in range(3):
        shown = {p["id"]: p["brief"] for p in proposal_briefs.with_briefs(feed, await briefs.all(), leads_policy())}
        assert all(shown[p.id] is not None for p in feed), "every brief stays shown while the rewrite runs"
        step = await writer.tick()
        if not step:
            break
        rewritten |= set(step)
    assert rewritten == {p.id for p in feed[:4]}, f"every old brief is rewritten with the new voice, once: {rewritten}"
    assert await writer.tick() == [], "and then nothing is due"
    assert {_system(r) for r in sent[-5:]} == {_system_of(proposal_briefs, GERMAN)}

    agents = Agents()
    jobs = Jobs([c["input"] for c in GOLDEN["jobs"]["cases"][:3]])
    sent = []
    job_writer = _job_writer(Store(), agents, jobs, sent, per_tick=2)
    assert len(await job_writer.tick()) == 2 and len(await job_writer.tick()) == 1
    agents.rows["job-brief"]["system_prompt"] = GERMAN
    assert len(await job_writer.tick()) == 2 and len(await job_writer.tick()) == 1 and await job_writer.tick() == []
    assert all(r["brief_hash"] == job_briefs.input_hash(r) for r in jobs.rows), "readers still match each brief"


#: Voices in ordinary prose, each accepted: none defines one of the answer's keys.
PROSE_VOICES = {
    "German": "Du erklärst geplante Arbeit auf Deutsch, knapp und freundlich, für die Person, der der Code gehört.",
    "prose naming JSON": "Write for engineers who read JSON logs all day: keep it short and avoid jargon.",
    "prose avoiding JSON": "Avoid jargon such as JSON, YAML or HTTP status codes; say what happens.",
    "a glossary line": 'Write in plain Spanish.\n"Lead": say "hallazgo" instead.',
    "a quoted word and a colon": 'Say "yes": it is fine.',
    "a capitalised key word": 'Spell out "Title": never abbreviate it.',
}
#: Voices that define one of the answer's own keys, a quoted key and a colon, each refused.
KEY_VOICES = {
    "a key line": 'Answer in German.\n- "title": a short name',
    "an inline object": 'Answer like {"title": "...", "size": "small"} in German.',
    "single quotes": "Answer in German.\n'what': one sentence",
    "typographic quotes": "Antworte auf Deutsch.\n\u201cdone\u201d: ein Satz",
}


@pytest.mark.parametrize("name", sorted(PROSE_VOICES))
def test_a_voice_in_ordinary_prose_is_accepted(name: str) -> None:
    """A voice sets language, tone and style in its own words: naming JSON, quoting a word or writing a
    glossary is not saying the answer's format."""
    assert _voice_problem(PROSE_VOICES[name]) is None, f"{name} is ordinary prose, not the answer's format"
    for shipped in (proposal_briefs.VOICE, job_briefs.VOICE, GERMAN):
        assert _voice_problem(shipped) is None


@pytest.mark.parametrize("name", sorted(KEY_VOICES))
def test_a_voice_that_defines_one_of_the_answer_s_keys_is_refused(name: str) -> None:
    """What can be told reliably: a voice that defines one of the keys the format sets, as a quoted key and a
    colon, is trying to say the answer's format. Anything else it says, the format that follows it overrides."""
    problem = _voice_problem(KEY_VOICES[name])
    assert problem, f"{name}: a voice that defines one of the answer's keys says its format"
    assert "German" not in problem and "Deutsch" not in problem, "the refusal never repeats the voice"


def _voice_problem(voice: str) -> str | None:
    """The check the agents API holds a voice to, against the keys the two formats set, read from their schemas."""
    from embry0.services.brief_writer import voice_problem

    assert "keys" in inspect.signature(voice_problem).parameters, (
        "the voice check refuses only a voice that defines one of the answer's own keys, so it reads them"
    )
    keys = (*proposal_briefs.BRIEF_SCHEMA["properties"], *job_briefs.JOB_BRIEF_SCHEMA["properties"])
    return voice_problem(voice, keys)


@pytest.mark.parametrize("row", [None, "", "  \n "], ids=["missing", "empty", "blank"])
async def test_a_missing_or_empty_prompt_reads_as_the_shipped_one(row: str | None) -> None:
    """A definition someone deleted or cleared never sends a model no instructions."""
    _takes_its_prompt_from_the_agent_definitions()
    agents = Agents()
    for agent_type in ("proposal-brief", "job-brief"):
        if row is None:
            del agents.rows[agent_type]
        else:
            agents.rows[agent_type]["system_prompt"] = row
    sent: list[httpx.Request] = []
    await _proposal_writer(Store(), agents, _proposals(GOLDEN["proposals"]["cases"][:1]), sent).tick()
    await _job_writer(Store(), agents, Jobs([GOLDEN["jobs"]["cases"][0]["input"]]), sent).tick()
    assert [_system(r) for r in sent] == [proposal_briefs.SYSTEM_PROMPT, job_briefs.SYSTEM_PROMPT]


@pytest.mark.parametrize("loop", ["proposals", "jobs"])
async def test_a_brief_written_before_the_voices_is_not_due_when_a_release_rewords_the_shipped_voice(
    loop: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A brief written before #166 stores no prompt hash. That stands for the prompt it was written with,
    recorded, not for the voice the code ships now: a later release that rewords ``VOICE`` leaves the
    deployment's row, and so the prompt, as it was, and none of those briefs is due."""
    import dataclasses

    from embry0.services.brief_writer import BriefWriter, prompt_hash, system_prompt

    module: Any = proposal_briefs if loop == "proposals" else job_briefs
    reader = proposal_briefs.brief_settings if loop == "proposals" else job_briefs.job_brief_settings
    # The rows as this release seeded them, built before the reword, whatever has been imported so far.
    agents, cleared = Agents(), Agents()
    cleared.rows[module.AGENT]["system_prompt"] = ""
    seeded = module.VOICE
    reworded = f"{seeded} Keep it short."
    monkeypatch.setattr(module, "VOICE", reworded)
    if loop == "proposals":
        briefs: Any = proposal_briefs.ProposalBriefs(proposals=None, store=None)  # type: ignore[arg-type]
        items: list[Any] = _proposals(GOLDEN["proposals"]["cases"])
    else:
        briefs = job_briefs.JobBriefs(store=None)
        items = [c["input"] for c in GOLDEN["jobs"]["cases"]]
    # The later release builds its loop with the reworded voice wherever the loop carries the shipped one.
    briefs = dataclasses.replace(
        briefs, **{f.name: reworded for f in dataclasses.fields(briefs) if getattr(briefs, f.name) == seeded}
    )
    writer: Any = BriefWriter(briefs=briefs, settings=None, calls=None)  # type: ignore[arg-type]
    unrecorded = [(item, briefs.input_hash(item), None) for item in items]

    read = await reader(Store(), agents)()
    assert read.voice == seeded, "the deployment's row still holds the voice it was seeded with"
    due = writer.due(unrecorded, prompt_hash(system_prompt(read.voice, briefs.format)))
    assert due == [], f"a reworded VOICE makes no brief written before the voices due: {len(due)} of {len(items)}"

    recorded = {json.loads(c["request"]["body"])["messages"][0]["content"] for c in GOLDEN[loop]["cases"]}
    assert len(recorded) == 1, "every brief of a loop was asked with one prompt"
    assert getattr(briefs, "unrecorded_prompt_hash", None) == prompt_hash(recorded.pop()), (
        "a missing prompt hash stands for the prompt recorded before the voices, a literal, not the shipped voice"
    )

    read = await reader(Store(), cleared)()
    assert read.voice == reworded, "a cleared row reads as the voice the code ships now"
    due = writer.due(unrecorded, prompt_hash(system_prompt(read.voice, briefs.format)))
    assert len(due) == len(items), "a voice other than the one they were written with rewrites them"


async def test_a_row_of_the_deployment_s_own_under_a_voice_s_name_is_never_read_as_the_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment may have made an agent of its own called ``proposal-brief`` or ``job-brief`` before the
    voices were seeded: the seeder never overwrites it, and its prompt is a job agent's, not a voice. The loop
    writes with its shipped voice and says so once."""
    from structlog.testing import capture_logs

    from embry0.services import brief_writer

    monkeypatch.setattr(brief_writer, "_SHIPPED_LOGGED", set())
    own = "You are a careful developer. Use tools to fix the bug."
    agents = Agents()
    for agent_type in ("proposal-brief", "job-brief"):
        agents.rows[agent_type].update(system_prompt=own, is_builtin=False, runs_in_jobs=True)
    with capture_logs() as logs:
        for _ in range(2):
            proposal = await proposal_briefs.brief_settings(Store(), agents)()
            job = await job_briefs.job_brief_settings(Store(), agents)()
            assert (proposal.voice, job.voice) == (proposal_briefs.VOICE, job_briefs.VOICE), (
                "an agent the deployment made under a voice's name is not the voice: the shipped one is read"
            )
    taken = [e for e in logs if e.get("event") == "brief_voice_name_taken"]
    assert sorted(e["agent_type"] for e in taken) == ["job-brief", "proposal-brief"], (
        f"the collision is logged once per loop: {logs}"
    )
    assert own not in str(logs), "the log never repeats the row's prompt"

    agents = Agents()
    agents.rows["proposal-brief"].update(system_prompt=own, runs_in_jobs=True)
    proposal = await proposal_briefs.brief_settings(Store(), agents)()
    assert proposal.voice == proposal_briefs.VOICE, "only a built-in row that runs in no job is a voice"


async def test_each_tick_reads_its_settings_and_its_prompt_once() -> None:
    """One snapshot of the settings documents and one read of the agent definition a tick, whatever the number
    of items: a write between two reads can never hand one tick two versions."""
    _takes_its_prompt_from_the_agent_definitions()
    store, agents = Store(), Agents()
    sent: list[httpx.Request] = []
    await _proposal_writer(store, agents, _proposals(GOLDEN["proposals"]["cases"]), sent).tick()
    assert (store.reads, agents.reads, len(sent)) == (1, 1, len(GOLDEN["proposals"]["cases"]))
    store, agents = Store(), Agents()
    await _job_writer(store, agents, Jobs([c["input"] for c in GOLDEN["jobs"]["cases"]]), sent).tick()
    assert (store.reads, agents.reads) == (1, 1), f"the job briefs read once a tick: {store.reads}, {agents.reads}"


async def test_each_loop_s_calls_are_metered_under_its_own_feature() -> None:
    """Spend stays metered through BackgroundCalls (#164): each call is recorded under its loop's key of
    ``background_models``, with the repository it was about."""
    _takes_its_prompt_from_the_agent_definitions()
    from embry0.services.background_models import BackgroundCalls

    class Spend:
        def __init__(self) -> None:
            self.rows: list[dict[str, Any]] = []

        async def record(self, **row: Any) -> None:
            self.rows.append(row)

    spend = Spend()
    calls = BackgroundCalls(resolver=Resolver(), spend=spend, gate=OpenGate())
    sent: list[httpx.Request] = []
    feed = _proposals(GOLDEN["proposals"]["cases"][:1])
    writer = _proposal_writer(Store(), Agents(), feed, sent)
    writer._calls = calls  # noqa: SLF001 - the metered path, with a spend the test reads
    await writer.tick()
    jobs = Jobs([GOLDEN["jobs"]["cases"][0]["input"]])
    job_writer = _job_writer(Store(), Agents(), jobs, sent)
    job_writer._calls = calls  # noqa: SLF001
    await job_writer.tick()
    assert [(r["feature"], r["repo"]) for r in spend.rows] == [
        ("proposal_brief", "acme/widgets"),
        ("job_brief", "acme/widgets"),
    ], f"each call is recorded under its loop's feature: {spend.rows}"


def test_both_loops_are_the_one_brief_writer() -> None:
    """One service: each loop is the brief writer with its own briefs, cadence and per-tick count, not a copy
    of its loop."""
    from embry0.services import brief_writer

    for loop in (proposal_briefs.ProposalBriefWriter, job_briefs.JobBriefWriter):
        assert issubclass(loop, brief_writer.BriefWriter), f"{loop.__name__} is the one brief writer"
        assert loop.tick is brief_writer.BriefWriter.tick and loop.due is brief_writer.BriefWriter.due, (
            f"{loop.__name__} writes with the brief writer's own tick"
        )
        for parameter in ("per_tick", "interval_seconds"):
            assert parameter in inspect.signature(loop).parameters, f"{loop.__name__} keeps its own {parameter}"


async def test_a_job_s_provider_check_reads_only_the_definitions_a_job_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A brief writer's voice runs in no job (``runs_in_jobs`` false): a model set on it must never make a job
    wait for that model's provider account."""
    from embry0.services import _executor_run
    from embry0.storage.repositories import agent_definitions

    rows = [
        {"type": "developer", "model": "claude-opus-5", "runs_in_jobs": True},
        {"type": "proposal-brief", "model": "grok-4.5", "runs_in_jobs": False},
    ]

    async def list_all(self: Any) -> list[dict[str, Any]]:
        return rows

    monkeypatch.setattr(agent_definitions.AgentDefinitionsRepository, "list_all", list_all)
    read = await _executor_run._agent_definitions_for_precheck(SimpleNamespace(_db=object()))  # type: ignore[arg-type]
    assert [r["type"] for r in read] == ["developer"], f"a job's providers come from the agents it runs: {read}"
    seeds = await _executor_run._agent_definitions_for_precheck(SimpleNamespace(_db=None))  # type: ignore[arg-type]
    shipped = {seed["system_prompt"] for seed in seeds}
    assert not {proposal_briefs.VOICE, job_briefs.VOICE} & shipped, "nor, without a database, from the seeds"
