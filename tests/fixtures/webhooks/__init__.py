"""Canonical inbound webhook payloads, shared across forges (#159).

Before this package every test hand-built its payload dict inline and
monkeypatched httpx its own way — there were no webhook fixtures at all, no
recorded deliveries, and no contract test against any platform's real shape.
Adding a third forge on that basis is how divergence gets in.

Payloads are minimal but structurally faithful: the keys the handlers actually
read, nested the way the forge nests them. `github_issue` and `gitlab_issue`
differ deliberately — GitHub puts the issue under `issue` with `labels[].name`
and a distinct `labeled` action; GitLab puts it under `object_attributes` with
`labels[].title`, no `labeled` action, and the delta in `changes.labels`.
"""

from embry0.api.webhook_ingress import SignatureScheme

from .payloads import (
    alertmanager_alert,
    github_issue,
    github_issue_comment,
    github_pull_request,
    github_review_pull_request,
    github_workflow_run,
    gitlab_issue,
    gitlab_merge_request,
    gitlab_mr_note,
    linear_issue,
    sign,
    smee_envelope,
)

__all__ = [
    "SignatureScheme",
    "alertmanager_alert",
    "github_issue",
    "github_issue_comment",
    "github_pull_request",
    "github_review_pull_request",
    "github_workflow_run",
    "gitlab_issue",
    "gitlab_merge_request",
    "gitlab_mr_note",
    "linear_issue",
    "sign",
    "smee_envelope",
]
