"""Every audit action says where its rows name the repositories and owners they are about, and every writer agrees.

``GET /audit`` narrows a key with a repository allowlist to the rows about its
repositories (:mod:`embry0.api.audit_allowlist`), reading each row where its
action's entry in :mod:`embry0.audit.subjects` says it names them. A writer
that names a repository somewhere its entry does not read would show a key a
row about a repository it does not read beside one it does. So every writer is
scanned, as the action registry's test scans them (``test_action_registry.py``),
and each detail it writes that names a repository, an owner, or an item of
one must be one its action's entry reads, or :data:`NOT_READ` says why the row
needs no more. A detail names one by its key (:data:`ITEM_KEYS`, and any key
shaped like a repository's or an owner's, :data:`NAME_SHAPE`: ``repo``,
``target_repo``, ``full_name``, ``owner``, ``source_owner`` …) or by its value
(a slug written as text, or an expression read from something so named:
``req.repo``, ``row["full_repo"]``, ``vars(req).get("repo")``, ``owner``).

What it cannot see: a value read from a variable whose own name says nothing
(``other = req.repo`` and then ``{"mirror_of": other}``). It reads each
value's own expression, not where the names in it were assigned, so a writer
that names a repository that way is caught only by its key, or by review.

A writer whose details this scan cannot read is listed in :data:`OPAQUE` with
the details it writes; a new one fails until it is.
"""

from __future__ import annotations

import ast
import pathlib
import re
import typing
from collections.abc import Iterable, Mapping

import pytest

import embry0
from embry0.api.schemas import IntegrationConfigUpdate, ProviderConfigUpdate
from embry0.audit.actions import AUDIT_ACTIONS, RETIRED_AUDIT_ACTIONS
from embry0.audit.subjects import AUDIT_ACTION_SUBJECTS, DEPLOYMENT, OwnerSource, RepoSource, subject_of
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
#: Details that name an item of a repository or an owner, or a scope, by their key.
ITEM_KEYS: frozenset[str] = frozenset().union(*REPO_KEYS.values(), *OWNER_KEYS.values()) | {
    "case_id",
    "proposal_id",
    "decision_id",
    "pr_url",
    "child_issue_ids",
    "allowed_repos",
    "results",
    "started",
    "skipped",
}
#: A key, or an identifier a value is read from, shaped like a repository's or an owner's name.
NAME_SHAPE = re.compile(
    r"(?:^|_)(?:repo|repos|repository|repositories|owner|owners|project|projects|namespace|org|orgs|organization)$"
    r"|^(?:full_name|full_repo|path_with_namespace|stack)$"
)
#: Text shaped like a repository's slug, ``owner/name`` (a GitLab subgroup's ``group/sub/name`` too).
SLUG_SHAPE = re.compile(r"^[A-Za-z0-9][\w.-]*(?:/[\w.-]+)+$")


def _names_by_value(value: ast.expr | None) -> bool:
    """Whether a detail's value names a repository or an owner: a slug written as text, or anything read from a name,
    an attribute or a key shaped like one."""
    if value is None:
        return False
    for node in ast.walk(value):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and SLUG_SHAPE.match(node.value):
            return True
        if isinstance(node, ast.Name) and NAME_SHAPE.search(node.id):
            return True
        if isinstance(node, ast.Attribute) and NAME_SHAPE.search(node.attr):
            return True
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            key = node.slice.value
            if isinstance(key, str) and NAME_SHAPE.search(key):
                return True
        if isinstance(node, ast.Call) and _reads_a_named_key(node):
            return True
    return False


def _reads_a_named_key(call: ast.Call) -> bool:
    """A key read as a subscript is: ``x.get("repo")`` (``vars(req).get("repo")`` among them) and
    ``getattr(x, "repo")``, with a key shaped like a repository's or an owner's."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "get" and call.args:
        key = call.args[0]
    elif isinstance(func, ast.Name) and func.id == "getattr" and len(call.args) >= 2:
        key = call.args[1]
    else:
        return False
    return isinstance(key, ast.Constant) and isinstance(key.value, str) and bool(NAME_SHAPE.search(key.value))


def naming(details: Mapping[str, ast.expr | None]) -> set[str]:
    """The details that name a repository, an owner or an item of one, by their key or by their value."""
    return {
        key for key, value in details.items() if key in ITEM_KEYS or NAME_SHAPE.search(key) or _names_by_value(value)
    }


_THE_REPOSITORY_S = "names an item of the repository the row's details.repo names"
_KEYS = "a key's list or its change: the deployment's row, which no key with an allowlist reads"
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
    ("repo.areas_map_applied", "proposal_id"): _THE_REPOSITORY_S,
    ("repo.areas_map_rejected", "proposal_id"): _THE_REPOSITORY_S,
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
        "with a repo, that repository's job alone; without, the owner's due repositories, and the row is the "
        "owner's, which no key with an allowlist reads"
    ),
    ("organization.context_audit_run", "skipped"): "as started",
    ("organization.action", "results"): "the repositories of an owner's row, which no key with an allowlist reads",
    ("background_enrollment.blocked", "features"): "the features of the row's own repository (details.repo)",
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
    # embry0.services.areas_import.import_one's record(repo, {...}).
    ("embry0/api/app.py", "repo.areas_imported"): frozenset({"repo", "revision_id", "left_out"}),
    # embry0.services.watcher_stacks.audit_refused_signature_masks's write({...}).
    ("embry0/api/app.py", "watcher_signature_masks_refused"): frozenset({"repo", "kept", "refused"}),
}

#: Keyword arguments of a writer that are not details.
_NOT_DETAILS = frozenset({"action", "actor", "audit_log_path", "db", "trace_id", "details"})


def _details(node: ast.Call, where: str) -> dict[str, ast.expr | None] | None:
    """The details a writer call writes, each with its value, the ``issue_id`` column included; None when they are
    not literal here."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
    position = registry._WRITERS[name]  # noqa: SLF001
    column = {kw.arg: kw.value for kw in node.keywords if kw.arg == "issue_id"}
    details = next((kw.value for kw in node.keywords if kw.arg == "details"), None)
    rest = node.args[position + 1 :]
    if details is None and where.endswith("job_review.py") and rest:
        # embry0.api.v1.job_review._audit(request, action, job_id, **fields)
        return {"job_id": rest[0], **{kw.arg: kw.value for kw in node.keywords if kw.arg}}
    if details is None and rest:
        details = rest[0]
    if details is None:
        # embry0.api.v1._audit.audit(request, action, **details)
        if any(kw.arg is None for kw in node.keywords):
            return None
        return {kw.arg: kw.value for kw in node.keywords if kw.arg and kw.arg not in _NOT_DETAILS} | column
    if not isinstance(details, ast.Dict) or any(key is None for key in details.keys):
        return None
    pairs = {
        key.value: value
        for key, value in zip(details.keys, details.values, strict=True)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    return pairs | column


class _Sites(registry._Scan):  # noqa: SLF001
    """The registry test's scan, with the details each writer call writes."""

    def __init__(self, path: pathlib.Path, tree: ast.Module) -> None:
        super().__init__(path, tree)
        self.details_at: dict[int, dict[str, ast.expr | None] | None] = {}

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name in registry._WRITERS:  # noqa: SLF001
            self.details_at[node.lineno] = _details(node, self.where)
        super().visit_Call(node)


Site = tuple[str, str, "dict[str, ast.expr | None] | None"]


def sites(paths: Iterable[tuple[pathlib.Path, str]]) -> list[Site]:
    """Each writer call in ``paths`` (a path and its source): its file and line, its action, and its details."""
    out: list[Site] = []
    for path, source in paths:
        tree = ast.parse(source)
        scan = _Sites(path, tree)
        scan.visit(tree)
        for where, action in scan.written:
            out.append((where, action, scan.details_at.get(int(where.rsplit(":", 1)[1]))))
    return out


def problems(found: list[Site]) -> list[str]:
    """What a writer writes that its action's entry does not read, unexplained, and what this scan cannot read."""
    out: list[str] = []
    for where, action, details in found:
        file = where.rsplit(":", 1)[0]
        if details is None:
            listed = OPAQUE.get((file, action))
            if listed is None:
                out.append(f"{where} {action}: its details are not literal here; list them in OPAQUE")
                continue
            details = dict.fromkeys(listed)
        subject = subject_of(action)
        read = frozenset().union(
            *(REPO_KEYS[source] for source in subject.repos), *(OWNER_KEYS[source] for source in subject.owners)
        )
        unread = sorted(k for k in naming(details) if k not in read and (action, k) not in NOT_READ)
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


def test_an_action_the_map_does_not_list_reads_as_the_deployment_s(monkeypatch: pytest.MonkeyPatch) -> None:
    """A registered action added without its entry reads as the deployment's, so a key with an allowlist reads none of
    its rows, and the narrowed read still runs: it does not fail every read of the audit log."""
    from embry0.audit import subjects

    listed = dict(subjects.AUDIT_ACTION_SUBJECTS)
    listed.pop("repo.preferences_set")
    monkeypatch.setattr(subjects, "AUDIT_ACTION_SUBJECTS", listed)
    assert subjects.subject_of("repo.preferences_set") == DEPLOYMENT
    assert "repo.preferences_set" not in subjects.actions_by_source().get("repo", [])


def test_every_source_has_its_query_and_reads_the_details_listed_here() -> None:
    assert set(typing.get_args(RepoSource)) == set(audit_repository._REPO_SQL) == set(REPO_KEYS)  # noqa: SLF001
    assert set(typing.get_args(OwnerSource)) == set(OWNER_KEYS)
    for source, keys in REPO_KEYS.items():
        sql = audit_repository._REPO_SQL[source]  # noqa: SLF001
        assert all(f"'{key}'" in sql or key == "issue_id" for key in keys), f"{source} does not read {keys}: {sql}"


def test_a_settings_scope_is_read_as_the_settings_document_writes_it() -> None:
    assert str(Scope.parse("repository:acme/web")).startswith(audit_repository._REPOSITORY_SCOPE)  # noqa: SLF001


def test_a_detail_names_a_repository_or_an_owner_by_its_key_or_its_value() -> None:
    """The shapes the scan recognises, and some it must not mistake for one."""

    def value(text: str) -> ast.expr:
        return ast.parse(text, mode="eval").body

    named = {
        "repo": None,
        "target_repo": value("x"),
        "source_owner": None,
        "full_name": None,
        "path_with_namespace": None,
        "where": value("req.repo"),
        "at": value("row['full_repo']"),
        "who": value("owner"),
        "slug_text": value("'globex/gizmos'"),
        "nested": value("[{'r': s.repository} for s in x]"),
        "fetched": value("vars(req).get('repo')"),
        "looked_up": value("row.get('full_repo', '')"),
        "attribute": value("getattr(req, 'target_repo', None)"),
    }
    plain = {
        "label": value("req.label"),
        "name": value("key.name"),
        "path": value("'/tmp/x'"),
        "kind": value("'a b/c'"),
        "fetched": value("vars(req).get('label')"),
        "attribute": value("getattr(req, 'name', None)"),
    }
    assert naming(named) == set(named), sorted(set(named) - naming(named))
    assert naming(plain) == set(), naming(plain)


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
        # The repository moved to a key the entry does not read.
        (
            'async def f(db, req):\n    await emit_audit(db, "qa_image_build", details={"target_repo": req.repo})\n',
            "writes ['target_repo'], which its entry in embry0/audit/subjects.py does not read",
        ),
        # A repository added to a row of the deployment's.
        (
            "async def f(request):\n"
            '    await _audit(request, "credential.rotated", {"credential_id": 1, "full_repo": "globex/gizmos"})\n',
            "writes ['full_repo'], which its entry in embry0/audit/subjects.py does not read",
        ),
        # A second repository beside the one the entry reads.
        (
            "async def f(db, req):\n"
            '    await emit_audit(db, "qa_image_build", details={"repo": req.repo, "source_repo": "globex/gizmos"})\n',
            "writes ['source_repo'], which its entry in embry0/audit/subjects.py does not read",
        ),
        # A repository under a key that does not look like one, found by its value.
        (
            'async def f(db, req):\n    await emit_audit(db, "host.created", details={"host_id": 1, "target": req.repo})\n',
            "writes ['target'], which its entry in embry0/audit/subjects.py does not read",
        ),
        # The same, read with .get, as a subscript is read.
        (
            "async def f(db, req):\n"
            '    await emit_audit(db, "host.created", details={"host_id": 1, "target": vars(req).get("repo")})\n',
            "writes ['target'], which its entry in embry0/audit/subjects.py does not read",
        ),
    ],
)
def test_the_scan_reports_a_writer_its_action_s_entry_does_not_read(source: str, expected: str) -> None:
    """Not quiet by construction: a planted writer naming a repository where its entry does not read it, by its key
    or by its value, and one whose details are not literal, are reported."""
    found = sites([(_SRC / "planted.py", source)])
    assert [problem.split(": ", 1)[1] for problem in problems(found)] == [expected], found
