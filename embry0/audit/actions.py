"""The audit action names: one registry, read by every writer and by the Audit filter.

Every action an ``audit_log`` row can carry is listed here once, with the
label the dashboard shows for it. The writers (``emit_audit`` and its
siblings) take an ``AuditAction``, so mypy refuses a name that is not listed,
and ``GET /audit/actions`` serves the list the Audit page filters by.
``tests/unit/audit/test_action_registry.py`` checks both directions: every
name written is listed, and every listed name is written somewhere.

A name is the string stored in the row, so a row's name never changes. When
an action is renamed, its old name moves to :data:`RETIRED_AUDIT_ACTIONS`
with the name that replaced it: nothing writes it again, old rows keep it
and still read with a label, and a filter by the new name finds them too
(:func:`names_of`), so one event's history is not split across two names.
Add new actions as dotted names ending in their verb, ``noun.verb`` or
``noun.part.verb`` (``job.review.evaluated``); the older ``noun_verb`` names
stay as they are.
"""

from __future__ import annotations

import typing
from typing import Final, Literal

AuditAction = Literal[
    # Sign-in, users and API keys
    "user.login",
    "user.disabled",
    "user.enabled",
    "user.force_logout",
    "apikey.created",
    "apikey.revoked",
    "apikey.allowed_repos_changed",
    "apikey.repo_refused",
    "dev_mode_enabled",
    # Settings
    "provider_config_updated",
    "provider_config_tested",
    "provider_connection_tested",
    "provider_catalog_refreshed",
    "integration_config_updated",
    "context_config_updated",
    "context_config_deleted",
    "settings.updated",
    "settings.change_undone",
    "settings.bootstrapped",
    # The Slop-O-Meter removed what it kept of a repository that left its scope, or refused to (#165)
    "slop_meter.pruned",
    "slop_meter.prune.refused",
    # The background enrollment import is held by a repository name no settings layer can be stored under (#165)
    "background_enrollment.blocked",
    "environment.secret_revealed",
    # Credentials
    "credential.created",
    "credential.bound",
    "credential.imported",
    "credential.status_changed",
    "credential.updated",
    "credential.rotated",
    "credential.verified",
    "credential.deleted",
    # Hosts
    "host.created",
    "host.imported",
    "host.updated",
    "host.removed",
    # Repositories and organizations
    "repo.auto_register",
    "repo_docker_access_granted",
    "repo_docker_access_revoked",
    "repo.qa_config_set",
    "repo.qa_config_deleted",
    "repo.preferences_set",
    "repo.preferences_cleared",
    "repo.change_undone",
    "repo.setup_applied",
    "repo.setup_rejected",
    "organization.register",
    "organization.probe",
    "organization.patch",
    "organization.status",
    "organization.discover",
    "organization.webhook_install",
    "organization.action",
    "organization.need_dismiss",
    "organization.need_reopen",
    "organization.context_audit_run",
    "standard.updated",
    "standard.repo_mode_set",
    "standard.audit_started",
    # The progression content pack
    "progression.pack_activated",
    "progression.pack_draft_saved",
    "progression.pack_draft_deleted",
    "progression.pack_reset",
    # Agents and skills
    "agent.skill_added",
    "agent.skill_removed",
    "skill_source.created",
    "skill_source.updated",
    "skill_source.synced",
    "skill_source.deleted",
    "inline_skill.written",
    "inline_skill.deleted",
    # Issues
    "issue.created",
    "issue.updated",
    "issue.status_changed",
    "issue.triaged",
    "issue.decomposed",
    "issue.input_created",
    "issue.pipeline_paused",
    "issue.cancelled",
    # Jobs
    "issue.job_created",
    "job_created",
    "qa.job_created",
    "onboard.job_created",
    "pr_review.job_created",
    "job.budget_refused",
    # A person continued a job past its cap, letting it spend more (#171).
    "job.budget_raised",
    "job.skipped_duplicate_pr",
    "job.cancelled",
    # Done when (spec 2026-10-26): a person replaced a job's finish line.
    "job.done_when_edited",
    "qa_image_build",
    # Watcher, healing cases and merges
    "watcher_stack_updated",
    "watcher_stack_deleted",
    "case_accepted",
    # A watcher draft the stack's auto-accept bar accepted (spec 2026-10-25).
    "case_auto_accepted",
    "case_closed",
    "merge.decided",
    # Owner proposals (spec 2026-10-24)
    "proposal.answered",
    # Job review: a person's verdict on a finished job's work, and what they did with it
    "job.review.evaluated",
    "job.review.sent_back",
    "job.review.rejected",
    "job.review.restarted",
    "job.review.dismissed",
    # Decisions (spec 2026-10-27): a decision sent to a teammate
    "handoff.created",
    "handoff.returned",
    "handoff.cancelled",
    # Knowledge maps: an admin gave a map to another owner (#180)
    "map.owner_changed",
]

AUDIT_ACTION_LABELS: Final[dict[AuditAction, str]] = {
    "user.login": "Signed in",
    "user.disabled": "User disabled",
    "user.enabled": "User enabled",
    "user.force_logout": "Forced logout",
    "apikey.created": "API key created",
    "apikey.revoked": "API key revoked",
    "apikey.allowed_repos_changed": "API key repositories changed",
    "apikey.repo_refused": "API key refused a repository",
    "dev_mode_enabled": "Dev mode enabled",
    "provider_config_updated": "Provider config updated",
    "provider_config_tested": "Provider tested",
    "provider_connection_tested": "Provider connection tested",
    "provider_catalog_refreshed": "Provider model list read",
    "integration_config_updated": "Integrations updated",
    "context_config_updated": "Context updated",
    "context_config_deleted": "Context deleted",
    "settings.updated": "Settings document changed",
    "settings.change_undone": "Settings change undone",
    "settings.bootstrapped": "Settings imported from the environment",
    "slop_meter.pruned": "Slop-O-Meter data of a repository removed",
    "slop_meter.prune.refused": "Slop-O-Meter removal refused",
    "background_enrollment.blocked": "Background enrollment held by a repository name",
    "environment.secret_revealed": "Secret revealed",
    "credential.created": "Credential created",
    "credential.bound": "Credential bound",
    "credential.imported": "Credentials imported",
    "credential.status_changed": "Credential status changed",
    "credential.updated": "Credential updated",
    "credential.rotated": "Credential rotated",
    "credential.verified": "Credential verified",
    "credential.deleted": "Credential deleted",
    "host.created": "Host created",
    "host.imported": "Hosts imported",
    "host.updated": "Host updated",
    "host.removed": "Host removed",
    "repo.auto_register": "Repository registered by webhook",
    "repo_docker_access_granted": "Docker access granted",
    "repo_docker_access_revoked": "Docker access revoked",
    "repo.qa_config_set": "Test setup saved",
    "repo.qa_config_deleted": "Test setup removed",
    "repo.preferences_set": "Repository settings saved",
    "repo.preferences_cleared": "Repository settings cleared",
    "repo.change_undone": "Repository change undone",
    "repo.setup_applied": "Test setup proposal applied",
    "repo.setup_rejected": "Test setup proposal turned down",
    "organization.register": "Organization registered",
    "organization.probe": "Organization probed",
    "organization.patch": "Organization updated",
    "organization.status": "Organization status changed",
    "organization.discover": "Organization discovery started",
    "organization.webhook_install": "Organization webhook installed",
    "organization.action": "Organization action run",
    "organization.need_dismiss": "Organization need dismissed",
    "organization.need_reopen": "Organization need reopened",
    "organization.context_audit_run": "Context audits run by hand",
    "standard.updated": "Standard updated",
    "standard.repo_mode_set": "Standard mode set for a repository",
    "standard.audit_started": "Standard audit started",
    "progression.pack_activated": "Game content pack activated",
    "progression.pack_draft_saved": "Game content draft saved",
    "progression.pack_draft_deleted": "Game content draft discarded",
    "progression.pack_reset": "Game content reset to the shipped default",
    "agent.skill_added": "Skill added to an agent",
    "agent.skill_removed": "Skill removed from an agent",
    "skill_source.created": "Skill source added",
    "skill_source.updated": "Skill source updated",
    "skill_source.synced": "Skill source synced",
    "skill_source.deleted": "Skill source removed",
    "inline_skill.written": "Inline skill written",
    "inline_skill.deleted": "Inline skill removed",
    "issue.created": "Created",
    "issue.updated": "Updated",
    "issue.status_changed": "Status changed",
    "issue.triaged": "Triaged",
    "issue.decomposed": "Decomposed",
    "issue.input_created": "Question asked",
    "issue.pipeline_paused": "Pipeline paused",
    "issue.cancelled": "Cancelled",
    "issue.job_created": "Job created",
    "job_created": "Job created without an issue",
    "qa.job_created": "QA job created",
    "onboard.job_created": "Onboard job created",
    "pr_review.job_created": "Review job created",
    "job.budget_refused": "Job refused by budget",
    "job.budget_raised": "Job budget raised",
    "job.skipped_duplicate_pr": "Job skipped as a duplicate",
    "job.cancelled": "Job cancelled",
    "job.done_when_edited": "Job done-when list edited",
    "qa_image_build": "QA image build",
    "watcher_stack_updated": "Watcher stack updated",
    "watcher_stack_deleted": "Watcher stack deleted",
    "case_accepted": "Case accepted",
    "case_auto_accepted": "Case auto-accepted",
    "case_closed": "Case closed",
    "merge.decided": "Merge decided",
    "proposal.answered": "Proposal answered",
    "job.review.evaluated": "Job evaluated",
    "job.review.sent_back": "Job sent back",
    "job.review.rejected": "Job rejected",
    "job.review.restarted": "Job run again",
    "job.review.dismissed": "Job dismissed",
    "handoff.created": "Decision sent to a teammate",
    "handoff.returned": "Decision sent back",
    "handoff.cancelled": "Hand-off taken back",
    "map.owner_changed": "Map owner changed",
}

AUDIT_ACTIONS: Final[frozenset[str]] = frozenset(typing.get_args(AuditAction))

#: Names that older rows carry and nothing writes any more, each with the name that replaced it.
#: Reviewing a job was the world's (the game layer's) until it moved under /jobs/{job_id}; the fleet
#: budget was the budget_config row until it moved into the settings document (#171).
RETIRED_AUDIT_ACTIONS: Final[dict[str, AuditAction]] = {
    "budget_config_updated": "settings.updated",
    "quest.evaluated": "job.review.evaluated",
    "quest.sent_back": "job.review.sent_back",
    "quest.rejected": "job.review.rejected",
    "quest.restarted": "job.review.restarted",
    "quest.dismissed": "job.review.dismissed",
}


def names_of(action: str) -> tuple[str, ...]:
    """``action`` and every retired name it replaced: what a filter by ``action`` matches."""
    return (action, *sorted(old for old, new in RETIRED_AUDIT_ACTIONS.items() if new == action))
