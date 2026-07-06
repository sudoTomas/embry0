"""Operator dispatch: Linear ticket -> embry0 job (INT-655 W1b).

The pure functions here (fetch/compose/build) are the reusable seed of the
W1c Linear-trigger adapter; `main()` (Task 4) is the operator CLI on top.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
DEFAULT_REPO = "client-project/ai-quoting"
DEFAULT_PROFILE = "dev-python"

_ISSUE_QUERY = """\
query IssueByIdentifier($id: String!) {
  issue(id: $id) {
    identifier
    title
    description
    url
  }
}
"""


@dataclass
class LinearIssue:
    identifier: str
    title: str
    description: str
    url: str


def fetch_linear_issue(rav_id: str, api_key: str) -> LinearIssue:
    """Fetch a Linear issue by identifier (e.g. ``INT-655``).

    Linear personal API keys go bare in the Authorization header — the
    ``Bearer`` prefix is only for OAuth tokens and 401s with personal keys.
    """
    resp = httpx.post(
        LINEAR_GRAPHQL_URL,
        json={"query": _ISSUE_QUERY, "variables": {"id": rav_id}},
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        timeout=30.0,
    )
    resp.raise_for_status()
    body = resp.json()
    issue = (body.get("data") or {}).get("issue")
    if not issue:
        raise ValueError(f"Linear issue {rav_id} not found: {json.dumps(body)[:500]}")
    return LinearIssue(
        identifier=issue["identifier"],
        title=issue.get("title") or "",
        description=issue.get("description") or "",
        url=issue["url"],
    )


def compose_task(issue: LinearIssue) -> str:
    """Ticket -> the job's ``task`` text: [id] title, body, Linear URL."""
    parts = [f"[{issue.identifier}] {issue.title}"]
    if issue.description.strip():
        parts.append(issue.description.strip())
    parts.append(f"Linear: {issue.url}")
    return "\n\n".join(parts)


def compose_additional_context(identifier: str) -> str:
    """The embry0 addendum appended to the developer prompt.

    Rides ``JobCreateRequest.additional_context`` (already wired into the
    developer prompt); the cloned repo's CLAUDE.md is auto-loaded by the
    in-sandbox Claude CLI, so conventions only need a pointer, not a copy.
    """
    return (
        f"This job was dispatched from Linear ticket {identifier} by embry0.\n"
        "\n"
        "Repository conventions: follow the repo's CLAUDE.md. Use Conventional "
        f"Commits and include ({identifier}) in every commit subject.\n"
        "\n"
        "Self-check before opening the PR: read `.e0/dev.yaml` at the repo "
        "root and run the `check` command for each service whose files you "
        "changed (python -> ml/, node -> frontend/, jvm -> platform/). Fix "
        "failures and re-run until the check passes.\n"
        "\n"
        f"Pull request: target `main`; reference {identifier} in the PR body "
        "and state that the PR was opened autonomously by embry0. After "
        "opening it, add the `embry0` label to the PR (POST to the GitHub "
        "API `/repos/<owner>/<repo>/issues/<pr-number>/labels` with body "
        '`{"labels": ["embry0"]}` using the same authenticated access you '
        "used to open the PR). If labeling fails, note that in a PR comment "
        "and continue."
    )


def build_job_payload(issue: LinearIssue, repo: str, profile: str) -> dict[str, str]:
    """The exact ``POST /api/v1/jobs`` body for an operator dispatch."""
    return {
        "repo": repo,
        "task": compose_task(issue),
        "sandbox_profile": profile,
        "additional_context": compose_additional_context(issue.identifier),
    }
