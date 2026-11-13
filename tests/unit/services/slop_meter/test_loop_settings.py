"""The meter's loop reads the ``slop_meter`` section every look (#169): a change applies with no restart.

On the fakes of test_loop.py: no git, no network, no database. Each score
records the configuration version its repository read; a change to what a
score is computed from rescores, at the next look, only the repositories it
touches, from what was read, with no fetch and no forge call; ``enabled`` and
``interval_hours`` pace the schedule; a credential's forge budget is the
deployment's, an organization's values narrowing only its own share; and the
section's bots, AI agents and judge guidance reach the scoring, the weighing
off the event loop.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

import pytest

from embry0.services.areas import Area
from embry0.services.background_scope import Enrollment, Registered
from embry0.services.slop_meter import loop as loop_mod
from embry0.services.slop_meter.loop import MeterRead, SlopMeterLoop
from embry0.settings.sections import CATALOG
from tests.fixtures.areas import no_areas_of
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
    areas: Mapping[str, list[Area]] | None = None,
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
        areas_of=_areas_reader(areas) if areas is not None else no_areas_of,
        clock=lambda: clock[0],
    )


def _areas_reader(areas: Mapping[str, list[Area]]) -> Any:
    """An ``areas_of`` that reads ``areas`` as it stands at each call, so a test can change a repository's areas."""

    async def read(repos: Iterable[str]) -> dict[str, list[Area]]:
        return {repo.lower(): list(areas.get(repo.lower(), [])) for repo in repos}

    return read


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
    assert store.versions_of[REPO] == _version({"weights": {"*.sql": 1.0}}) != _version({}), (
        f"the score records the version of the settings it was computed with: {store.versions_of}"
    )


class Mirrors(fakes.FakeMirror):
    """One fake history for every repository; ``synced`` names each fetch."""


async def test_a_changed_weight_rescores_only_the_repository_it_touches_with_no_fetch_and_no_forge_call() -> None:
    acme, globex = "acme/web", "globex/web"
    records, order = _sql_history()
    forges = {name: fakes.FakeForge(repo=name) for name in (acme, globex)}
    store, mirror, clock = fakes.FakeStore(), Mirrors(records, order), [NOW]
    section = Section()
    loop = _loop(section, store, mirror, clock, repos=(acme, globex), forges=forges)
    assert await loop.scheduled() == "run", "the first look runs at once"
    before = {repo: store.stored[repo][""] for repo in (acme, globex)}
    first = dict(store.versions_of)
    fetched, called = len(mirror.synced), {name: len(forge.calls) for name, forge in forges.items()}
    assert fetched == 2 and all(called.values()), "the run fetched both and read both forges"

    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() is None, "nothing changed and the day has not passed: nothing runs"
    section.owners["acme"] = {"weights": {"*.sql": 0.0}}
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() == "rescore", "acme's weight changed: the next look rescores"
    assert len(mirror.synced) == fetched, "with no fetch"
    assert {name: len(forge.calls) for name, forge in forges.items()} == called, "and no forge call, for either"
    assert store.versions_of[acme] == _version({"weights": {"*.sql": 0.0}}) != first[acme], "acme records its new one"
    assert store.versions_of[globex] == first[globex] and store.stored[globex][""] is before[globex], (
        "globex, which the change does not touch, is not rescored"
    )
    assert sum(w.cost for w in store.stored[acme][""].weeks) < sum(w.cost for w in before[acme].weeks), (
        "SQL weighing nothing lowers acme's attention cost"
    )
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() is None, "once rescored, the next look does nothing"
    assert 0 < loop.wait_seconds() <= loop_mod.LOOK_SECONDS
    clock[0] = NOW + timedelta(hours=24, minutes=1)
    assert await loop.scheduled() == "run", "the full run keeps its interval from the last one"
    assert len(mirror.synced) == fetched + 2


async def test_a_change_to_a_repository_s_areas_rescores_it_alone_with_no_fetch() -> None:
    """Spec 2026-11-10: a repository's areas are what its area meters are scored by, so a change to them rescores
    it at the next look, as a change of its settings does; one whose areas are the same is not rescored."""
    acme, globex = "acme/web", "globex/web"
    records, order = _sql_history()
    forges = {name: fakes.FakeForge(repo=name) for name in (acme, globex)}
    store, mirror, clock = fakes.FakeStore(), Mirrors(records, order), [NOW]
    areas: dict[str, list[Area]] = {globex: [Area("Db", ("db/**",))]}
    loop = _loop(Section(), store, mirror, clock, repos=(acme, globex), forges=forges, areas=areas)
    assert await loop.scheduled() == "run"
    assert set(store.stored[acme]) == {""} and set(store.stored[globex]) == {"", "Db"}
    first = dict(store.versions_of)
    assert first[acme] == _version({}) and first[globex].startswith(_version({}) + "+"), first
    fetched = len(mirror.synced)

    areas[acme] = [Area("Db", ("db/**",)), Area("Src", ("src/**",))]
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() == "rescore", "acme's areas changed: the next look rescores"
    assert len(mirror.synced) == fetched, "with no fetch"
    assert set(store.stored[acme]) == {"", "Db", "Src"}, "acme is scored by the areas it names now"
    assert store.versions_of[acme] != first[acme] and store.versions_of[globex] == first[globex]
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() is None, "once rescored, the next look does nothing"


async def test_a_repository_whose_rescore_fails_is_not_tried_again_every_look() -> None:
    records, order = _sql_history()
    store, mirror, clock = fakes.FakeStore(), fakes.FakeMirror(records, order), [NOW]
    section = Section()
    loop = _loop(section, store, mirror, clock)
    await loop.scheduled()
    tries: list[str] = []

    async def failing(repo: str, name: str) -> str | None:
        tries.append(repo)
        raise RuntimeError("the mirror cannot be read")

    mirror.read = failing  # type: ignore[method-assign]
    section.values = {"weights": {"*.sql": 0.0}}
    for _ in range(3):
        clock[0] += timedelta(minutes=5)
        await loop.scheduled()
    assert len(tries) == 1, f"one try per version, not one a look: {len(tries)} tries"
    assert "the mirror cannot be read" in store.states[REPO]["error"], "and the failure is recorded"


async def test_a_key_no_score_is_computed_from_rescores_nothing() -> None:
    records, order = _sql_history()
    store, clock = fakes.FakeStore(), [NOW]
    section = Section()
    loop = _loop(section, store, fakes.FakeMirror(records, order), clock)
    await loop.scheduled()
    section.values = {"api_calls_per_tick": 10, "judge_prompt": "Judge it."}
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() is None, "the forge budget and the judge's guidance rescore nothing"


async def test_off_reads_nothing_and_turned_on_it_reads_at_the_next_look() -> None:
    records, order = _sql_history()
    store, mirror, clock = fakes.FakeStore(), fakes.FakeMirror(records, order), [NOW]
    section = Section({"enabled": False})
    loop = _loop(section, store, mirror, clock)
    assert await loop.scheduled() == "run", "off, the first look still runs: it removes what left the scope"
    assert mirror.synced == [] and store.stored == {}, "and reads and scores nothing"
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() is None
    section.values = {}
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() == "run", "turned on, the next look reads, not a day after the last removal"
    assert mirror.synced == [(REPO, "github")] and REPO in store.stored


async def test_the_interval_is_the_section_s() -> None:
    records, order = _sql_history()
    store, clock = fakes.FakeStore(), [NOW]
    loop = _loop(Section({"interval_hours": 1}), store, fakes.FakeMirror(records, order), clock)
    await loop.scheduled()
    clock[0] += timedelta(minutes=59)
    assert await loop.scheduled() is None and loop.wait_seconds() == pytest.approx(60), "due in a minute"
    clock[0] += timedelta(minutes=2)
    assert await loop.scheduled() == "run", "an hour after the last run ended"


def _clients(forges: Mapping[str, Any]) -> Any:
    async def client_for(repo: str) -> Any:
        return forges.get(repo)

    return client_for


def _tokens(tokens: Mapping[str, str]) -> Any:
    async def token_for(repo: str) -> tuple[str, str, str]:
        return ("github", tokens[repo], "")

    return token_for


def _calls(forges: Mapping[str, Any]) -> dict[str, int]:
    return {name: len(forge.calls) for name, forge in forges.items()}


async def test_an_organization_s_budget_narrows_only_its_own_share_of_a_credential() -> None:
    acme, globex = "acme/app", "globex/app"
    records, order = fakes._history(2)

    def forges(remaining: int = 4_000) -> dict[str, Any]:
        return {name: fakes.FakeForge(comment_pages=50, repo=name, remaining=remaining) for name in (acme, globex)}

    async def read_with(section: Section, found: dict[str, Any], tokens: Mapping[str, str] | None = None) -> None:
        loop = _loop(section, fakes.FakeStore(), fakes.FakeMirror(records, order), [NOW], repos=(acme, globex))
        loop._client_for = _clients(found)  # noqa: SLF001
        if tokens is not None:
            loop._token_for = _tokens(tokens)  # noqa: SLF001
        await loop.tick()

    deployment = {"api_calls_per_tick": 20, "rate_reserve": 0}
    shared = forges()
    await read_with(Section(deployment, acme={"api_calls_per_tick": 0}), shared)
    assert _calls(shared) == {acme: 0, globex: 20}, (
        f"acme's 0 stops acme's reads alone; globex, on the same credential, reads the deployment's 20: {_calls(shared)}"
    )
    shared = forges()
    await read_with(Section(deployment, acme={"rate_reserve": 100_000}), shared)
    assert _calls(shared)[acme] == 1 and _calls(shared)[globex] == 19, (
        f"acme's large reserve stops acme after the first answer names the limit; globex goes on: {_calls(shared)}"
    )
    shared = forges()
    await read_with(Section(deployment, acme={"api_calls_per_tick": 5}, globex={"api_calls_per_tick": 500}), shared)
    assert _calls(shared) == {acme: 5, globex: 15}, (
        f"an organization's value narrows its share and never widens it past the credential's 20: {_calls(shared)}"
    )
    apart = forges()
    tokens = {acme: "token-acme", globex: "token-globex"}
    await read_with(Section(deployment, acme={"api_calls_per_tick": 3}), apart, tokens)
    assert _calls(apart) == {acme: 3, globex: 20}, (
        f"on two credentials, each has its own budget (#185): {_calls(apart)}"
    )


@pytest.mark.parametrize(
    "narrower", [{"api_calls_per_tick": 0}, {"api_calls_per_tick": 3}, {"rate_reserve": 100_000}], ids=str
)
async def test_an_organization_that_reads_less_never_holds_the_meter_on_catch_up(narrower: dict[str, int]) -> None:
    """A repository stopped only by its organization's narrower share waits for the next run, at the interval: six
    hours of looks at a 24-hour interval are one full run, and one fetch of the other organization."""
    acme, globex = "acme/app", "globex/app"
    records, order = fakes._history(2)
    forges = {name: fakes.FakeForge(comment_pages=1, repo=name) for name in (acme, globex)}
    store, mirror, clock = fakes.FakeStore(), fakes.FakeMirror(records, order), [NOW]
    loop = _loop(Section({}, acme=narrower), store, mirror, clock, repos=(acme, globex), forges=forges)
    runs = []
    for _ in range(72):
        runs.append(await loop.scheduled())
        clock[0] += timedelta(minutes=5)
    assert runs.count("run") == 1, f"one full run in six hours, not one every catch-up: {runs.count('run')}"
    assert [repo for repo, _ in mirror.synced].count(globex) == 1, "globex is fetched once"
    assert globex in store.stored, "and scored"


def _narrowed(share: int, store: fakes.FakeStore | None = None) -> tuple[SlopMeterLoop, fakes.FakeStore, Any]:
    acme, globex = "acme/app", "globex/app"
    records, order = fakes._history(5)
    forges = {name: fakes.FakeForge(comment_pages=1, repo=name) for name in (acme, globex)}
    store = store if store is not None else fakes.FakeStore()
    section = Section({}, acme={"api_calls_per_tick": share})
    loop = _loop(section, store, fakes.FakeMirror(records, order), [NOW], repos=(acme, globex), forges=forges)
    return loop, store, section


#: The calls one whole read of a repository takes on a FakeForge: the two comment listings, the pull requests,
#: the reviews of its two pull requests and the two logins its events name.
WHOLE_READ = 7


@pytest.mark.parametrize("share", [1, 2, 3])
async def test_a_share_too_small_for_a_whole_read_finishes_it_over_several_runs(share: int) -> None:
    """#396: each run goes on from where the last stopped, so a share smaller than one whole read still reads it
    all, in ceil(7 / share) runs, and is scored then; it never puts the meter on catch-up."""
    loop, store, _ = _narrowed(share)
    runs = -(-WHOLE_READ // share)
    backlog = [await loop.tick()]
    assert "acme/app" not in store.stored, f"{share} calls cannot read acme's events whole in one run"
    backlog += [await loop.tick() for _ in range(runs - 1)]
    assert "acme/app" in store.stored, f"{share} calls a run read acme's at most {WHOLE_READ} calls in {runs} runs"
    assert any(e["kind"] == "review" for e in store.event_rows["acme/app"].values()), "with its reviews"
    assert "globex/app" in store.stored and not any(backlog), "the others are scored, at the interval"


async def test_a_share_large_enough_for_a_whole_read_scores() -> None:
    loop, store, _ = _narrowed(4)
    for _ in range(10):
        await loop.tick()
    assert "acme/app" in store.stored, "four calls a run read every listing: acme is scored"
    assert any(e["kind"] == "review" for e in store.event_rows["acme/app"].values()), "with its reviews"


async def test_a_repository_whose_share_cuts_its_events_short_keeps_its_previous_score() -> None:
    loop, store, section = _narrowed(200)
    await loop.scheduled()
    scored = store.stored["acme/app"]
    section.owners["acme"] = {"api_calls_per_tick": 1}
    loop._last_pass = None  # noqa: SLF001 - the next look is a full run
    assert await loop.scheduled() == "run"
    assert store.stored["acme/app"] is scored, "the run its share cut short wrote no score: the previous one stays"
    section.owners["acme"] = {"api_calls_per_tick": 1, "weights": {"*.py": 2.0}}
    loop._clock = lambda: NOW + timedelta(minutes=5)  # noqa: SLF001
    assert await loop.scheduled() is None, "nor does a settings change rescore it from that short read"
    assert store.stored["acme/app"] is scored


async def test_the_credential_s_own_budget_holds_no_catch_up_and_the_next_run_goes_on() -> None:
    """#396: the deployment's budget running out puts the meter on catch-up no more than an organization's share
    does. The repository keeps no score until its events are read, and each run goes on from where the last
    stopped, so three calls a run read its seven in three runs."""
    records, order = fakes._history(2)
    forges = {REPO: fakes.FakeForge(repo=REPO)}
    store = fakes.FakeStore()
    loop = _loop(Section({"api_calls_per_tick": 3}), store, fakes.FakeMirror(records, order), [NOW], forges=forges)
    assert await loop.tick() is False and REPO not in store.stored, "not scored until its events are read"
    assert await loop.tick() is False and REPO not in store.stored
    assert await loop.tick() is False and REPO in store.stored, "the third run reads the last of them, and scores"
    assert len(forges[REPO].calls) == WHOLE_READ, f"no call is made twice: {forges[REPO].calls}"
    forges[REPO].calls.clear()
    await loop.tick()
    assert forges[REPO].calls[0][0] == f"/repos/{REPO}/issues/comments", (
        f"a read that finished leaves no place: the next starts at the first listing, not {forges[REPO].calls[0]}"
    )


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


async def test_the_judge_hears_the_repository_s_guidance_and_the_weighing_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records, order = fakes._history(40)
    records["big"] = fakes._record("big", fakes.START + timedelta(days=200), [("src/drop.py", 9_000, 0)])
    order.append("big")
    heard: list[str] = []
    threaded: list[str] = []
    to_thread = loop_mod.asyncio.to_thread

    async def judge(repo: str, outlier: Any, ep: Any, client: Any, calls: Any, *, guidance: str) -> tuple[bool, str]:
        heard.append(guidance)
        return False, "a vendored parser"

    async def spy(function: Any, *args: Any) -> Any:
        threaded.append(function.__name__)
        return await to_thread(function, *args)

    monkeypatch.setattr("embry0.services.slop_meter.judge.judge", judge)
    monkeypatch.setattr(loop_mod.asyncio, "to_thread", spy)
    section = Section({}, acme={"judge_prompt": "Imports under third_party are never slop."})
    loop = _loop(section, fakes.FakeStore(), fakes.FakeMirror(records, order), [NOW], model="custom/local")
    await loop.tick()
    assert heard == ["Imports under third_party are never slop."], f"the owner's guidance reached the judge: {heard}"
    assert threaded == ["history_of", "unjudged", "compute"], (
        "building the history, finding the outliers to judge and scoring each go through every commit or path, "
        f"so each runs in a thread: {threaded}"
    )


async def test_until_the_import_succeeds_the_meter_reads_the_variables_it_read_before() -> None:
    """A first start whose environment import failed leaves ``slop_meter.*`` out of the ledger: a deployment that
    had the meter off (``SLOP_METER_INTERVAL_SECONDS=0``) keeps it off, as the budget's readers keep their caps."""
    import os
    from unittest.mock import patch

    from embry0.config import Embry0Config
    from embry0.settings.store import build_snapshot

    _settings({})
    with patch.dict(
        os.environ, {"SLOP_METER_INTERVAL_SECONDS": "0", "SLOP_METER_API_CALLS_PER_TICK": "50"}, clear=True
    ):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]

    class Store:
        def __init__(self, data: dict[str, Any], ledger: list[str]) -> None:
            self.rows = [
                {"scope": "deployment", "data": data, "generation": 1, "updated_at": None, "bootstrapped_keys": ledger},
                {"scope": "organization:acme", "data": {"slop_meter": {"api_calls_per_tick": 7}}, "generation": 1},
            ]

        async def snapshot(self) -> Any:
            return build_snapshot(CATALOG, self.rows, token=(), log=False)

    pending = await loop_mod.meter_reader(Store({}, []), config)()
    assert pending.meter.deployment().enabled is False, "not imported yet: the variable's 0 still turns it off"
    assert pending.meter.repo("globex/web").api_calls_per_tick == 50, "and its budget is still the variable's"
    assert pending.meter.repo(REPO).api_calls_per_tick == 7, "where an organization's layer sets one, it wins"
    ledger = [f"slop_meter.{key}" for key in ("enabled", "interval_hours", "api_calls_per_tick", "rate_reserve")]
    imported = await loop_mod.meter_reader(Store({}, ledger), config)()
    assert imported.meter.deployment().enabled is True, "once imported, the document is read: here, nothing written"
    written = await loop_mod.meter_reader(Store({"slop_meter": {"enabled": True}}, []), config)()
    assert written.meter.deployment().enabled is True, "a key written into the document since wins"
