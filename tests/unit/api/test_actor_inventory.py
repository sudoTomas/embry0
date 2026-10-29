"""Every place who-did-it can come from is classified, so a new one cannot appear without a decision.

Each GET route the app serves under ``/api/v1`` is one of three kinds:

- **history**: it answers who changed something, and shows it as
  ``GET /audit`` shows actors (``embry0/api/actor_visibility.py``). These
  are the routes ``test_actor_fields.py`` reads as three callers
  (:data:`~tests.unit.api.test_actor_fields.HISTORY_ROUTES`).
- **collaboration**: it names people working together (who started a job,
  who answered, who handed a decision over, whose mark), visible to every
  signed-in key by design. :data:`COLLABORATION_ROUTES` says why for each.
- **names no one**: :data:`NO_ACTOR_ROUTES`, with why.

A new GET route fails until it is put in one of them. Every column of the
migrated schema whose name says it holds a person (``actor``, ``*_by``, an
email, a login, an author, a recipient, an owner) is classified the same way
in :data:`ACTOR_COLUMNS`, and a history column names the history routes
that serve it. A new such column fails until it is classified.

What these checks cannot catch: an existing route that starts returning an
existing column, and a name written into free text or a JSON payload. The
per-route cases and review hold those.
"""

from __future__ import annotations

import os
import re
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import APIRouter

from embry0.config import Embry0Config
from tests.fixtures.routes import app_routes
from tests.unit.api.test_actor_fields import HISTORY_ROUTES

#: GET routes that name people working together, visible to every signed-in key by design, grouped by why.
COLLABORATION_ROUTES: dict[str, tuple[str, ...]] = {
    "who started a job, set its finish line, answered its questions or acted on its issue: the people on it": (
        "/api/v1/jobs/{job_id}",
        "/api/v1/jobs/{job_id}/inputs",
        "/api/v1/issues/{issue_id}",
        "/api/v1/issues/{issue_id}/inputs",
        "/api/v1/issues/{issue_id}/activity",
    ),
    "a job's task and log, which may say who sent it back or started it again": (
        "/api/v1/jobs",
        "/api/v1/jobs/{job_id}/logs/events",
    ),
    "who handed a decision over and who answered it: the team coordinates on it": (
        "/api/v1/handoffs",
        "/api/v1/decisions",
        "/api/v1/decisions/{decision_id:path}",
        "/api/v1/asks",
        "/api/v1/asks/due",
    ),
    "whose verdict a map part carries and who sent it: marking a map is a shared review": (
        "/api/v1/maps",
        "/api/v1/maps/{slug}/parts/{node_id:path}",
        "/api/v1/maps/{slug}/marks",
        "/api/v1/maps/{slug}/work",
    ),
    "leads and quests: who marked a part bloat, owners and reviewers by their forge logins, who rated the work": (
        "/api/v1/leads",
        "/api/v1/proposals",
        "/api/v1/proposals/{proposal_id}",
        "/api/v1/world",
        "/api/v1/world/quests/{job_id}",
    ),
}

#: GET routes that name no one as having done something, grouped by why.
NO_ACTOR_ROUTES: dict[str, tuple[str, ...]] = {
    "the caller's own session, or the sign-in flow": (
        "/api/v1/auth/check",
        "/api/v1/auth/login",
        "/api/v1/auth/callback",
        "/api/v1/auth/me",
    ),
    "admin-only, or only the caller's own keys to anyone else": ("/api/v1/users", "/api/v1/apikeys"),
    "the directory of people a decision can be sent to: who signs in, not who did what": ("/api/v1/people",),
    "configuration, catalogues and health, with no actor": (
        "/api/v1/health/ready",
        "/api/v1/agents",
        "/api/v1/agents/{agent_type}",
        "/api/v1/skills",
        "/api/v1/skills/sources",
        "/api/v1/skills/sources/{source_id}",
        "/api/v1/skills/inline",
        "/api/v1/tools",
        "/api/v1/audit/actions",
        "/api/v1/sandbox-profiles",
        "/api/v1/sandbox-profiles/{name}",
        "/api/v1/sandboxes/active",
        "/api/v1/config/models",
        "/api/v1/config/budget",
        "/api/v1/config/context",
        "/api/v1/config/context/repos/{repo:path}",
        "/api/v1/config/integrations",
        "/api/v1/config/provider",
        "/api/v1/config/providers/{provider_name}/usage",
        "/api/v1/config/context/repos",
        "/api/v1/config/settings/schema",
        "/api/v1/progression/pack/default",
        "/api/v1/progression/pack/default/diff",
        "/api/v1/progression/pack/vocabulary",
        "/api/v1/run-with/keys",
        "/api/v1/watcher/stacks",
        "/api/v1/watcher/stacks/{owner}/{name}",
        "/api/v1/environment/global",
        "/api/v1/environment/global/{key}/reveal",
        "/api/v1/repos/{full_repo:path}/environment",
        "/api/v1/repos/{full_repo:path}/environment/resolve",
        "/api/v1/repos/{full_repo:path}/environment/{key}/reveal",
        "/api/v1/repos/{full_repo:path}/environment/detect",
        "/api/v1/repos/{full_repo:path}/preferences",
        "/api/v1/repos/{full_repo:path}/qa-config",
        "/api/v1/repos/{full_repo:path}/setup/cost",
        "/api/v1/repos/{full_repo:path}/readiness",
        "/api/v1/setup/proposals",
        "/api/v1/repos",
        "/api/v1/hosts",
        "/api/v1/hosts/{host_id}",
        "/api/v1/admin/retention/preview",
    ),
    "a credential's own forge account (verified_login), never who registered it": (
        "/api/v1/credentials",
        "/api/v1/credentials/resolve",
        "/api/v1/credentials/xai/device-login/{login_id}",
    ),
    "what jobs did and cost, with no actor": (
        "/api/v1/issues",
        "/api/v1/jobs/{job_id}/verification",
        "/api/v1/jobs/{job_id}/agents/{agent_type}/session",
        "/api/v1/jobs/{job_id}/deliverables",
        "/api/v1/jobs/{job_id}/deliverables/{deliverable_id}/download",
        "/api/v1/logs/containers",
        "/api/v1/logs/query",
        "/api/v1/traces",
        "/api/v1/stats",
        "/api/v1/queue",
        "/api/v1/pipelines/templates",
        "/api/v1/pipelines/templates/{template_id}",
    ),
    "QA runs and their artifacts, with no actor": (
        "/api/v1/jobs/{job_id}/artifacts/logs/{service}",
        "/api/v1/jobs/{job_id}/artifacts/screenshots/latest",
        "/api/v1/jobs/{job_id}/qa/services",
        "/api/v1/jobs/{job_id}/qa/attempts",
        "/api/v1/jobs/{job_id}/qa/attempts/{attempt_n}/result",
        "/api/v1/jobs/{job_id}/qa/screenshots/latest",
        "/api/v1/jobs/{job_id}/qa/attempts/{attempt_n}/artifacts/{path:path}",
        "/api/v1/jobs/{job_id}/artifacts/{path:path}",
        "/api/v1/qa/runs/{run_id}/apps/{app}/artifacts/{kind}",
        "/api/v1/qa/runs/{run_id}/apps/{app}/artifacts/{kind}/{filename}",
        "/api/v1/qa/repos",
        "/api/v1/qa/repos/{repo:path}/runs",
        "/api/v1/qa/repos/{repo:path}/apps/{app}/history",
        "/api/v1/qa/runs/{run_id}",
        "/api/v1/qa/runs/{run_id}/apps/{app}",
        "/api/v1/qa/runs/{run_id}/affected_set",
        "/api/v1/qa/repos/{repo:path}/cache/analytics",
        "/api/v1/qa/repos/{repo:path}/flake",
        "/api/v1/qa/runs/{run_id}/events",
    ),
    "findings, areas and the agents' progression: no one named as having done anything (areas name their owners)": (
        "/api/v1/cases",
        "/api/v1/cases/{case_id}",
        "/api/v1/repos/{full_repo:path}/areas",
        "/api/v1/repos/{full_repo:path}/slop-meter",
        "/api/v1/watcher/runs",
        "/api/v1/progression",
        "/api/v1/progression/preview",
        "/api/v1/world/characters/{agent_type}",
        "/api/v1/maps/{slug}",
    ),
    "forge owners and their repositories, read from the forge": (
        "/api/v1/orgs/{owner}/webhook",
        "/api/v1/orgs/{owner}/repos",
        "/api/v1/orgs/{owner}/runs",
        "/api/v1/orgs/{owner}/context-audit",
        "/api/v1/orgs/{owner}/needs",
        "/api/v1/standards/{owner}/{kind}/repos/{repo:path}",
        "/api/v1/github/repos",
        "/api/v1/github/discoverable-repos",
        "/api/v1/gitlab/discoverable-projects",
    ),
}

_HISTORY = "history"
_COLLABORATION = "collaboration"
_VISIBLE = "visible"
_UNSERVED = "not served"

_JOB = "who started a job, set its finish line or answered it: the people working on it"
_HANDOFF = "who handed a decision over, closed it or answered it: the team coordinates on it"
_MAP = "whose verdict a map part carries, who sent it, who uploaded the map: a shared review"
_OWNER = "a forge owner: an organization or account embry0 maintains"
_COMMIT = "configuration: the identity a job commits under"
_SLOP = "no route returns it: the slop meter answers scores"

#: Every column whose name says it holds a person, by kind. A history column names the GET routes that serve
#: it, each read as three callers in test_actor_fields.py; every other kind says why.
ACTOR_COLUMNS: dict[str, tuple[str, str | tuple[str, ...]]] = {
    # /audit's own rows. GET /issues/{id}/activity shows an issue's rows to the people working on it (collaboration).
    "audit_log.actor": (_HISTORY, ("/api/v1/audit",)),
    "config_revisions.actor": (
        _HISTORY,
        (
            "/api/v1/config/settings/effective",
            "/api/v1/config/settings/revisions",
            "/api/v1/config/settings/revisions/{revision_id}",
        ),
    ),
    "settings_documents.updated_by": (_HISTORY, ("/api/v1/config/settings", "/api/v1/config/settings/layers")),
    "progression_packs.created_by": (
        _HISTORY,
        (
            "/api/v1/progression/pack",
            "/api/v1/progression/pack/versions",
            "/api/v1/progression/pack/versions/{version}",
            "/api/v1/progression/pack/draft",
        ),
    ),
    "merge_approvals.decided_by": (_HISTORY, ("/api/v1/merge-approvals", "/api/v1/merge-approvals/{approval_id}")),
    "repo_config_revisions.actor": (_HISTORY, ("/api/v1/repos/{full_repo:path}/history",)),
    "repo_qa_configs.updated_by": (_HISTORY, ("/api/v1/repos/{full_repo:path}",)),
    "repo_setup_proposals.created_by": (_HISTORY, ("/api/v1/repos/{full_repo:path}/setup/proposal",)),
    "repo_setup_proposals.decided_by": (_HISTORY, ("/api/v1/repos/{full_repo:path}/setup/proposal",)),
    "credential_bindings.created_by": (
        _HISTORY,
        ("/api/v1/credentials/bindings", "/api/v1/repos/{full_repo:path}/credentials"),
    ),
    "organizations.created_by": (_HISTORY, ("/api/v1/orgs", "/api/v1/orgs/{owner}")),
    "standards.created_by": (
        _HISTORY,
        ("/api/v1/standards/{owner}", "/api/v1/standards/{owner}/{kind}", "/api/v1/standards/{owner}/{kind}/versions"),
    ),
    "jobs.dispatched_by": (_COLLABORATION, _JOB),
    "issue_inputs.answered_by": (_COLLABORATION, _JOB),
    "issue_inputs.skipped_by": (_COLLABORATION, _JOB),
    "job_ratings.rated_by": (_COLLABORATION, "who evaluated a quest's work"),
    "handoffs.from_actor": (_COLLABORATION, _HANDOFF),
    "handoffs.closed_by": (_COLLABORATION, _HANDOFF),
    "handoffs.to_email": (_COLLABORATION, "whom a decision was sent to, so two people do not act on it"),
    "asks.answered_by": (_COLLABORATION, _HANDOFF),
    "asks.recipient": (_COLLABORATION, "whom an ask goes to: what the chat assistant sends it to"),
    "knowledge_map_marks.marked_by": (_COLLABORATION, _MAP),
    "knowledge_map_sends.sent_by": (_COLLABORATION, _MAP),
    "knowledge_maps.uploaded_by": (_COLLABORATION, _MAP),
    "credentials.git_author_email": (_VISIBLE, _COMMIT),
    "credentials.git_author_name": (_VISIBLE, _COMMIT),
    "repo_preferences.git_author_email": (_VISIBLE, _COMMIT),
    "repo_preferences.git_author_name": (_VISIBLE, _COMMIT),
    "repo_preferences.owners": (_VISIBLE, "configuration: the forge logins a repository's decisions go to"),
    "credentials.verified_login": (_VISIBLE, "the forge account a token belongs to, not who did anything"),
    "issues.created_by": (_VISIBLE, "how an issue came in (user, webhook, linear-webhook, ...), never a person"),
    "organizations.owner": (_VISIBLE, _OWNER),
    "standards.owner": (_VISIBLE, _OWNER),
    "repo_standard_state.owner": (_VISIBLE, _OWNER),
    "org_discovery_runs.owner": (_VISIBLE, _OWNER),
    "org_needs.owner": (_VISIBLE, _OWNER),
    "org_needs_synced.owner": (_VISIBLE, _OWNER),
    "org_repo_facts.owner": (_VISIBLE, _OWNER),
    "knowledge_maps.owner": (_VISIBLE, _OWNER),
    "knowledge_map_marks.map_owner": (_VISIBLE, _OWNER),
    "knowledge_map_sends.map_owner": (_VISIBLE, _OWNER),
    "users.email": (_UNSERVED, "the admin-only users router; GET /people lists who signs in, not who did what"),
    "users.github_login": (_UNSERVED, "the admin-only users router; GET /people lists who signs in, not who did what"),
    "api_keys.created_by": (_UNSERVED, "GET /apikeys shows a caller who is not an admin only its own keys"),
    "credentials.created_by": (_UNSERVED, "no route returns it: CredentialOut leaves it out"),
    "proposal_decisions.decided_by": (_UNSERVED, "no route returns it: it only tells which proposals are answered"),
    "qa_image_tags.built_by": (_UNSERVED, "no route returns it"),
    "slop_meter_commits.author_email": (_UNSERVED, _SLOP),
    "slop_meter_commits.author_name": (_UNSERVED, _SLOP),
    "slop_meter_events.login": (_UNSERVED, _SLOP),
    "slop_meter_logins.email": (_UNSERVED, _SLOP),
    "slop_meter_logins.login": (_UNSERVED, _SLOP),
}

#: A column name that says it holds a person; a time or a counter about one (``last_login_at``) does not.
PERSON_COLUMN = re.compile(r"(^|_)(actor|by|email|login|author|person|recipient|owner)s?($|_)")
NOT_A_PERSON = re.compile(r"(_at|_count|_per_hour)$|_per_")


def _app() -> Any:
    from embry0.api.app import create_app

    with patch.dict(os.environ, {}, clear=True):
        return create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))  # type: ignore[call-arg]


def _paths(groups: dict[str, tuple[str, ...]]) -> set[str]:
    return {path for paths in groups.values() for path in paths}


def unclassified_routes(app: Any) -> tuple[set[str], set[str], set[str]]:
    """GET routes of no kind, routes of more than one, and listed routes the app does not serve."""
    served = {r.path for r in app_routes(app) if r.methods and "GET" in r.methods and r.path.startswith("/api/v1/")}
    kinds = [set(HISTORY_ROUTES), _paths(COLLABORATION_ROUTES), _paths(NO_ACTOR_ROUTES)]
    listed = set().union(*kinds)
    twice = {path for path in listed if sum(path in kind for kind in kinds) > 1}
    return served - listed, twice, listed - served


def test_every_get_route_is_history_collaboration_or_names_no_one() -> None:
    missing, twice, gone = unclassified_routes(_app())
    assert not missing, (
        f"GET routes nobody classified: {sorted(missing)}. A route that answers who changed something shows it "
        "as GET /audit does (embry0.api.actor_visibility) and gets a case in test_actor_fields.py; one that names "
        "people working together goes in COLLABORATION_ROUTES, any other in NO_ACTOR_ROUTES, each with why"
    )
    assert not twice, f"classified as more than one kind: {sorted(twice)}"
    assert not gone, f"classified but not served any more: {sorted(gone)}"


def test_the_route_inventory_fails_on_a_new_get_route() -> None:
    """The check is not quiet by construction: a route added to the app is reported until it is classified."""
    app = _app()
    extra = APIRouter()

    @extra.get("/approvals-for-a-test")
    async def approvals() -> dict[str, Any]:
        return {"approved_by": "someone@example.com"}

    app.include_router(extra, prefix="/api/v1")
    missing, _, _ = unclassified_routes(app)
    assert missing == {"/api/v1/approvals-for-a-test"}, missing


def test_every_history_column_is_served_only_by_routes_read_as_three_callers() -> None:
    collaboration = _paths(COLLABORATION_ROUTES)
    for column, (kind, detail) in ACTOR_COLUMNS.items():
        assert kind in (_HISTORY, _COLLABORATION, _VISIBLE, _UNSERVED), column
        if kind == _HISTORY:
            assert isinstance(detail, tuple) and detail, f"{column}: name the routes that serve it"
            assert set(detail) <= HISTORY_ROUTES, (
                f"{column}: served by routes no case reads: {set(detail) - HISTORY_ROUTES}"
            )
            assert not set(detail) & collaboration, f"{column}: a history column listed on a collaboration route"
        else:
            assert isinstance(detail, str) and detail, f"{column}: say why"


async def _person_columns(db: Any) -> set[str]:
    rows = await db.fetch(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
    )
    return {
        f"{r['table_name']}.{r['column_name']}"
        for r in rows
        if PERSON_COLUMN.search(r["column_name"]) and not NOT_A_PERSON.search(r["column_name"])
    }


@pytest.mark.requires_postgres
async def test_every_column_that_holds_a_person_is_classified(db_with_migrations: Any) -> None:
    found = await _person_columns(db_with_migrations)
    assert found, "the schema walk found no column at all: it read the wrong schema"
    missing, gone = found - set(ACTOR_COLUMNS), set(ACTOR_COLUMNS) - found
    assert not missing, (
        f"columns that hold a person and are not classified: {sorted(missing)}. List each in ACTOR_COLUMNS: history "
        "(shown as GET /audit shows actors, embry0.api.actor_visibility), collaboration, visible or not served"
    )
    assert not gone, f"listed but no longer in the schema: {sorted(gone)}"


@pytest.mark.requires_postgres
async def test_the_column_inventory_fails_on_a_new_column_that_holds_a_person(db_with_migrations: Any) -> None:
    await db_with_migrations.execute("CREATE TABLE approvals_for_a_test (id INT, approved_by TEXT, approved_at TEXT)")
    found = await _person_columns(db_with_migrations)
    assert found - set(ACTOR_COLUMNS) == {"approvals_for_a_test.approved_by"}
