"""The meter's loop reads the ``slop_meter`` section every look (#169): a change applies with no restart.

On the fakes of test_loop.py: no git, no network, no database. Each score
records the configuration version its repository read; a change to what a
score is computed from rescores at the next look instead of at the next
interval; ``enabled`` and ``interval_hours`` pace the schedule; each
organization's forge budget applies to the credential it reads with; and the
section's bots, AI agents and judge guidance reach the scoring.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import pytest

from embry0.services.background_scope import Enrollment, Registered
from embry0.services.slop_meter import loop as loop_mod
from embry0.services.slop_meter.loop import MeterRead, SlopMeterLoop
from embry0.settings.sections import CATALOG
from tests.unit.services.slop_meter import test_loop as fakes

REPO = fakes.REPO
NOW = fakes.START + timedelta(days=60)


def _settings(values: Mapping[str, Any]) -> Any:
    """The section as a repository reads it from a document writing ``values`` (weights over the defaults)."""
    from embry0.settings.store import build_snapshot

    section = CATALOG.get("slop_meter")
    if section is None:
        pytest.fail("the settings document has no slop_meter section: the meter reads no settings")
    rows = [{"scope": "deployment", "data": {"slop_meter": dict(values)}, "generation": 1, "updated_at": None}]
    return build_snapshot(CATALOG, rows, token=(), log=False).scoped(section).repo(REPO)


class Section:
    """The ``slop_meter`` section a test changes between looks: the deployment's values, and per owner."""

    def __init__(self, values: Mapping[str, Any] | None = None, **owners: Mapping[str, Any]) -> None:
        self.values = dict(values or {})
        self.owners = {owner: dict(v) for owner, v in owners.items()}

    def deployment(self) -> Any:
        return _settings(self.values)

    def repo(self, repo: str | None) -> Any:
        owner = (repo or "").split("/", 1)[0]
        return _settings({**self.values, **self.owners.get(owner, {})})


def _reader(section: Section, model: str = "") -> Any:
    async def read() -> MeterRead:
        return MeterRead(
            models=lambda repo: model,
            enrollment=Enrollment(fakes._EveryRepository(), frozenset()),
            meter=section,
        )

    return read


def _loop(
    section: Section,
    store: fakes.FakeStore,
    mirror: fakes.FakeMirror,
    clock: list[datetime],
    *,
    repos: tuple[str, ...] = (REPO,),
    tokens: Mapping[str, str] | None = None,
    forges: Mapping[str, Any] | None = None,
    model: str = "",
    calls: Any = None,
) -> SlopMeterLoop:
    _settings({})  # fails with its own message on a tree with no slop_meter section

    async def registered() -> Registered:
        return Registered(active=repos)

    async def token_for(repo: str) -> tuple[str, str, str]:
        return ("github", (tokens or {}).get(repo, "token-1"), "")

    async def client_for(repo: str) -> Any:
        return (forges or {}).get(repo)

    async def bots(repo: str) -> tuple[str, ...]:
        return ()

    return SlopMeterLoop(
        repos=registered,
        exits=fakes.NoExits(),
        audit=fakes.no_audit,
        read=_reader(section, model),
        store=store,
        mirror=mirror,
        token_for=token_for,
        client_for=client_for,
        bots=bots,
        calls=calls if calls is not None else fakes.FakeCalls(),
        limits=loop_mod.SlopMeterLimits(commits_per_tick=500, event_pages=100),
        clock=lambda: clock[0],
    )


def _sql_history() -> tuple[dict[str, str], list[str]]:
    order = [f"q{i:03d}" for i in range(30)]
    records = {
        sha: fakes._record(sha, fakes.START + timedelta(hours=5 * i), [("db/q.sql", 400, 0), ("src/a.py", 10, 0)])
        for i, sha in enumerate(order)
    }
    return records, order


def _version(values: Mapping[str, Any]) -> str:
    from embry0.settings.sections.slop_meter import config_version

    return config_version(_settings(values))


async def test_a_score_records_the_configuration_version_of_its_repository_s_settings() -> None:
    records, order = _sql_history()
    store = fakes.FakeStore()
    loop = _loop(Section({"weights": {"*.sql": 1.0}}), store, fakes.FakeMirror(records, order), [NOW])
    assert await loop.tick() is False
    assert store.versions[REPO] == _version({"weights": {"*.sql": 1.0}}) != _version({}), (
        f"the score records the version of the settings it was computed with: {store.versions}"
    )


async def test_a_changed_weight_rescores_at_the_next_look_and_records_the_new_version() -> None:
    records, order = _sql_history()
    store, clock = fakes.FakeStore(), [NOW]
    section = Section()
    loop = _loop(section, store, fakes.FakeMirror(records, order), clock)
    assert await loop.scheduled() is True, "the first look runs at once"
    before = store.stored[REPO][""]
    assert store.versions[REPO] == _version({})
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() is False, "nothing changed and the day has not passed: no run"
    section.values = {"weights": {"*.sql": 0.0}}
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() is True, "a weight changed: the next look rescores, not tomorrow's run"
    after = store.stored[REPO][""]
    assert store.versions[REPO] == _version({"weights": {"*.sql": 0.0}}), "and records the new version"
    assert sum(w.cost for w in after.weeks) < sum(w.cost for w in before.weeks), (
        "SQL weighing nothing lowers the attention cost the score is computed from"
    )
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() is False, "once rescored, the next look waits for the interval again"
    assert 0 < loop.wait_seconds() <= loop_mod.LOOK_SECONDS


async def test_a_key_no_score_is_computed_from_runs_nothing_early() -> None:
    records, order = _sql_history()
    store, clock = fakes.FakeStore(), [NOW]
    section = Section()
    loop = _loop(section, store, fakes.FakeMirror(records, order), clock)
    await loop.scheduled()
    section.values = {"api_calls_per_tick": 10, "judge_prompt": "Judge it."}
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() is False, "the forge budget and the judge's guidance rescore nothing"


async def test_off_reads_nothing_and_turned_on_it_reads_at_the_next_look() -> None:
    records, order = _sql_history()
    store, mirror, clock = fakes.FakeStore(), fakes.FakeMirror(records, order), [NOW]
    section = Section({"enabled": False})
    loop = _loop(section, store, mirror, clock)
    assert await loop.scheduled() is True, "off, the first look still runs: it removes what left the scope"
    assert mirror.synced == [] and store.stored == {}, "and reads and scores nothing"
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() is False
    section.values = {}
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() is True, "turned on, the next look reads, not a day after the last removal"
    assert mirror.synced == [(REPO, "github")] and REPO in store.stored


async def test_the_interval_is_the_section_s() -> None:
    records, order = _sql_history()
    store, clock = fakes.FakeStore(), [NOW]
    loop = _loop(Section({"interval_hours": 1}), store, fakes.FakeMirror(records, order), clock)
    await loop.scheduled()
    clock[0] += timedelta(minutes=59)
    assert await loop.scheduled() is False and loop.wait_seconds() == pytest.approx(60), "due in a minute"
    clock[0] += timedelta(minutes=2)
    assert await loop.scheduled() is True, "an hour after the last run ended"


async def test_each_organization_s_forge_budget_applies_to_its_credential_and_a_shared_one_takes_the_strictest() -> (
    None
):
    acme, globex = "acme/app", "globex/app"
    records, order = fakes._history(2)
    section = Section({}, acme={"api_calls_per_tick": 3, "rate_reserve": 0}, globex={"api_calls_per_tick": 12})

    def forges() -> dict[str, Any]:
        return {name: fakes.FakeForge(comment_pages=50, repo=name) for name in (acme, globex)}

    apart = forges()
    tokens = {acme: "token-acme", globex: "token-globex"}
    loop = _loop(section, fakes.FakeStore(), fakes.FakeMirror(records, order), [NOW], repos=(acme, globex))
    loop._client_for = _clients(apart)  # noqa: SLF001
    loop._token_for = _tokens(tokens)  # noqa: SLF001
    await loop.tick()
    assert (len(apart[acme].calls), len(apart[globex].calls)) == (3, 12), (
        "each organization's credential has the calls its organization allows: "
        f"{len(apart[acme].calls)} and {len(apart[globex].calls)}"
    )
    shared = forges()
    loop = _loop(section, fakes.FakeStore(), fakes.FakeMirror(records, order), [NOW], repos=(acme, globex))
    loop._client_for = _clients(shared)  # noqa: SLF001
    await loop.tick()
    assert len(shared[acme].calls) + len(shared[globex].calls) == 3, (
        "read with one credential, the two share the fewer calls either organization allows"
    )


def _clients(forges: Mapping[str, Any]) -> Any:
    async def client_for(repo: str) -> Any:
        return forges.get(repo)

    return client_for


def _tokens(tokens: Mapping[str, str]) -> Any:
    async def token_for(repo: str) -> tuple[str, str, str]:
        return ("github", tokens[repo], "")

    return token_for


async def test_the_section_s_bots_and_ai_agents_are_bots_when_scored(monkeypatch: pytest.MonkeyPatch) -> None:
    def record(sha: str, hours: int, email: str, body: str = "Work") -> str:
        at = fakes.START + timedelta(hours=hours)
        return f"\x1e{sha}\x1f{at.isoformat()}\x1fSomeone\x1f{email}\x1f{body}\n\x1f\n20\t0\tsrc/a.py\n"

    records = {
        "c0": record("c0", 0, "dev@example.com", "x\n\nCo-authored-by: Helper <helper@tools.example>"),
        "c1": record("c1", 3, "release@acme.example"),
        "c2": record("c2", 6, "helper@tools.example"),
    }
    seen: list[Any] = []
    real = loop_mod.compute

    def spy(inputs: Any) -> Any:
        seen.append(inputs)
        return real(inputs)

    monkeypatch.setattr(loop_mod, "compute", spy)
    section = Section({}, acme={"bots": ["Release@Acme.example"], "ai_agents": ["helper@"]})
    loop = _loop(section, fakes.FakeStore(), fakes.FakeMirror(records, ["c0", "c1", "c2"]), [NOW], repos=(REPO,))
    await loop.tick()
    [inputs] = seen
    assert {c.sha for c in inputs.commits if c.bot} == {"c1", "c2"}, "the owner's bot and the AI agent author"
    assert inputs.commits[0].ai_coauthor and inputs.commits[0].coauthors == (), "the agent co-wrote, not a person"
    assert inputs.settings.bots == ("Release@Acme.example",), "the repository is scored with its owner's section"


async def test_the_judge_hears_the_repository_s_guidance(monkeypatch: pytest.MonkeyPatch) -> None:
    records, order = fakes._history(40)
    records["big"] = fakes._record("big", fakes.START + timedelta(days=200), [("src/drop.py", 9_000, 0)])
    order.append("big")
    heard: list[str] = []

    async def judge(repo: str, outlier: Any, ep: Any, client: Any, calls: Any, *, guidance: str) -> tuple[bool, str]:
        heard.append(guidance)
        return False, "a vendored parser"

    monkeypatch.setattr("embry0.services.slop_meter.judge.judge", judge)
    section = Section({}, acme={"judge_prompt": "Imports under third_party are never slop."})
    loop = _loop(section, fakes.FakeStore(), fakes.FakeMirror(records, order), [NOW], model="custom/local")
    await loop.tick()
    assert heard == ["Imports under third_party are never slop."], f"the owner's guidance reached the judge: {heard}"
