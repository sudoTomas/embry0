"""Deploy markers → ``record_deploy`` only after a forge ancestry check (#278 part 2)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from embry0.services.healing.deploys import (
    DEFAULT_LOOKBACK_HOURS,
    MAX_LOOKBACK_HOURS,
    DeployLedgerSync,
    DeployMarker,
    GitHubAncestry,
    ancestry_from_compare,
    parse_deploy_marker,
)
from embry0.services.healing.lifecycle import fix_deployed

from .conftest import REPO

FIXTURES = Path(__file__).parents[3] / "fixtures" / "github-compare"
NEW, OLD, SIDE = "fb9fe6b0ad96", "378a576c9cf8", "7762df9b4185"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


# ---- the marker line ------------------------------------------------------------


def test_the_lab_deploy_marker_parses_to_sha_and_services():
    marker = parse_deploy_marker("deployed 65f0a0257ee1: backend ml\n", NOW)
    assert marker == DeployMarker(at=NOW, sha="65f0a0257ee1", services=("backend", "ml"))
    assert marker.event_key == f"deploy:{int(NOW.timestamp() * 1e9)}:65f0a0257ee1"


@pytest.mark.parametrize(
    "line",
    [
        'time="2026-09-20" msg="Session done" Failed=0 Scanned=9 Updated=1',  # watchtower
        "deployed : backend",
        "deployed 65f0a0257ee1 backend",  # no colon
        "deployed 65F0A0257EE1: backend",  # not a lowercase git sha
        "deployed 65f0a0257ee1: backend; rm -rf /",
        "we deployed 65f0a0257ee1: backend",
        "",
    ],
)
def test_anything_else_is_not_a_marker(line):
    assert parse_deploy_marker(line, NOW) is None


def test_a_marker_with_no_services_is_still_a_deploy():
    assert parse_deploy_marker("deployed 65f0a0257ee1:", NOW).services == ()


# ---- reading GitHub's compare answer (recorded fixtures) --------------------------
# The URL is compare/{deployed}...{merge}; `ancestor` is the merge sha.


@pytest.mark.parametrize(
    ("fixture", "merge", "contained"),
    [
        ("deployed_contains_merge", OLD, True),
        ("deployed_is_merge", NEW, True),
        ("deployed_predates_merge", NEW, False),
        ("diverged", SIDE, False),
    ],
)
def test_ancestry_is_read_the_right_way_round(fixture, merge, contained):
    assert ancestry_from_compare(_fixture(fixture), ancestor=merge) is contained


def test_the_wrong_direction_would_have_vouched_for_an_older_deploy():
    # The trap: `ahead` is NOT "the deploy is ahead of the merge". With the
    # URL compare/{deployed}...{merge} it says the MERGE is ahead — the deploy
    # predates it. Reading this fixture as contained would anchor a case on a
    # deploy that does not carry its fix.
    body = _fixture("deployed_predates_merge")
    assert body["status"] == "ahead"
    assert ancestry_from_compare(body, ancestor=NEW) is False


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"status": "behind"},  # no merge base
        {"status": "behind", "ahead_by": 0, "merge_base_commit": {"sha": "0" * 40}},  # contradicts itself
        {"status": "diverged", "ahead_by": 1, "merge_base_commit": {"sha": OLD + "0" * 28}},  # likewise
        {"status": "behind", "ahead_by": 2, "merge_base_commit": {"sha": OLD + "0" * 28}},
        {"status": "something_new", "ahead_by": 0, "merge_base_commit": {"sha": OLD + "0" * 28}},
    ],
)
def test_an_answer_that_does_not_add_up_is_undetermined(body):
    assert ancestry_from_compare(body, ancestor=OLD) is None


def _client(status: int, body: object = None) -> MagicMock:
    request = httpx.Request("GET", "https://api.github.test/compare")
    return MagicMock(get=AsyncMock(return_value=httpx.Response(status, json=body, request=request)))


@pytest.mark.asyncio
async def test_github_is_asked_deployed_then_merge_and_the_answer_is_cached():
    client = _client(200, _fixture("deployed_contains_merge"))
    ancestry = GitHubAncestry(client)
    assert await ancestry.contains(REPO, descendant=NEW, ancestor=OLD) is True
    assert await ancestry.contains(REPO, descendant=NEW, ancestor=OLD) is True
    client.get.assert_awaited_once_with(f"/repos/{REPO}/compare/{NEW}...{OLD}", repo=REPO)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 403, 500])
async def test_a_forge_that_cannot_answer_is_undetermined_and_not_cached(status):
    client = _client(status, {"message": "Not Found"})
    ancestry = GitHubAncestry(client)
    assert await ancestry.contains(REPO, descendant=NEW, ancestor=OLD) is None
    assert await ancestry.contains(REPO, descendant=NEW, ancestor=OLD) is None
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_a_forge_error_is_undetermined_never_raised():
    ancestry = GitHubAncestry(MagicMock(get=AsyncMock(side_effect=httpx.ConnectError("down"))))
    assert await ancestry.contains(REPO, descendant=NEW, ancestor=OLD) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("sha", ["main", "../../user", "abc", "65f0a0257ee1;x", "", "65f0a0257ee1\n"])
async def test_only_plain_shas_reach_the_url(sha):
    client = _client(200, _fixture("deployed_contains_merge"))
    assert await GitHubAncestry(client).contains(REPO, descendant=sha, ancestor=OLD) is None
    assert await GitHubAncestry(client).contains(REPO, descendant=NEW, ancestor=sha) is None
    client.get.assert_not_awaited()


# ---- markers onto cases (real Postgres) -------------------------------------------


class _Ancestry:
    def __init__(self, answers: dict[str, bool | None]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, str]] = []

    async def contains(self, repo: str, *, descendant: str, ancestor: str) -> bool | None:
        self.asked.append((descendant, ancestor))
        return self.answers[descendant]


async def _merged_case(service, *, merge_sha="aaaaaaa1", merged_ago=timedelta(hours=5)):
    case = await service.open_case(repo=REPO, origin="human", title="t")
    now = datetime.now(UTC)
    await service.record_merge(case["id"], merge_sha=merge_sha, resolution_path="embry0", at=now - merged_ago)
    return case["id"], now


def _marker(now, hours_ago, sha, *services):
    return DeployMarker(at=now - timedelta(hours=hours_ago), sha=sha, services=services)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_earliest_deploy_proven_to_carry_the_merge_becomes_the_anchor(service, cases_repo):
    case_id, now = await _merged_case(service)
    ancestry = _Ancestry({"d0ld0000": True, "dbad0000": False, "dg00d000": True, "d1ater00": True})
    markers = [
        _marker(now, 1, "d1ater00", "backend"),
        _marker(now, 3, "dg00d000", "backend", "ml"),
        _marker(now, 4, "dbad0000", "frontend"),  # after the merge but does not contain it
        _marker(now, 9, "d0ld0000", "backend"),  # before the merge: never even asked
    ]
    counts = await DeployLedgerSync(service, cases_repo, ancestry).record(REPO, markers)

    assert counts == {"recorded": 1, "undetermined": 0}
    assert ancestry.asked == [("dbad0000", "aaaaaaa1"), ("dg00d000", "aaaaaaa1")]
    case = await cases_repo.get(case_id)
    assert (case["stage"], case["deploy_sha"], case["deploy_services"]) == ("deployed", "dg00d000", ["backend", "ml"])
    assert case["deploy_marker_at"] == markers[1].at
    assert fix_deployed(merge_sha=case["merge_sha"], merged_at=case["merged_at"], deployed_at=case["deployed_at"])


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_an_undetermined_deploy_is_not_reported(service, cases_repo):
    case_id, now = await _merged_case(service)
    sync = DeployLedgerSync(service, cases_repo, _Ancestry({"d0000001": None}))
    counts = await sync.record(REPO, [_marker(now, 2, "d0000001", "backend")])

    assert counts == {"recorded": 0, "undetermined": 1}
    detail = await service.get(case_id)
    assert detail["stage"] == "merged"
    assert detail["deploy_sha"] is None
    assert not [e for e in detail["events"] if e["event"] == "deployed"]  # nothing was vouched, nothing written


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_second_pass_asks_nothing_and_writes_nothing(service, cases_repo):
    case_id, now = await _merged_case(service)
    markers = [_marker(now, 2, "d0000001", "backend"), _marker(now, 1, "d0000002", "backend")]
    ancestry = _Ancestry({"d0000001": True, "d0000002": True})
    sync = DeployLedgerSync(service, cases_repo, ancestry)
    await sync.record(REPO, markers)
    events = len((await service.get(case_id))["events"])
    asked = len(ancestry.asked)

    assert await sync.record(REPO, markers) == {"recorded": 0, "undetermined": 0}
    assert len(ancestry.asked) == asked
    assert len((await service.get(case_id))["events"]) == events


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_cases_without_a_merge_sha_other_repos_and_closed_cases_are_left_alone(service, cases_repo):
    now = datetime.now(UTC)
    no_sha = await service.open_case(repo=REPO, origin="human", title="backfilled merge")
    await service.record_merge(no_sha["id"], merge_sha=None, resolution_path="embry0", at=now - timedelta(hours=5))
    unmerged = await service.open_case(repo=REPO, origin="human", title="no merge")
    other = await service.open_case(repo="acme/other", origin="human", title="other repo")
    await service.record_merge(other["id"], merge_sha="bbbbbbb1", resolution_path="embry0", at=now - timedelta(hours=5))
    closed_id, _ = await _merged_case(service, merge_sha="ccccccc1")
    await service.close(closed_id)

    ancestry = _Ancestry({"d0000001": True})
    counts = await DeployLedgerSync(service, cases_repo, ancestry).record(REPO, [_marker(now, 1, "d0000001")])

    assert counts == {"recorded": 0, "undetermined": 0}
    assert ancestry.asked == []
    for case_id in (no_sha["id"], unmerged["id"], other["id"], closed_id):
        assert (await cases_repo.get(case_id))["deploy_sha"] is None


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_an_earlier_deploy_resolved_later_replaces_the_anchor(service, cases_repo):
    case_id, now = await _merged_case(service)
    early, late = _marker(now, 3, "dear1y00", "backend"), _marker(now, 1, "d1ate000", "backend")
    ancestry = _Ancestry({"dear1y00": None, "d1ate000": True})
    sync = DeployLedgerSync(service, cases_repo, ancestry)
    await sync.record(REPO, [early, late])
    assert (await cases_repo.get(case_id))["deploy_sha"] == "d1ate000"

    ancestry.answers["dear1y00"] = True  # the forge answers on the next tick
    await sync.record(REPO, [early, late])
    assert (await cases_repo.get(case_id))["deploy_sha"] == "dear1y00"


# ---- how far back to read markers (review of #340, minor 5) -------------------


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_lookback_is_the_default_when_nothing_awaits_a_deploy(service, cases_repo):
    sync = DeployLedgerSync(service, cases_repo, _Ancestry({}))
    assert await sync.lookback_hours(REPO) == DEFAULT_LOOKBACK_HOURS


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_lookback_reaches_back_to_the_oldest_unanchored_merge(service, cases_repo):
    # An orchestrator down for a week must not lose the marker that carries
    # the merge: without this the case never reaches fix_deployed at all.
    await _merged_case(service, merge_sha="aaaaaaa1", merged_ago=timedelta(days=7))
    hours = await DeployLedgerSync(service, cases_repo, _Ancestry({})).lookback_hours(REPO)
    assert DEFAULT_LOOKBACK_HOURS < hours
    assert 7 * 24 < hours <= MAX_LOOKBACK_HOURS


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_lookback_is_bounded_so_one_forgotten_case_cannot_query_forever(service, cases_repo):
    await _merged_case(service, merge_sha="aaaaaaa2", merged_ago=timedelta(days=400))
    assert await DeployLedgerSync(service, cases_repo, _Ancestry({})).lookback_hours(REPO) == MAX_LOOKBACK_HOURS


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_an_anchored_case_does_not_widen_the_lookback(service, cases_repo):
    case_id, now = await _merged_case(service, merge_sha="aaaaaaa3", merged_ago=timedelta(days=9))
    await service.record_deploy(case_id, sha="d0000009", marker_at=now - timedelta(days=8), services=["backend"])
    assert await DeployLedgerSync(service, cases_repo, _Ancestry({})).lookback_hours(REPO) == DEFAULT_LOOKBACK_HOURS
