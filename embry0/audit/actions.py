"""The audit action names: one registry, read by every writer and by the Audit filter.

Every action an ``audit_log`` row can carry is listed here once, with the
label the dashboard shows for it. The writers (``emit_audit`` and its
siblings) take an ``AuditAction``, so mypy refuses a name that is not listed,
and ``GET /audit/actions`` serves the list the Audit page filters by.
``tests/unit/audit/test_action_registry.py`` checks both directions: every
name written is listed, and every listed name is written somewhere.

A name is the string stored in the row, so it never changes: renaming one
would split one event's history across two names. Add new actions as
``noun.verb``; the older ``noun_verb`` names stay as they are.
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
    "budget_config_updated",
    "provider_config_updated",
    "provider_config_tested",
    "provider_connection_tested",
    "integration_config_updated",
    "context_config_updated",
    "context_config_deleted",
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
    "organization.register",
    "organization.probe",
    "organization.patch",
    "organization.status",
    "organization.discover",
    "organization.webhook_install",
    "organization.action",
    "organization.need_dismiss",
    "organization.need_reopen",
    "standard.updated",
    "standard.repo_mode_set",
    "standard.audit_started",
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
    "job.skipped_duplicate_pr",
    "job.cancelled",
    "qa_image_build",
    # Watcher, healing cases and merges
    "watcher_stack_updated",
    "watcher_stack_deleted",
    "case_accepted",
    "case_closed",
    "merge.decided",
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
    "budget_config_updated": "Budget config updated",
    "provider_config_updated": "Provider config updated",
    "provider_config_tested": "Provider tested",
    "provider_connection_tested": "Provider connection tested",
    "integration_config_updated": "Integrations updated",
    "context_config_updated": "Context updated",
    "context_config_deleted": "Context deleted",
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
    "organization.register": "Organization registered",
    "organization.probe": "Organization probed",
    "organization.patch": "Organization updated",
    "organization.status": "Organization status changed",
    "organization.discover": "Organization discovery started",
    "organization.webhook_install": "Organization webhook installed",
    "organization.action": "Organization action run",
    "organization.need_dismiss": "Organization need dismissed",
    "organization.need_reopen": "Organization need reopened",
    "standard.updated": "Standard updated",
    "standard.repo_mode_set": "Standard mode set for a repository",
    "standard.audit_started": "Standard audit started",
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
    "job.skipped_duplicate_pr": "Job skipped as a duplicate",
    "job.cancelled": "Job cancelled",
    "qa_image_build": "QA image build",
    "watcher_stack_updated": "Watcher stack updated",
    "watcher_stack_deleted": "Watcher stack deleted",
    "case_accepted": "Case accepted",
    "case_closed": "Case closed",
    "merge.decided": "Merge decided",
}

AUDIT_ACTIONS: Final[frozenset[str]] = frozenset(typing.get_args(AuditAction))
