"""Builders for canonical webhook payloads and their signatures."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from embry0.api.webhook_ingress import SignatureScheme


def sign(scheme: SignatureScheme, body: bytes, secret: str) -> str:
    """The header value a forge would present for ``body``."""
    if scheme is SignatureScheme.SHARED_SECRET:
        return secret
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}" if scheme is SignatureScheme.HMAC_SHA256_PREFIXED else digest


def smee_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap as smee.io relays it: the real body re-serialised into a string."""
    return {"payload": json.dumps(payload)}


# --- GitHub -----------------------------------------------------------------


def github_issue(
    *,
    action: str = "opened",
    number: int = 42,
    labels: list[str] | None = None,
    repo: str = "owner/repo",
    title: str = "Fix auth",
    body: str = "",
) -> dict[str, Any]:
    return {
        "action": action,
        "repository": {"full_name": repo},
        "issue": {
            "number": number,
            "title": title,
            "body": body,
            "html_url": f"https://github.com/{repo}/issues/{number}",
            "labels": [{"name": name} for name in (labels or [])],
        },
    }


def github_issue_comment(
    *,
    body: str,
    number: int = 42,
    repo: str = "owner/repo",
    login: str = "octocat",
    association: str = "OWNER",
    on_pull_request: bool = False,
) -> dict[str, Any]:
    issue: dict[str, Any] = {"number": number}
    if on_pull_request:
        issue["pull_request"] = {"url": f"https://api.github.com/repos/{repo}/pulls/{number}"}
    return {
        "action": "created",
        "repository": {"full_name": repo},
        "issue": issue,
        "comment": {
            "id": 1001,
            "body": body,
            "user": {"login": login},
            "author_association": association,
        },
    }


def github_pull_request(
    *,
    action: str = "opened",
    number: int = 7,
    branch: str = "embry0/iss-abcd1234-fix",
    repo: str = "owner/repo",
    merged: bool = False,
) -> dict[str, Any]:
    return {
        "action": action,
        "repository": {"full_name": repo},
        "pull_request": {
            "number": number,
            "html_url": f"https://github.com/{repo}/pull/{number}",
            "head": {"ref": branch},
            "merged": merged,
        },
    }


def github_review_pull_request(
    *,
    action: str = "ready_for_review",
    number: int = 7,
    repo: str = "acme/widgets",
    head_repo: str | None = "acme/widgets",
    head_ref: str = "feature/login",
    head_sha: str = "a" * 40,
    base_ref: str = "main",
    draft: bool | None = False,
    author: str = "octocat",
    state: str = "open",
    association: str = "COLLABORATOR",
    author_type: str = "User",
) -> dict[str, Any]:
    """A `pull_request` delivery with every field the reviewer reads (#30).

    ``head_repo=None`` is a fork whose repository was deleted: GitHub sends
    ``head.repo: null``. ``draft=None`` leaves the key out. ``association``
    is the pull request's ``author_association``; ``author_type`` is
    GitHub's ``user.type`` (``"Bot"`` for an app).
    """
    pr: dict[str, Any] = {
        "number": number,
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "title": "Add the login form",
        "body": "Adds the login form and its validation.",
        "state": state,
        "user": {"login": author, "type": author_type},
        "author_association": association,
        "head": {"ref": head_ref, "sha": head_sha, "repo": {"full_name": head_repo} if head_repo else None},
        "base": {"ref": base_ref, "sha": "b" * 40, "repo": {"full_name": repo}},
        "updated_at": "2026-10-15T10:00:00Z",
    }
    if draft is not None:
        pr["draft"] = draft
    return {"action": action, "repository": {"full_name": repo}, "sender": {"login": author}, "pull_request": pr}


# --- Linear -----------------------------------------------------------------


def linear_issue(
    *,
    action: str = "create",
    identifier: str = "ENG-1234",
    uuid: str = "uuid-1",
) -> dict[str, Any]:
    return {
        "type": "Issue",
        "action": action,
        "data": {"id": uuid, "identifier": identifier},
    }


# --- GitLab -----------------------------------------------------------------


def gitlab_issue(
    *,
    action: str = "open",
    iid: int = 11,
    labels: list[str] | None = None,
    project_path: str = "acme/products/fbr",
    title: str = "Fix the thing",
    previous_labels: list[str] | None = None,
) -> dict[str, Any]:
    """GitLab Issue Hook.

    Structurally unlike GitHub's, and the differences are the ones that break
    naive ports: the event kind is in the body, the issue lives under
    ``object_attributes``, labels carry ``title`` rather than ``name``, there
    is no ``labeled`` action (a label change arrives as ``update``), and the
    project path may contain nested subgroups.
    """
    payload: dict[str, Any] = {
        "object_kind": "issue",
        "event_type": "issue",
        "project": {"id": 1, "path_with_namespace": project_path},
        "object_attributes": {
            "iid": iid,
            "title": title,
            "description": "",
            "action": action,
            "url": f"https://gitlab.com/{project_path}/-/issues/{iid}",
        },
        "labels": [{"id": i, "title": name} for i, name in enumerate(labels or [])],
    }
    if previous_labels is not None:
        payload["changes"] = {
            "labels": {
                "previous": [{"id": i, "title": n} for i, n in enumerate(previous_labels)],
                "current": [{"id": i, "title": n} for i, n in enumerate(labels or [])],
            }
        }
    return payload


def gitlab_merge_request(
    *,
    action: str = "update",
    iid: int = 5,
    project_path: str = "acme/products/web",
    draft: bool | None = False,
    previous_draft: bool | None = True,
    legacy_wip: bool = False,
    source_project_id: int = 1,
    target_project_id: int = 1,
    sha: str = "c" * 40,
    state: str = "opened",
    username: str = "jdoe",
    user_id: int = 42,
    author_id: int | None = 51,
) -> dict[str, Any]:
    """GitLab Merge Request Hook (#30).

    ``legacy_wip`` spells the draft flag ``work_in_progress``, as GitLab did
    before ``draft`` existed, in ``object_attributes`` and in ``changes``.
    ``previous_draft=None`` sends no ``changes`` delta at all. ``user`` is
    whoever opened the merge request or marked it ready; the author is named
    only by ``object_attributes.author_id``, so the two ids differ by default.
    ``author_id=None`` leaves the key out.
    """
    key = "work_in_progress" if legacy_wip else "draft"
    attrs: dict[str, Any] = {
        "iid": iid,
        "title": "Add search",
        "description": "Adds search to the catalogue.",
        "action": action,
        "state": state,
        "url": f"https://gitlab.example.com/{project_path}/-/merge_requests/{iid}",
        "source_branch": "feature/search",
        "target_branch": "main",
        "source_project_id": source_project_id,
        "target_project_id": target_project_id,
        "last_commit": {"id": sha},
    }
    if author_id is not None:
        attrs["author_id"] = author_id
    if draft is not None:
        attrs[key] = draft
    payload: dict[str, Any] = {
        "object_kind": "merge_request",
        "event_type": "merge_request",
        "user": {"id": user_id, "username": username},
        "project": {"id": target_project_id, "path_with_namespace": project_path},
        "object_attributes": attrs,
    }
    if previous_draft is not None and draft is not None:
        payload["changes"] = {key: {"previous": previous_draft, "current": draft}}
    return payload


def gitlab_mr_note(
    *,
    note: str = "/embry0 review",
    note_id: int = 901,
    iid: int = 5,
    project_path: str = "acme/products/web",
    user_id: int = 42,
    username: str = "jdoe",
    system: bool = False,
    noteable_type: str = "MergeRequest",
    source_project_id: int = 1,
    target_project_id: int = 1,
    sha: str = "c" * 40,
) -> dict[str, Any]:
    """GitLab Note Hook on a merge request (#30)."""
    return {
        "object_kind": "note",
        "event_type": "note",
        "user": {"id": user_id, "username": username},
        "project": {"id": target_project_id, "path_with_namespace": project_path},
        "object_attributes": {"id": note_id, "note": note, "noteable_type": noteable_type, "system": system},
        "merge_request": {
            "iid": iid,
            "title": "Add search",
            "description": "Adds search to the catalogue.",
            "state": "opened",
            "url": f"https://gitlab.example.com/{project_path}/-/merge_requests/{iid}",
            "source_branch": "feature/search",
            "target_branch": "main",
            "source_project_id": source_project_id,
            "target_project_id": target_project_id,
            "last_commit": {"id": sha},
        },
    }


def alertmanager_alert(
    *,
    alertname: str = "ContainerKilled",
    status: str = "firing",
    severity: str = "critical",
    labels: dict | None = None,
    summary: str = "Container killed: acme-backend",
    description: str = "Container acme-backend has not been seen for over 60s.",
    starts_at: str = "2026-09-21T04:00:00.000Z",
    ends_at: str | None = None,
    fingerprint: str = "9f2b1c4e7a8d0e13",
    generator_url: str = "http://prometheus:9090/graph?g0.expr=up",
) -> dict:
    """One Alertmanager v4 webhook delivery (#296).

    Structurally faithful to what Alertmanager actually POSTs, including the
    zero `endsAt` it sends for an alert that has not resolved — a real value
    that must read as "no end time", not as a date in the year 1.
    """
    return {
        "version": "4",
        "groupKey": '{}:{alertname="' + alertname + '"}',
        "truncatedAlerts": 0,
        "status": status,
        "receiver": "embry0",
        "groupLabels": {"alertname": alertname},
        "commonLabels": {"alertname": alertname, "severity": severity},
        "commonAnnotations": {"summary": summary},
        "externalURL": "http://alertmanager:9093",
        "alerts": [
            {
                "status": status,
                "labels": {
                    "alertname": alertname,
                    "severity": severity,
                    **(labels if labels is not None else {"name": "acme-backend"}),
                },
                "annotations": {"summary": summary, "description": description},
                "startsAt": starts_at,
                "endsAt": ends_at or ("2026-09-21T05:00:00.000Z" if status == "resolved" else "0001-01-01T00:00:00Z"),
                "generatorURL": generator_url,
                "fingerprint": fingerprint,
            }
        ],
    }


def github_workflow_run(
    *,
    repo: str = "acme/backend",
    default_branch: str = "main",
    branch: str = "main",
    name: str = "CI",
    conclusion: str = "failure",
    status: str = "completed",
    run_id: int = 5150,
) -> dict:
    """A GitHub `workflow_run` delivery (#296)."""
    return {
        "action": status,
        "repository": {"full_name": repo, "default_branch": default_branch},
        "workflow_run": {
            "id": run_id,
            "name": name,
            "head_branch": branch,
            "status": status,
            "conclusion": conclusion,
            "created_at": "2026-09-21T03:50:00Z",
            "updated_at": "2026-09-21T04:02:00Z",
            "html_url": f"https://github.com/{repo}/actions/runs/{run_id}",
        },
    }
