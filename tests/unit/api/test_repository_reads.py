"""What a key with a repository allowlist reads of a repository it does not cover, and of an owner, case by case.

On the real app over a real database (:mod:`tests.fixtures.repository_reads`):
the limited key covers ``acme/web``; ``globex/gizmos`` is another owner's
repository and ``acme/api`` a sibling under the covered repository's owner.
``test_repository_reads_inventory.py`` walks every route; here each case the
walk cannot show alone is read on its own: the setup state a view of another
repository used to show (its proposal, checklist and standard), the forge
read for its environment template, the check of a test setup against its
variables and Docker access, the old paths that can name two repositories,
what belongs to an owner as a whole, and the two writes whose answer told
whether another repository exists (mapping its areas, an organization action
on it).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from embry0.api.deps import bust_api_key_cache
from embry0.execution.github_client import GitHubClient
from embry0.orchestration.dispatch_actors import REPO_ALLOWLIST
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.repository_reads import (
    MARK,
    MISSING,
    MISSING_OWNER,
    OWNER_MARK,
    OWNERS,
    SIBLING,
    UNLISTED,
    repository_reads_app,
    same_answer,
)
from tests.fixtures.repository_writes import COVERED, MASTER, UNCOVERED, call

pytestmark = pytest.mark.requires_postgres

HIDDEN = (UNCOVERED, SIBLING)
_CORPUS = Path(__file__).parents[2] / "fixtures" / "qa-yaml-corpus" / "v2"


def _names(repo: str, missing: bool = False) -> dict[str, str]:
    """The names a read of ``repo`` carries, each to its placeholder: its slug, and the owner when the limited key
    covers nothing under it; with ``missing``, the missing counterparts'."""
    names = {MISSING[repo] if missing else repo: "<repo>"}
    if repo == UNCOVERED:
        owner = OWNERS[repo]
        names[MISSING_OWNER[owner] if missing else owner] = "<owner>"
    return names


async def _same_as_missing(app: Any, token: str, url: str, repo: str) -> tuple[httpx.Response, Any]:
    """``url`` naming ``repo`` read with ``token``, and how it differs from the same read naming a repository nobody
    registered, under an owner nobody registered when the limited key covers nothing under ``repo``'s (None when it
    does not)."""
    hidden = await call(app, "GET", url, token)
    missing_url = url.replace(repo, MISSING[repo])
    if repo == UNCOVERED:
        missing_url = missing_url.replace(f"/{OWNERS[repo]}/", f"/{MISSING_OWNER[OWNERS[repo]]}/")
    missing = await call(app, "GET", missing_url, token)
    return hidden, same_answer(hidden, _names(repo), missing, _names(repo, missing=True))


#: The fields of the setup state and the standard's audit that a view of a repository used to show a key that does not
#: cover it: a proposal's notes, failure, smoke results and proposed setup, a standard's score, open findings and
#: exceptions.
SETUP_STATE = ('"notes_md"', '"failure"', '"smoke"', '"proposed_yaml"', '"score"', '"findings_open"', '"exceptions"')


@pytest.mark.parametrize("repo", HIDDEN)
async def test_another_repository_s_setup_state_and_audit_read_as_a_repository_nobody_registered(
    repo: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The views #318 left naming no job id but still showing the repository's own setup state: its test setup
    proposal (status, timing, failure, notes, smoke results, proposed setup), its checklist, and its standard's
    state (audit score, open findings, exceptions). Each answers the limited key as for a repository nobody
    registered, and shows it none of that state; the key's own repository still shows its own."""
    owner = OWNERS[repo]
    paths = (
        f"/api/v1/repos/{repo}/-/setup/proposal",
        f"/api/v1/repos/{repo}/-/readiness",
        f"/api/v1/standards/{owner}/design/repos/{repo}",
        f"/api/v1/repos/{repo}",
    )
    async with repository_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        answers = {path: await _same_as_missing(app, keys.limited, path, repo) for path in paths}
        everyone = {path: (await call(app, "GET", path, keys.open)).text for path in paths}
        own = {
            path: await call(app, "GET", path.replace(repo, COVERED).replace(f"/{owner}/", "/acme/"), keys.limited)
            for path in paths
        }
        listed = (await call(app, "GET", f"/api/v1/standards/{owner}", keys.limited)).json()
    assert all(f in everyone[paths[0]] for f in SETUP_STATE[:4]) and f"failure of {MARK[repo]}" in everyone[paths[0]], (
        f"a key without a list reads the proposal's state, so the check measures something: {everyone[paths[0]][:400]}"
    )
    assert all(f in everyone[paths[2]] for f in SETUP_STATE[4:]) and "exception of" in everyone[paths[2]], (
        f"and the standard's audit: {everyone[paths[2]][:400]}"
    )
    differ = {path: diff for path, (_response, diff) in answers.items() if diff is not None}
    assert differ == {}, f"the limited key reads {repo}'s views as a repository nobody registered's: {differ}"
    shown = {
        path: response.text[:200]
        for path, (response, _diff) in answers.items()
        if MARK[repo] in response.text or any(f in response.text for f in SETUP_STATE)
    }
    assert shown == {}, f"the limited key reads none of {repo}'s setup state: {shown}"
    assert [s["repo"] for s in listed["repos"]] == ([COVERED] if owner == "acme" else []), (
        f"the owner's standard lists only the key's repositories: {listed['repos']}"
    )
    assert all(response.status_code == 200 for response in own.values()), (
        f"the limited key reads its own repository's views: { {p: r.text[:200] for p, r in own.items()} }"
    )
    assert f"notes of {MARK[COVERED]}" in own[paths[0]].text and '"score":77' in own[paths[2]].text, (
        f"and its own setup state and audit: {own[paths[0]].text[:300]} {own[paths[2]].text[:300]}"
    )


@pytest.mark.parametrize("repo", HIDDEN)
async def test_another_repository_s_environment_template_is_not_read_from_the_forge(
    repo: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GET /repos/{repo}/-/environment/detect`` reads a template file from the forge with the repository's
    credential. For a repository the limited key does not cover the forge is not asked: it answers as a repository
    whose files name no template, as for one nobody registered."""
    asked: list[str] = []

    async def forge_get(_client: GitHubClient, path: str, **_kwargs: Any) -> httpx.Response:
        asked.append(path)
        return httpx.Response(
            200, text=f"# {MARK.get(path.split('/contents/')[0].removeprefix('/repos/'), '')}\nAPI_URL=x\n"
        )

    monkeypatch.setattr(GitHubClient, "get", forge_get)
    url = f"/api/v1/repos/{repo}/-/environment/detect"
    async with repository_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        hidden, differ = await _same_as_missing(app, keys.limited, url, repo)
        hidden_asked = list(asked)
        everyone = await call(app, "GET", url, keys.open)
        own = await call(app, "GET", url.replace(repo, COVERED), keys.limited)
    assert hidden_asked == [], f"the forge is not asked for {repo} for the limited key: {hidden_asked}"
    assert hidden.json() == {"source_file": "", "variables": [], "unconfigured_count": 0} and differ is None, (
        f"{repo} reads as a repository whose files name no template: {hidden.text} {differ}"
    )
    assert everyone.json()["variables"] and own.json()["variables"], (
        f"a key without a list, and the limited key on its own repository, read the template: {everyone.text} {own.text}"
    )


@pytest.mark.parametrize("repo", HIDDEN)
async def test_checking_a_test_setup_reads_another_repository_s_variables_and_docker_access_as_none(
    repo: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``POST /repos/{repo}/-/qa-config/check`` stores nothing and answers with what the repository's variables and
    Docker access mean for the setup. For a repository the limited key does not cover it answers as for one nobody
    registered: no variables of its own, no Docker access."""
    docker = (_CORPUS / "single-app-with-e2e.yaml").read_text()
    login = (_CORPUS / "managed-vibium-with-auth.yaml").read_text().replace("QA_STORAGE_STATE", "QA_LOGIN_STATE")
    url = f"/api/v1/repos/{repo}/-/qa-config/check"
    async with repository_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        results: dict[str, dict[str, Any]] = {}
        for name, text in (("docker", docker), ("login", login)):
            for caller, token in (("limited", keys.limited), ("open", keys.open)):
                for target in (repo, MISSING[repo]):
                    response = await call(app, "POST", url.replace(repo, target), token, text)
                    assert response.status_code == 200, response.text
                    results[f"{name} {caller} {target}"] = response.json()
    for name in ("docker", "login"):
        limited, missing = results[f"{name} limited {repo}"], results[f"{name} limited {MISSING[repo]}"]
        assert json.dumps(limited) == json.dumps(missing), (
            f"{name}: the limited key's check of {repo} is a repository nobody registered's: {limited} {missing}"
        )
        assert results[f"{name} open {repo}"] != results[f"{name} open {MISSING[repo]}"], (
            f"{name}: a key without a list reads {repo}'s variables and Docker access, so the check measures something"
        )


async def test_an_old_path_naming_a_repository_the_key_does_not_cover_does_not_refuse_it_as_two(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An old path without ``/-/`` that also names a registered repository answers 409 listing both. A repository the
    limited key does not cover is not registered for it: the path keeps its old meaning, as when nobody registered
    it, so the 409 never tells the key it exists."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        await db.execute(
            "INSERT INTO repos (repo, active) VALUES ($1, true), ($2, true)",
            f"{COVERED}/qa-config",
            f"{UNCOVERED}/qa-config",
        )
        own = await call(app, "GET", f"/api/v1/repos/{COVERED}/qa-config", keys.limited)
        own_to_everyone = await call(app, "GET", f"/api/v1/repos/{COVERED}/qa-config", keys.open)
        hidden, differ = await _same_as_missing(app, keys.limited, f"/api/v1/repos/{UNCOVERED}/qa-config", UNCOVERED)
    assert own_to_everyone.status_code == 409, f"both readings are registered: {own_to_everyone.text}"
    assert own.status_code == 200 and MARK[COVERED] in own.text, (
        f"to the limited key the path is its repository's test setup: {own.status_code} {own.text[:200]}"
    )
    assert hidden.status_code == 404 and differ is None, (
        f"the other repository's old path answers as one nobody registered: {hidden.status_code} {differ}"
    )


async def _whole_owner_key(db: DatabasePool) -> str:
    """An admin key covering every repository registered under ``acme``: it covers the owner as a whole."""
    _row, token = await ApiKeysRepository(db).create(
        name="acme-whole", scope="admin", user_id=None, created_by="tests", allowed_repos=[COVERED, SIBLING]
    )
    bust_api_key_cache()
    return token


#: What belongs to ``acme`` as a whole, by route: each answers a key covering acme in part as a missing owner.
OWNER_READS = {
    "/api/v1/orgs/acme": 404,
    "/api/v1/orgs/acme/needs": 404,
    "/api/v1/orgs/acme/runs": 404,
    "/api/v1/orgs/acme/repos": 404,
    "/api/v1/orgs/acme/context-audit": 404,
    "/api/v1/standards/acme/design": 404,
    "/api/v1/config/settings?owner=acme": 404,
    f"/api/v1/maps/map-{OWNER_MARK['acme']}": 404,
}


async def test_what_belongs_to_an_owner_as_a_whole_reads_only_to_a_key_covering_it_as_a_whole(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key covering every repository registered under ``acme`` reads acme's organization, needs, discovery runs,
    standard, settings layer, map of no repository and forge credential binding, narrowed to the repositories it
    covers: not a repository discovery saw that nobody registered and that its list leaves out. The limited key,
    which covers acme in part, reads each as an owner nobody registered."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        await db.execute(
            "UPDATE organizations SET access = jsonb_set(access, '{sample_repo}', to_jsonb($2::text)) WHERE owner = $1",
            "acme",
            UNLISTED["acme"],
        )
        whole = await _whole_owner_key(db)
        reads: dict[str, dict[str, httpx.Response]] = {}
        for path in [*OWNER_READS, "/api/v1/orgs", "/api/v1/standards/acme", "/api/v1/maps"]:
            reads[path] = {
                caller: await call(app, "GET", path, token)
                for caller, token in (("whole", whole), ("open", keys.open), ("limited", keys.limited))
            }
        bindings = "/api/v1/credentials/bindings?scope_kind=owner&scope_value=github:acme"
        owner_bindings = {
            caller: (await call(app, "GET", bindings, token)).json()["bindings"]
            for caller, token in (("whole", whole), ("open", keys.open), ("limited", keys.limited))
        }
    for path, status in OWNER_READS.items():
        limited, missing_owner = reads[path]["limited"], reads[path]["open"]
        assert limited.status_code == status, (
            f"{path}: the limited key reads acme as an owner nobody registered: {limited.text[:200]}"
        )
        assert reads[path]["whole"].status_code == 200, (
            f"{path}: the whole-owner key reads it: {reads[path]['whole'].text[:200]}"
        )
        assert missing_owner.status_code == 200, f"{path}: a key without a list reads it: {missing_owner.text[:200]}"
    org = reads["/api/v1/orgs/acme"]
    assert org["open"].json()["access"]["sample_repo"] == UNLISTED["acme"], org["open"].text[:300]
    # Its counts of the owner's repositories count its own (test_an_owner_s_discovery_runs_count_only_...).
    counted = ("access", "repo_count")
    assert {**org["whole"].json(), **dict.fromkeys(counted)} == {**org["open"].json(), **dict.fromkeys(counted)}, (
        f"the whole-owner key reads the organization: {org['whole'].text[:300]}"
    )
    assert org["whole"].json()["access"]["sample_repo"] is None, f"its sample only when covered: {org['whole'].text}"
    facts = reads["/api/v1/orgs/acme/repos"]
    assert UNLISTED["acme"] in facts["open"].text and UNLISTED["acme"] not in facts["whole"].text, (
        f"the facts of a repository nobody registered and the key does not cover are left out: {facts['whole'].text[:300]}"
    )
    assert sorted(r["repo"] for r in facts["whole"].json()["repos"]) == sorted([COVERED, SIBLING]), facts["whole"].text[
        :300
    ]
    # The needs and the runs are narrowed to its repositories (test_an_owner_s_needs_name_only_..., ..._runs_count_...).
    for path in ("/api/v1/standards/acme/design", "/api/v1/standards/acme"):
        assert reads[path]["whole"].content == reads[path]["open"].content, (
            f"{path}: the whole-owner key reads it whole"
        )
    orgs = {
        caller: [o["owner"] for o in reads["/api/v1/orgs"][caller].json()["organizations"]]
        for caller in ("whole", "open", "limited")
    }
    assert orgs == {"whole": ["acme"], "open": ["acme", "globex"], "limited": []}, orgs
    listed = reads["/api/v1/standards/acme"]["limited"].json()
    assert listed["standards"] == [] and [s["repo"] for s in listed["repos"]] == [COVERED], (
        f"to the limited key, acme's standard is not there and only its own repository is listed: {listed}"
    )
    maps = {
        caller: sorted(m["slug"] for m in reads["/api/v1/maps"][caller].json()["maps"] if m["owner"] == "acme")
        for caller in ("whole", "limited")
    }
    assert f"map-{OWNER_MARK['acme']}" in maps["whole"] and f"map-{OWNER_MARK['acme']}" not in maps["limited"], maps
    assert f"map-{MARK[SIBLING]}" in maps["whole"] and f"map-{MARK[SIBLING]}" not in maps["limited"], maps
    assert f"map-{MARK[COVERED]}" in maps["limited"], f"the limited key reads its own repository's map: {maps}"
    assert owner_bindings["whole"] == owner_bindings["open"] != [] and owner_bindings["limited"] == [], owner_bindings


async def test_a_master_key_and_a_key_without_a_list_read_every_repository_and_owner(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule reads nothing for a caller without a list: the master key and an admin key without a list read the
    same of every repository and owner, the hidden ones included."""
    paths = [
        "/api/v1/repos",
        "/api/v1/orgs",
        "/api/v1/maps",
        "/api/v1/setup/proposals",
        "/api/v1/watcher/stacks",
        "/api/v1/config/context/repos",
        "/api/v1/config/settings/layers",
        *(f"/api/v1/repos/{repo}" for repo in HIDDEN),
        *(f"/api/v1/orgs/{owner}" for owner in OWNER_MARK),
    ]
    async with repository_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        reads = {
            path: (await call(app, "GET", path, MASTER), await call(app, "GET", path, keys.open)) for path in paths
        }
    for path, (master, everyone) in reads.items():
        assert master.status_code == everyone.status_code == 200, f"{path}: {master.status_code} {everyone.status_code}"
    listed = reads["/api/v1/repos"][1].json()
    assert {COVERED, UNCOVERED, SIBLING} <= {r["repo"] for r in listed}, listed


async def test_an_owner_s_context_audits_leave_out_unread_a_repository_the_reader_may_not_read(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GET /orgs/{owner}/context-audit`` surveys only the repositories the key covers. The survey leaves one it may
    not read out whole: it is not listed, and the forge is not asked for it, which the schedule here would refuse
    (it reads no forge) had it been asked for a repository with no reading taken."""
    async with repository_reads_app(db_with_migrations, monkeypatch) as (app, _keys, _values):
        schedule = app.state.context_audit
        del schedule._last_read[SIBLING]  # noqa: SLF001 - never read: a survey reading it would ask the forge
        org = await app.state.organizations_repo.get("acme")
        survey = await schedule.survey(org, refresh=False, hidden=lambda repo: repo == SIBLING)
    assert [state.context.repo for state in survey.repos] == [COVERED] and survey.unread == (), (
        f"the survey lists only the repository the reader may read, and reads no other: {survey}"
    )


async def _refusals(db: DatabasePool) -> list[str]:
    rows = await db.fetch(
        "SELECT details->>'repo' AS repo FROM audit_log WHERE action = 'apikey.repo_refused' AND actor = $1 ORDER BY id",
        REPO_ALLOWLIST,
    )
    return [str(r["repo"]) for r in rows]


@pytest.mark.parametrize("repo", HIDDEN)
async def test_mapping_another_repository_s_areas_is_refused_whether_or_not_it_is_registered(
    repo: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``POST /repos/{repo}/-/areas/map`` checks the key's allowlist before it reads the registration, as every route
    that starts work on a repository does: another repository answers the same refusal whether it is registered or
    not, each refusal audited. A key without a list is answered by the registration (404 for one nobody registered)."""
    db = db_with_migrations
    url = f"/api/v1/repos/{repo}/-/areas/map"
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        before = await _refusals(db)
        hidden = await call(app, "POST", url, keys.limited, {})
        missing_url = url.replace(repo, MISSING[repo])
        if repo == UNCOVERED:
            missing_url = missing_url.replace(f"/{OWNERS[repo]}/", f"/{MISSING_OWNER[OWNERS[repo]]}/")
        missing = await call(app, "POST", missing_url, keys.limited, {})
        refused = (await _refusals(db))[len(before) :]
        everyone = await call(app, "POST", missing_url, keys.open, {})
    differ = same_answer(hidden, _names(repo), missing, _names(repo, missing=True))
    assert differ is None and hidden.status_code == 403, (
        f"mapping {repo}'s areas answers the limited key as for a repository nobody registered: {differ}"
    )
    assert refused == [repo, missing_url.split("/repos/", 1)[1].removesuffix("/-/areas/map")], (
        f"each refusal is audited, naming the repository: {refused}"
    )
    assert everyone.status_code == 404, f"a key without a list reads the registration: {everyone.text[:200]}"


#: A repository under acme that nobody registered and discovery never saw, its name as long as ``acme/unlisted``'s.
NEVER_SEEN = "acme/notseen1"


@pytest.mark.parametrize("dry_run", [True, False])
async def test_an_organization_action_refuses_a_repository_the_key_does_not_cover_whether_or_not_discovery_saw_it(
    dry_run: bool, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``POST /orgs/{owner}/actions`` names each repository the key does not cover as ``failed`` with the refusal,
    under the name the body gives, before it reads discovery's facts: ``acme/unlisted``, which discovery saw, and a
    repository it never saw answer the same, so the answer does not tell which names discovery has seen. A key
    covering acme as a whole runs it; a real run audits each refusal. To a key without a list the two differ."""
    assert len(NEVER_SEEN) == len(UNLISTED["acme"])
    db = db_with_migrations
    url = "/api/v1/orgs/acme/actions"
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        whole = await _whole_owner_key(db)
        before = await _refusals(db)
        answers = {
            name: await call(app, "POST", url, whole, {"action": "register", "repos": [name], "dry_run": dry_run})
            for name in (UNLISTED["acme"], NEVER_SEEN)
        }
        refused = (await _refusals(db))[len(before) :]
        everyone = {
            name: (
                await call(app, "POST", url, keys.open, {"action": "register", "repos": [name], "dry_run": True})
            ).json()["results"]
            for name in (UNLISTED["acme"], NEVER_SEEN)
        }
    seen, never = answers[UNLISTED["acme"]], answers[NEVER_SEEN]
    differ = same_answer(seen, {UNLISTED["acme"]: "<repo>"}, never, {NEVER_SEEN: "<repo>"})
    assert seen.status_code == 200 and differ is None, (
        f"a repository discovery saw and one it never saw answer the key that does not cover them the same: {differ}"
    )
    assert [r["outcome"] for r in seen.json()["results"]] == ["failed"], seen.text[:300]
    assert refused == ([] if dry_run else [UNLISTED["acme"], NEVER_SEEN]), (
        f"a real run audits each refusal, a dry run none: {refused}"
    )
    assert everyone[UNLISTED["acme"]] != [] and [r["outcome"] for r in everyone[NEVER_SEEN]] == ["skipped"], (
        f"to a key without a list, discovery's facts decide: {everyone}"
    )
    assert everyone[UNLISTED["acme"]][0]["outcome"] != "skipped", everyone


async def _layer_revision(db: DatabasePool, repo: str) -> int:
    """The id of the change that wrote ``repo``'s settings layer."""
    return int(
        await db.fetchval(
            "SELECT min(id) FROM config_revisions WHERE surface = 'settings' AND scope = $1", f"repository:{repo}"
        )
    )


@pytest.mark.parametrize("repo", HIDDEN)
async def test_undoing_a_change_of_another_repository_s_layer_answers_as_a_change_that_does_not_exist(
    repo: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``POST /config/settings/revisions/{id}/undo`` authorizes the document the change belongs to. A change of a
    repository's layer the key does not cover is, to it, a change that does not exist: the same answer and the same
    audit row, naming no repository, whatever the id, so walking the ids does not list the repositories with a layer.
    The layer is not touched; the key undoes a change of its own repository's layer."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        hidden_id = await _layer_revision(db, repo)
        missing_id = int(await db.fetchval("SELECT max(id) FROM config_revisions")) + 1000
        before = await _refusals(db)
        hidden = await call(app, "POST", f"/api/v1/config/settings/revisions/{hidden_id}/undo", keys.limited)
        missing = await call(app, "POST", f"/api/v1/config/settings/revisions/{missing_id}/undo", keys.limited)
        refused = (await _refusals(db))[len(before) :]
        layer = (await call(app, "GET", f"/api/v1/config/settings?repo={repo}", MASTER)).json()
        own_id = await _layer_revision(db, COVERED)
        own = await call(app, "POST", f"/api/v1/config/settings/revisions/{own_id}/undo", keys.limited)
    assert (hidden.status_code, hidden.content) == (missing.status_code, missing.content), (
        f"undoing a change of {repo}'s layer answers as a change that does not exist: {hidden.text} {missing.text}"
    )
    assert repo not in hidden.text and refused == ["(no repository)", "(no repository)"], (
        f"neither the answer nor the audit names {repo}: {hidden.text} {refused}"
    )
    assert layer["generation"] == 1 and MARK[repo] in layer["yaml_text"], f"the layer is not touched: {layer}"
    assert own.status_code == 200, f"the key undoes a change of its own repository's layer: {own.text[:300]}"


#: A repository on the discovering key's list that nobody registered, and discovery saw.
INCOMING = "acme/incoming"
#: The acme discovery run the fixture holds.
ACME_RUN = f"odr-{OWNER_MARK['acme']}"


async def _discovered(db: DatabasePool) -> str:
    """Discovery's facts of acme, each stamped with its run, as a run that compared counts left them: acme/incoming
    and acme/unlisted (nobody registered either) with contents the credential cannot read, acme/unlisted with no
    webhook here too; the run's summary with a count gap. Returns a key covering acme as a whole and acme/incoming."""
    await db.execute(
        "INSERT INTO org_repo_facts (owner, repo, default_branch, facts) VALUES ('acme', $1, 'main', $2::jsonb)",
        INCOMING,
        {"root_visible": False},
    )
    await db.execute(
        "UPDATE org_repo_facts SET facts = $2::jsonb WHERE repo = $1",
        UNLISTED["acme"],
        {"root_visible": False, "hooks_visible": True, "hook_present": False},
    )
    await db.execute("UPDATE org_repo_facts SET run_id = $1 WHERE owner = 'acme'", ACME_RUN)
    gap = {"gap": 3, "owner_says": 7, "listed": 4, "public": 0, "private": 4, "internal": 0, "forks": 0, "archived": 0}
    await db.execute(
        "UPDATE org_discovery_runs SET summary = $2::jsonb WHERE id = $1",
        ACME_RUN,
        {"repos": 4, "registered": 2, "count_gap": gap, "listing_complete": True},
    )
    _row, token = await ApiKeysRepository(db).create(
        name="acme-discovering",
        scope="admin",
        user_id=None,
        created_by="tests",
        allowed_repos=[COVERED, SIBLING, INCOMING],
    )
    bust_api_key_cache()
    return token


async def test_an_owner_s_needs_name_only_the_repositories_the_key_covers(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery reads every repository under the owner with the deployment's credential, and the needs it derives
    name them all. A key covering acme as a whole reads each need narrowed to its own repositories, its title and what
    its action names counted again for them; a need naming none of them (acme/unlisted's webhook), or a count of the
    owner's whole listing (the count gap), is not there for it, and acting on one answers as a missing need."""
    db = db_with_migrations
    url = "/api/v1/orgs/acme/needs"
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        key = await _discovered(db)
        everyone = (await call(app, "GET", url, keys.open)).json()
        mine = (await call(app, "GET", url, key)).json()
        hook = next(n for n in everyone["needs"] if n["kind"] == "no_webhook")
        before = await _refusals(db)
        hidden = await call(app, "POST", f"{url}/{hook['id']}/dismiss", key)
        missing = await call(app, "POST", f"{url}/{'need-' + '0' * (len(hook['id']) - 5)}/dismiss", key)
        refused = (await _refusals(db))[len(before) :]
        after = next(n for n in (await call(app, "GET", url, keys.open)).json()["needs"] if n["id"] == hook["id"])
    kinds = {n["kind"]: n for n in everyone["needs"]}
    assert UNLISTED["acme"] in str(everyone) and "count_gap" in kinds, f"a key without a list reads them: {everyone}"
    assert kinds["not_registered"]["title"] == "Register 2 repositories", kinds["not_registered"]
    assert UNLISTED["acme"] not in str(mine), f"the key reads no need naming acme/unlisted: {mine}"
    shown = {n["kind"]: n for n in mine["needs"]}
    assert "count_gap" not in shown and "no_webhook" not in shown, f"a need naming none of its repositories: {mine}"
    assert (shown["not_registered"]["repos"], shown["not_registered"]["title"]) == ([INCOMING], "Register 1 repository")
    assert shown["access_limited"]["title"] == "Widen the credential for 1 repository", shown["access_limited"]
    assert shown["access_limited"]["action"]["params"]["missing"] == {INCOMING: ["contents"]}, shown["access_limited"]
    statuses = [n["status"] for n in mine["needs"]]
    assert mine["counts"] == {s: statuses.count(s) for s in ("open", "done", "dismissed")}, mine["counts"]
    differ = same_answer(hidden, {hook["id"]: "<need>"}, missing, {"need-" + "0" * (len(hook["id"]) - 5): "<need>"})
    assert differ is None and hidden.status_code == 404, (
        f"dismissing a need it does not read is a missing need's: {differ}"
    )
    assert refused == [f"need {hook['id']}"] and after["status"] == hook["status"], (
        f"the refusal is audited and the need left as it was: {refused} {after}"
    )


async def test_an_owner_s_discovery_runs_count_only_the_repositories_the_key_covers(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A discovery run's counts come from the facts it gathered of every repository under the owner, and the
    organization's ``repo_count`` from its newest. To a key covering acme as a whole they count only its own
    repositories, and the count gap, the owner's record against the whole listing, is left out, as is the forge
    probe's count of every repository the credential sees."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        key = await _discovered(db)
        everyone = (await call(app, "GET", "/api/v1/orgs/acme/runs", keys.open)).json()["runs"][0]
        mine = (await call(app, "GET", "/api/v1/orgs/acme/runs", key)).json()["runs"][0]
        org = {
            who: (await call(app, "GET", "/api/v1/orgs/acme", token)).json()
            for who, token in (("everyone", keys.open), ("mine", key))
        }
    assert everyone["summary"]["repos"] == 4 and "count_gap" in everyone["summary"], everyone
    assert (mine["repos_total"], mine["repos_seen"], mine["summary"]["repos"], mine["summary"]["registered"]) == (
        3,
        3,
        3,
        2,
    ), f"the run counts acme/web, acme/api and acme/incoming, the key's own: {mine}"
    assert "count_gap" not in mine["summary"] and mine["summary"]["listing_complete"] is True, mine
    assert (org["everyone"]["repo_count"], org["everyone"]["access"]["repo_count"]) == (4, 4), org["everyone"]
    assert (org["mine"]["repo_count"], org["mine"]["access"]["repo_count"]) == (3, None), org["mine"]


async def test_an_owner_s_hook_need_names_only_the_hosts_of_the_key_s_repositories(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The need to name this deployment's webhook URL lists the hosts the hooks deliver to, gathered from every
    repository discovery read. A key covering acme as a whole and acme/incoming reads it narrowed to acme/incoming:
    its hosts, and its detail, name only acme/incoming's, never ``hidden.test``, which only acme/unlisted's hook
    delivers to. A key without a list reads both."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        key = await _discovered(db)
        for repo, host in ((INCOMING, "incoming.test"), (UNLISTED["acme"], "hidden.test")):
            await db.execute(
                "UPDATE org_repo_facts SET facts = $2::jsonb WHERE repo = $1",
                repo,
                {"hooks_visible": True, "hook_count": 1, "hook_hosts": [host]},
            )
        everyone = (await call(app, "GET", "/api/v1/orgs/acme/needs", keys.open)).json()
        mine = (await call(app, "GET", "/api/v1/orgs/acme/needs", key)).json()
    theirs = next(n for n in everyone["needs"] if n["kind"] == "hook_origin_unknown")
    assert theirs["action"]["params"]["hosts"] == ["hidden.test", "incoming.test"], theirs
    assert "hidden.test" not in str(mine), f"the key reads no host only acme/unlisted's hook delivers to: {mine}"
    need = next(n for n in mine["needs"] if n["kind"] == "hook_origin_unknown")
    assert (need["repos"], need["action"]["params"]["hosts"]) == ([INCOMING], ["incoming.test"]), need
    assert need["detail"].startswith("1 repository has webhooks") and "deliver to incoming.test." in need["detail"], (
        need
    )
