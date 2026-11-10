"""Every audit action says where its rows name the repositories and owners they are about, and every writer agrees.

``GET /audit`` narrows a key with a repository allowlist to the rows about its
repositories (:mod:`embry0.api.audit_allowlist`), reading each row where its
action's entry in :mod:`embry0.audit.subjects` says it names them. A row that
names a repository where its entry does not read is read as a row about no
repository, so the key that wrote it reads it whatever that repository is. So
every writer is scanned, as the action registry's test scans them
(``test_action_registry.py``): each detail it writes that names a repository,
an owner, or an item of one (:data:`NAMING`) must be one its action's entry
reads, or :data:`NOT_READ` says why the row needs no more.

A writer whose details this scan cannot read is listed in :data:`OPAQUE` with
the details it writes; a new one fails until it is.
"""

from __future__ import annotations

import ast
import pathlib
import typing
from collections.abc import Iterable

import pytest

import embry0
from embry0.api.schemas import IntegrationConfigUpdate, ProviderConfigUpdate
from embry0.audit.actions import AUDIT_ACTIONS, RETIRED_AUDIT_ACTIONS
from embry0.audit.subjects import AUDIT_ACTION_SUBJECTS, DEPLOYMENT, OWN_ACTIONS, OwnerSource, RepoSource, subject_of
from embry0.settings.scope import Scope
from embry0.storage.repositories import audit as audit_repository
from tests.unit.audit import test_action_registry as registry

_SRC = pathlib.Path(embry0.__file__).parent

#: The details each source reads.
REPO_KEYS: dict[str, frozenset[str]] = {
    "repo": frozenset({"repo"}),
    "settings_scope": frozenset({"scope"}),
    "binding_scope": frozenset({"scope_kind", "scope_value"}),
    "issue": frozenset({"issue_id"}),
    "job": frozenset({"job_id"}),
    "followup_job": frozenset({"followup_job_id"}),
    "approval": frozenset({"approval_id"}),
    "handoff": frozenset({"handoff_id"}),
}
OWNER_KEYS: dict[str, frozenset[str]] = {
    "owner": frozenset({"owner"}),
    "settings_scope": frozenset({"scope"}),
    "binding_scope": frozenset({"scope_kind", "scope_value"}),
    "before": frozenset({"before"}),
    "after": frozenset({"after"}),
}
#: Details that name a repository, an owner or an item of one.
NAMING: frozenset[str] = frozenset().union(*REPO_KEYS.values(), *OWNER_KEYS.values()) | {
    "case_id",
    "proposal_id",
    "decision_id",
    "pr_url",
    "child_issue_ids",
    "repositories",
    "allowed_repos",
    "results",
    "started",
    "skipped",
}

_THE_REPOSITORY_S = "names an item of the repository the row's details.repo names"
_KEYS = (
    "a key's list or its change: the deployment's row, read back only under the actor that wrote it; a key with an "
    "allowlist mints and changes no key"
)
_NOT_A_LAYER = "'global' or 'repo', not a settings scope: the repository is details.repo"

#: A detail a writer puts on a row that its action's entry does not read, and why the row needs no more.
NOT_READ: dict[tuple[str, str], str] = {
    ("apikey.repo_refused", "repo"): "a refusal's row, which a key with an allowlist never reads (repo_allowlist)",
    ("apikey.created", "allowed_repos"): _KEYS,
    ("apikey.created", "scope"): "the key's tier, not a settings scope",
    ("apikey.allowed_repos_changed", "before"): _KEYS,
    ("apikey.allowed_repos_changed", "after"): _KEYS,
    ("context_config_updated", "scope"): _NOT_A_LAYER,
    ("environment.secret_revealed", "scope"): _NOT_A_LAYER,
    ("credential.imported", "skipped"): "the environment's variables the import skipped: the deployment's row",
    ("repo.setup_applied", "proposal_id"): _THE_REPOSITORY_S,
    ("repo.setup_rejected", "proposal_id"): _THE_REPOSITORY_S,
    ("case_accepted", "case_id"): _THE_REPOSITORY_S,
    ("case_auto_accepted", "case_id"): _THE_REPOSITORY_S,
    ("case_closed", "case_id"): _THE_REPOSITORY_S,
    ("proposal.answered", "proposal_id"): _THE_REPOSITORY_S,
    ("job.skipped_duplicate_pr", "pr_url"): _THE_REPOSITORY_S,
    ("merge.decided", "pr_url"): "the approval's own pull request, in the approval's repository",
    ("handoff.created", "decision_id"): "the hand-off's own decision, in the hand-off's repository",
    ("handoff.returned", "decision_id"): "the hand-off's own decision, in the hand-off's repository",
    ("handoff.cancelled", "decision_id"): "the hand-off's own decision, in the hand-off's repository",
    ("issue.decomposed", "child_issue_ids"): "children are made in the issue's repository (_executor_outcomes)",
    ("organization.context_audit_run", "started"): (
        "with a repo, that repository's job alone; without, the owner's registered repositories, every one covered "
        "by a key that covers the owner as a whole, the only key that reads an owner's row of its own"
    ),
    ("organization.context_audit_run", "skipped"): "as started",
    ("organization.action", "results"): (
        "an owner's row, read only by the key that ran the action, on repositories its own body named"
    ),
    ("settings.bootstrapped", "repositories"): "the environment's import, written by embry0 itself: the deployment's",
}

#: Writer calls whose details this scan cannot read, by file and action, with the details each writes.
OPAQUE: dict[tuple[str, str], frozenset[str]] = {
    # The fields of the request that changed, as the route takes them.
    ("embry0/api/v1/config.py", "integration_config_updated"): frozenset(IntegrationConfigUpdate.model_fields),
    ("embry0/api/v1/config.py", "provider_config_updated"): frozenset(ProviderConfigUpdate.model_fields),
    ("embry0/api/v1/credentials.py", "credential.imported"): frozenset({"created", "bound", "skipped"}),
    # embry0.api.v1.settings._updated
    ("embry0/api/v1/settings.py", "settings.updated"): frozenset(
        {"scope", "revision_id", "generation", "keys", "source", "reason"}
    ),
    ("embry0/settings/bootstrap.py", "settings.bootstrapped"): frozenset(
        {"sections", "revision_id", "earlier", "kept"}
    ),
    ("embry0/execution/runtime/ptrace_scope.py", "kernel.ptrace_scope.flagged"): frozenset(
        {"value", "reason", "path", "remedy"}
    ),
    # embry0.orchestration.budget_gate.DispatchDecision.details, spread beside these.
    ("embry0/services/_executor_run.py", "job.budget_refused"): frozenset(
        {"spent_usd", "cap_usd", "actor", "dispatched", "limit_per_hour", "job_id", "limit", "message", "issue_id"}
    ),
    ("embry0/workflows/issue_to_pr/nodes/_interrupts.py", "job.budget_raised"): frozenset(
        {"job_id", "spent_usd", "cap_before_usd", "cap_after_usd", "extra_usd", "issue_id"}
    ),
    # embry0.api.v1.handoffs._audit writes the hand-off's id and its decision's.
    ("embry0/api/v1/handoffs.py", "handoff.returned"): frozenset({"handoff_id", "decision_id"}),
    ("embry0/api/v1/handoffs.py", "handoff.cancelled"): frozenset({"handoff_id", "decision_id"}),
}

#: Keyword arguments of a writer that are not details.
_NOT_DETAILS = frozenset({"action", "actor", "audit_log_path", "db", "trace_id", "details"})


def _detail_keys(node: ast.Call, where: str) -> frozenset[str] | None:
    """The details a writer call writes, the ``issue_id`` column included, or None when they are not literal here."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
    position = registry._WRITERS[name]  # noqa: SLF001
    column = {"issue_id"} if any(kw.arg == "issue_id" for kw in node.keywords) else set()
    details = next((kw.value for kw in node.keywords if kw.arg == "details"), None)
    rest = node.args[position + 1 :]
    if details is None and where.endswith("job_review.py") and rest:
        # embry0.api.v1.job_review._audit(request, action, job_id, **fields)
        return frozenset({"job_id", *(kw.arg for kw in node.keywords if kw.arg)})
    if details is None and rest:
        details = rest[0]
    if details is None:
        # embry0.api.v1._audit.audit(request, action, **details)
        if any(kw.arg is None for kw in node.keywords):
            return None
        return frozenset({kw.arg for kw in node.keywords if kw.arg and kw.arg not in _NOT_DETAILS} | column)
    if not isinstance(details, ast.Dict) or any(key is None for key in details.keys):
        return None
    keys = {key.value for key in details.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    return frozenset(keys | column)


class _Sites(registry._Scan):  # noqa: SLF001
    """The registry test's scan, with the details each writer call writes."""

    def __init__(self, path: pathlib.Path, tree: ast.Module) -> None:
        super().__init__(path, tree)
        self.keys_at: dict[int, frozenset[str] | None] = {}

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name in registry._WRITERS:  # noqa: SLF001
            self.keys_at[node.lineno] = _detail_keys(node, self.where)
        super().visit_Call(node)


def sites(paths: Iterable[tuple[pathlib.Path, str]]) -> list[tuple[str, str, frozenset[str] | None]]:
    """Each writer call in ``paths`` (a path and its source): its file and line, its action, and its details."""
    out: list[tuple[str, str, frozenset[str] | None]] = []
    for path, source in paths:
        tree = ast.parse(source)
        scan = _Sites(path, tree)
        scan.visit(tree)
        for where, action in scan.written:
            out.append((where, action, scan.keys_at.get(int(where.rsplit(":", 1)[1]))))
    return out


def problems(found: list[tuple[str, str, frozenset[str] | None]]) -> list[str]:
    """What a writer writes that its action's entry does not read, unexplained, and what this scan cannot read."""
    out: list[str] = []
    for where, action, keys in found:
        file = where.rsplit(":", 1)[0]
        if keys is None:
            keys = OPAQUE.get((file, action))
            if keys is None:
                out.append(f"{where} {action}: its details are not literal here; list them in OPAQUE")
                continue
        subject = subject_of(action)
        read = frozenset().union(
            *(REPO_KEYS[source] for source in subject.repos), *(OWNER_KEYS[source] for source in subject.owners)
        )
        unread = sorted(k for k in keys & NAMING if k not in read and (action, k) not in NOT_READ)
        if unread:
            out.append(f"{where} {action}: writes {unread}, which its entry in embry0/audit/subjects.py does not read")
    return out


def _source_files() -> list[tuple[pathlib.Path, str]]:
    return [(path, path.read_text()) for path in sorted(_SRC.rglob("*.py"))]


def test_every_action_and_every_retired_name_says_what_its_rows_are_about() -> None:
    assert set(AUDIT_ACTION_SUBJECTS) == AUDIT_ACTIONS, (
        f"actions without an entry: {sorted(AUDIT_ACTIONS - set(AUDIT_ACTION_SUBJECTS))}; "
        f"entries of no action: {sorted(set(AUDIT_ACTION_SUBJECTS) - AUDIT_ACTIONS)}"
    )
    for old, new in RETIRED_AUDIT_ACTIONS.items():
        assert subject_of(old) == AUDIT_ACTION_SUBJECTS[new], f"{old} is not read as {new}"
    assert subject_of("not-an-action") == DEPLOYMENT


def test_a_key_reads_back_its_own_rows_only_of_the_deployment_s_actions() -> None:
    """:data:`OWN_ACTIONS` opens a row about no repository and no owner; it names nothing else."""
    named = sorted(action for action in OWN_ACTIONS if subject_of(action) != DEPLOYMENT)
    assert named == [], f"OWN_ACTIONS lists actions whose rows name a repository or an owner: {named}"
    assert OWN_ACTIONS <= AUDIT_ACTIONS


def test_every_source_has_its_query_and_reads_the_details_listed_here() -> None:
    assert set(typing.get_args(RepoSource)) == set(audit_repository._REPO_SQL) == set(REPO_KEYS)  # noqa: SLF001
    assert set(typing.get_args(OwnerSource)) == set(audit_repository._OWNER_SQL) == set(OWNER_KEYS)  # noqa: SLF001
    for source, keys in [*REPO_KEYS.items(), *OWNER_KEYS.items()]:
        sql = audit_repository._REPO_SQL.get(source, "") + audit_repository._OWNER_SQL.get(source, "")  # noqa: SLF001
        assert all(f"'{key}'" in sql or key == "issue_id" for key in keys), f"{source} does not read {keys}: {sql}"


def test_a_settings_scope_is_read_as_the_settings_document_writes_it() -> None:
    assert str(Scope.parse("repository:acme/web")).startswith(audit_repository._REPOSITORY_SCOPE)  # noqa: SLF001
    assert str(Scope.parse("organization:acme")).startswith(audit_repository._ORGANIZATION_SCOPE)  # noqa: SLF001


def test_every_writer_names_its_rows_subject_where_its_action_s_entry_reads_it() -> None:
    found = sites(_source_files())
    assert len(found) > 100, f"the scan found {len(found)} writer calls: it measures little"
    assert problems(found) == []
    seen = {(where.rsplit(":", 1)[0], action) for where, action, keys in found if keys is None}
    stale = sorted(f"{file} {action}" for file, action in set(OPAQUE) - seen)
    assert stale == [], f"listed in OPAQUE but read here, or written no more: {stale}"
    excused = {action for action, _key in NOT_READ}
    unknown = sorted(excused - AUDIT_ACTIONS)
    assert unknown == [], f"NOT_READ names actions the registry does not list: {unknown}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            'async def f(db):\n    await emit_audit(db, "credential.created", details={"credential_id": 1, "repo": 2})\n',
            "writes ['repo'], which its entry in embry0/audit/subjects.py does not read",
        ),
        (
            'async def f(db, x):\n    await emit_audit(db, "repo.preferences_set", details=x)\n',
            "its details are not literal here; list them in OPAQUE",
        ),
        (
            'async def f(db):\n    await emit_audit(db, "merge.decided", details={"job_id": 1})\n',
            "writes ['job_id'], which its entry in embry0/audit/subjects.py does not read",
        ),
    ],
)
def test_the_scan_reports_a_writer_its_action_s_entry_does_not_read(source: str, expected: str) -> None:
    """Not quiet by construction: a planted writer naming a repository where its entry does not read it, and one
    whose details are not literal, are reported."""
    found = sites([(_SRC / "planted.py", source)])
    assert [problem.split(": ", 1)[1] for problem in problems(found)] == [expected], found
