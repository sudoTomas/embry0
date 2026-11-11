"""What each audit row is about: the repositories and owners it names, one rule per action.

``GET /audit`` shows a key with a repository allowlist only the rows about
repositories its list covers (:mod:`embry0.api.audit_allowlist`). A row is
about the repositories its action names it by, and this module says, for
every action in the registry (:mod:`embry0.audit.actions`), where they are:

- ``repo``: ``details.repo``;
- ``settings_scope``: ``details.scope``, a settings document's scope, when it
  is a repository's (``repository:<slug>``);
- ``binding_scope``: ``details.scope_value``, when ``details.scope_kind`` is
  ``repo``;
- ``issue``: the row's ``issue_id`` column, else ``details.issue_id``, the
  issue's repository;
- ``job`` and ``followup_job``: ``details.job_id`` and
  ``details.followup_job_id``, the repository each job works on
  (:func:`~embry0.storage.repositories.jobs.job_repository_sql`);
- ``approval``: ``details.approval_id``, the merge approval's repository;
- ``handoff``: ``details.handoff_id``, the hand-off's repository.

A row that names no repository may name owners instead (``owner``,
``before`` and ``after`` in its details, an organization's settings scope,
an owner's credential binding): it is the owner's. A row that names neither
is the deployment's: an API key, a person, a credential, a host, a model, a
skill, an agent. An action listed with no source is always the deployment's.
A key with an allowlist reads neither an owner's row nor the deployment's,
whoever wrote it; the owners are listed so that every writer's detail that
names something is accounted for.

``tests/unit/audit/test_audit_subjects.py`` checks that every action, and
every retired name, is listed, and that each writer puts its row's subject
where its action's entry reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, cast

from embry0.audit.actions import AUDIT_ACTIONS, RETIRED_AUDIT_ACTIONS, AuditAction

#: Where a row names a repository.
RepoSource = Literal["repo", "settings_scope", "binding_scope", "issue", "job", "followup_job", "approval", "handoff"]
#: Where a row names an owner.
OwnerSource = Literal["owner", "settings_scope", "binding_scope", "before", "after"]


@dataclass(frozen=True, slots=True)
class Subject:
    """Where an action's rows name the repositories they are about, and the owners they name."""

    repos: tuple[RepoSource, ...] = ()
    owners: tuple[OwnerSource, ...] = ()


#: The deployment's: a row about no repository and no owner.
DEPLOYMENT: Final = Subject()

_REPO: Final = Subject(repos=("repo",))
_REPO_JOB: Final = Subject(repos=("repo", "job"))
_ISSUE: Final = Subject(repos=("issue",))
_ISSUE_JOB: Final = Subject(repos=("issue", "job"))
_JOB: Final = Subject(repos=("job", "followup_job"))
_OWNER: Final = Subject(owners=("owner",))
_SETTINGS: Final = Subject(repos=("settings_scope",), owners=("settings_scope",))

AUDIT_ACTION_SUBJECTS: Final[dict[AuditAction, Subject]] = {
    "user.login": DEPLOYMENT,
    "user.disabled": DEPLOYMENT,
    "user.enabled": DEPLOYMENT,
    "user.force_logout": DEPLOYMENT,
    "apikey.created": DEPLOYMENT,
    "apikey.revoked": DEPLOYMENT,
    "apikey.allowed_repos_changed": DEPLOYMENT,
    # A refusal names what the key does not cover; a key with an allowlist never reads one back (repo_allowlist).
    "apikey.repo_refused": DEPLOYMENT,
    "dev_mode_enabled": DEPLOYMENT,
    "kernel.ptrace_scope.flagged": DEPLOYMENT,
    "provider_config_updated": DEPLOYMENT,
    "provider_config_tested": DEPLOYMENT,
    "provider_connection_tested": DEPLOYMENT,
    "provider_catalog_refreshed": DEPLOYMENT,
    "integration_config_updated": DEPLOYMENT,
    # A repository's context names it; the global context does not.
    "context_config_updated": _REPO,
    "context_config_deleted": _REPO,
    "settings.updated": _SETTINGS,
    "settings.change_undone": _SETTINGS,
    # A repository's layer copied from its organization's names it; the environment's import names none.
    "settings.bootstrapped": _SETTINGS,
    "slop_meter.pruned": _REPO,
    "slop_meter.prune.refused": _REPO,
    "background_enrollment.blocked": _REPO,
    # A repository's variable names it; a global one does not.
    "environment.secret_revealed": _REPO,
    "credential.created": DEPLOYMENT,
    "credential.bound": Subject(repos=("binding_scope",), owners=("binding_scope",)),
    "credential.imported": DEPLOYMENT,
    "credential.status_changed": DEPLOYMENT,
    "credential.updated": DEPLOYMENT,
    "credential.rotated": DEPLOYMENT,
    "credential.verified": DEPLOYMENT,
    "credential.deleted": DEPLOYMENT,
    "host.created": DEPLOYMENT,
    "host.imported": DEPLOYMENT,
    "host.updated": DEPLOYMENT,
    "host.removed": DEPLOYMENT,
    "repo.auto_register": Subject(repos=("repo",), owners=("owner",)),
    "repo_docker_access_granted": _REPO,
    "repo_docker_access_revoked": _REPO,
    "repo.qa_config_set": _REPO,
    "repo.qa_config_deleted": _REPO,
    "repo.preferences_set": _REPO,
    "repo.areas_set": _REPO,
    "repo.areas_imported": _REPO,
    "repo.areas_map_applied": _REPO,
    "repo.areas_map_rejected": _REPO,
    "repo.preferences_cleared": _REPO,
    "repo.change_undone": _REPO,
    "repo.setup_applied": _REPO,
    "repo.setup_rejected": _REPO,
    "organization.register": _OWNER,
    "organization.probe": _OWNER,
    "organization.patch": _OWNER,
    "organization.status": _OWNER,
    "organization.discover": _OWNER,
    "organization.webhook_install": _OWNER,
    "organization.action": _OWNER,
    "organization.need_dismiss": _OWNER,
    "organization.need_reopen": _OWNER,
    # A run of one repository names it; a run of the owner's due repositories names the owner.
    "organization.context_audit_run": Subject(repos=("repo",), owners=("owner",)),
    "standard.updated": _OWNER,
    "standard.repo_mode_set": Subject(repos=("repo",), owners=("owner",)),
    "standard.audit_started": Subject(repos=("repo", "job"), owners=("owner",)),
    "progression.pack_activated": DEPLOYMENT,
    "progression.pack_draft_saved": DEPLOYMENT,
    "progression.pack_draft_deleted": DEPLOYMENT,
    "progression.pack_reset": DEPLOYMENT,
    "agent.skill_added": DEPLOYMENT,
    "agent.skill_removed": DEPLOYMENT,
    "skill_source.created": DEPLOYMENT,
    "skill_source.updated": DEPLOYMENT,
    "skill_source.synced": DEPLOYMENT,
    "skill_source.deleted": DEPLOYMENT,
    "inline_skill.written": DEPLOYMENT,
    "inline_skill.deleted": DEPLOYMENT,
    "issue.created": _ISSUE,
    "issue.updated": _ISSUE,
    "issue.status_changed": _ISSUE,
    "issue.triaged": _ISSUE,
    "issue.decomposed": _ISSUE,
    "issue.input_created": _ISSUE,
    "issue.pipeline_paused": _ISSUE_JOB,
    "issue.cancelled": _ISSUE,
    "issue.job_created": _ISSUE_JOB,
    "job_created": _REPO_JOB,
    "qa.job_created": _REPO_JOB,
    "onboard.job_created": _REPO_JOB,
    "pr_review.job_created": _REPO_JOB,
    "job.budget_refused": _ISSUE_JOB,
    "job.budget_raised": _ISSUE_JOB,
    "job.skipped_duplicate_pr": Subject(repos=("repo", "issue", "job")),
    "job.cancelled": _ISSUE_JOB,
    "job.done_when_edited": _JOB,
    "qa_image_build": _REPO,
    "watcher_stack_updated": _REPO,
    "watcher_stack_deleted": _REPO,
    "watcher_signature_masks_refused": _REPO,
    # A retry runs on the case's issue, which may have moved to another repository.
    "case_accepted": Subject(repos=("repo", "issue", "job")),
    "case_auto_accepted": _REPO_JOB,
    "case_closed": _REPO,
    "merge.decided": Subject(repos=("approval",)),
    "proposal.answered": _REPO_JOB,
    "job.review.evaluated": _JOB,
    "job.review.sent_back": _JOB,
    "job.review.rejected": _JOB,
    "job.review.restarted": _JOB,
    "job.review.dismissed": _JOB,
    "handoff.created": Subject(repos=("handoff",)),
    "handoff.returned": Subject(repos=("handoff",)),
    "handoff.cancelled": Subject(repos=("handoff",)),
    # The owners a map moved between.
    "map.owner_changed": Subject(owners=("before", "after")),
}


def subject_of(action: str) -> Subject:
    """What a row of ``action`` is about: a retired name's rows as the action that replaced it, and a name the
    registry does not know, or one this map does not list yet, as the deployment's: a key with a repository
    allowlist reads none of its rows rather than every read of the audit log failing
    (``test_audit_subjects.py`` keeps the map whole)."""
    current = RETIRED_AUDIT_ACTIONS.get(action, action)
    if current not in AUDIT_ACTIONS:
        return DEPLOYMENT
    return AUDIT_ACTION_SUBJECTS.get(cast(AuditAction, current), DEPLOYMENT)


def actions_by_source() -> dict[str, list[str]]:
    """For each repository source, the action names (retired ones included) whose rows name a repository there: what
    the narrowing query reads per action."""
    repos: dict[str, list[str]] = {}
    for action in sorted({*AUDIT_ACTIONS, *RETIRED_AUDIT_ACTIONS}):
        for source in subject_of(action).repos:
            repos.setdefault(source, []).append(action)
    return repos
