"""Linear trigger + write-back (EMB-47).

Inbound: the Linear webhook posts Issue events to ``POST /api/v1/webhook/linear``;
when an issue carries the trigger label (default ``embry0``) and its project maps
to a repo, embry0 creates a tracked issue and auto-runs the pipeline — the Linear
sibling of :class:`embry0.services.github_sync.GitHubSyncService`.

Outbound: lifecycle comments (dispatched / completed with PR + QA verdict) are
posted back to the source Linear issue via GraphQL. Credentials stay
orchestrator-side only — ``LINEAR_API_KEY`` never enters a sandbox.

Reuses the GraphQL fetch/compose seed in :mod:`embry0.services.linear_dispatch`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from embry0.services.linear_dispatch import LINEAR_GRAPHQL_URL, compose_additional_context

logger = structlog.get_logger(__name__)

DEFAULT_TRIGGER_LABEL = "embry0"
# Label → per-agent model override applied to the dispatched job. Kept tiny and
# explicit; extend when more provider labels exist.
MODEL_LABELS: dict[str, dict[str, str]] = {
    "grok": {"triage": "grok-4.5", "developer": "grok-4.5", "review": "grok-4.5", "qa": "grok-4.5"},
}

_ISSUE_WITH_CONTEXT_QUERY = """\
query IssueForTrigger($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    description
    url
    labels { nodes { name } }
    project { name }
    team { key }
  }
}
"""

_COMMENT_MUTATION = """\
mutation CommentCreate($issueId: String!, $body: String!) {
  commentCreate(input: {issueId: $issueId, body: $body}) {
    success
  }
}
"""


@dataclass(frozen=True)
class LinearTriggerIssue:
    """The slice of a Linear issue the trigger decision + dispatch need."""

    linear_id: str  # Linear's UUID — commentCreate wants this
    identifier: str  # human key, e.g. EMB-48 — the dedupe key
    title: str
    description: str
    url: str
    labels: list[str]
    project: str
    team_key: str


class LinearSyncService:
    """Inbound Linear-webhook trigger + outbound issue comments."""

    def __init__(
        self,
        api_key: str,
        repo_map: dict[str, str],
        *,
        trigger_label: str = DEFAULT_TRIGGER_LABEL,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._repo_map = dict(repo_map)
        self._trigger_label = trigger_label
        self._http = http_client

    # ---- GraphQL ----------------------------------------------------------

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Linear personal API keys go bare in Authorization — the Bearer
        # prefix is only for OAuth tokens and 401s with personal keys.
        client = self._http or httpx.AsyncClient(timeout=30.0)
        try:
            resp = await client.post(
                LINEAR_GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers={"Authorization": self._api_key, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
            return body
        finally:
            if self._http is None:
                await client.aclose()

    async def fetch_trigger_issue(self, issue_id: str) -> LinearTriggerIssue:
        """Fetch the issue (by UUID or identifier) with labels + project.

        Webhook payloads carry label IDs, not names, and can lag edits — the
        authoritative trigger decision always re-reads the issue.
        """
        body = await self._graphql(_ISSUE_WITH_CONTEXT_QUERY, {"id": issue_id})
        issue = (body.get("data") or {}).get("issue")
        if not issue:
            raise ValueError(f"Linear issue {issue_id!r} not found: {json.dumps(body)[:300]}")
        return LinearTriggerIssue(
            linear_id=issue["id"],
            identifier=issue["identifier"],
            title=issue.get("title") or "",
            description=issue.get("description") or "",
            url=issue["url"],
            labels=[n["name"] for n in (issue.get("labels") or {}).get("nodes") or []],
            project=((issue.get("project") or {}).get("name")) or "",
            team_key=((issue.get("team") or {}).get("key")) or "",
        )

    async def create_issue(
        self,
        team_id: str,
        title: str,
        description: str,
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a Linear issue and return {id, identifier, url}, or None.

        RAV-657: used by the watcher to file DRAFT proposals. Deliberately
        never sets labels — applying the trigger label is the human
        acceptance step, and keeping this method label-free makes the
        watcher's human gate structural rather than behavioral.
        """
        mutation = """
        mutation($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier url }
          }
        }
        """
        issue_input: dict[str, Any] = {"teamId": team_id, "title": title, "description": description}
        if project_id:
            issue_input["projectId"] = project_id
        try:
            resp = await self._graphql(mutation, {"input": issue_input})
            payload = (resp.get("data") or {}).get("issueCreate") or {}
            if not payload.get("success"):
                logger.warning("linear_issue_create_rejected", title=title[:80], response=json.dumps(resp)[:300])
                return None
            issue: dict[str, Any] = payload["issue"]
            logger.info("linear_issue_created", identifier=issue.get("identifier"), title=title[:80])
            return issue
        except Exception as exc:
            logger.warning("linear_issue_create_failed", title=title[:80], error=str(exc))
            return None

    async def post_comment(self, linear_issue_id: str, body: str) -> bool:
        """Post a markdown comment on a Linear issue. Best-effort — never raises."""
        try:
            resp = await self._graphql(_COMMENT_MUTATION, {"issueId": linear_issue_id, "body": body})
            ok = bool(((resp.get("data") or {}).get("commentCreate") or {}).get("success"))
            if not ok:
                logger.warning("linear_comment_rejected", issue=linear_issue_id, response=json.dumps(resp)[:300])
            return ok
        except Exception as exc:
            logger.warning("linear_comment_failed", issue=linear_issue_id, error=str(exc))
            return False

    # ---- inbound trigger --------------------------------------------------

    def model_override_for(self, labels: list[str]) -> dict[str, str] | None:
        """Merged per-agent model override from any MODEL_LABELS present."""
        merged: dict[str, str] = {}
        for label in labels:
            merged.update(MODEL_LABELS.get(label.lower(), {}))
        return merged or None

    async def handle_webhook_event(
        self,
        payload: dict[str, Any],
        *,
        issues_repo: Any,
        issue_executor: Any,
        dashboard_base_url: str = "",
    ) -> dict[str, str]:
        """Process an inbound Linear webhook delivery.

        Trigger condition: an Issue create/update event whose issue carries the
        trigger label AND whose project (or team key) maps to a repo. Dedupe on
        the Linear identifier — label churn and webhook redelivery are no-ops
        after the first dispatch.
        """
        if payload.get("type") != "Issue" or payload.get("action") not in ("create", "update"):
            return {"status": "ignored", "reason": "not an issue create/update"}
        data = payload.get("data") or {}
        identifier = data.get("identifier") or data.get("id") or ""
        if not identifier:
            return {"status": "ignored", "reason": "no issue identifier in payload"}

        existing = await issues_repo.get_by_linear(str(identifier))
        if existing is not None:
            return {"status": "ignored", "reason": "already dispatched", "issue_id": existing["id"]}

        issue = await self.fetch_trigger_issue(str(identifier))
        # Payload key may have been the UUID — re-check dedupe on the human identifier.
        existing = await issues_repo.get_by_linear(issue.identifier)
        if existing is not None:
            return {"status": "ignored", "reason": "already dispatched", "issue_id": existing["id"]}

        if self._trigger_label not in {label.lower() for label in issue.labels}:
            return {"status": "ignored", "reason": f"no {self._trigger_label!r} label"}

        repo = self._repo_map.get(issue.project) or self._repo_map.get(issue.team_key) or ""
        if not repo:
            logger.warning("linear_trigger_unmapped", identifier=issue.identifier, project=issue.project)
            return {"status": "ignored", "reason": f"no repo mapping for project {issue.project!r}"}

        body = issue.description.strip()
        body = f"{body}\n\nLinear: {issue.url}" if body else f"Linear: {issue.url}"
        issue_id = await issues_repo.create(
            title=f"[{issue.identifier}] {issue.title}",
            body=body,
            labels=list(issue.labels),
            repo=repo,
            created_by="linear-webhook",
        )
        await issues_repo.update(
            issue_id,
            linear_identifier=issue.identifier,
            linear_issue_id=issue.linear_id,
            status="triaging",
        )

        agent_models = self.model_override_for(issue.labels)
        try:
            job_id = await issue_executor.execute(
                issue_id,
                additional_context=compose_additional_context(issue.identifier),
                agent_models=agent_models,
            )
        except Exception:
            logger.warning("linear_trigger_execute_failed", issue_id=issue_id, exc_info=True)
            return {"status": "accepted", "action": "issue_created_dispatch_failed", "issue_id": issue_id}

        logger.info(
            "linear_trigger_dispatched",
            identifier=issue.identifier,
            issue_id=issue_id,
            job_id=job_id,
            repo=repo,
            agent_models=agent_models or {},
        )
        dash = f"\n[Job dashboard]({dashboard_base_url.rstrip('/')}/jobs/{job_id})" if dashboard_base_url else ""
        models_note = f" Agents: `{json.dumps(agent_models)}`." if agent_models else ""
        await self.post_comment(
            issue.linear_id,
            f"**embry0 dispatched** — job `{job_id}` on `{repo}` (triage → develop → review → QA).{models_note}{dash}",
        )
        return {"status": "accepted", "action": "dispatched", "issue_id": issue_id, "job_id": job_id}

    # ---- outbound lifecycle write-back ------------------------------------

    async def post_job_outcome(self, issue: dict[str, Any], job: dict[str, Any]) -> None:
        """Comment the terminal job outcome on the source Linear issue.

        Called by IssueExecutor when a workflow finishes for an issue that has
        ``linear_issue_id``. Best-effort — failures only log.
        """
        linear_issue_id = issue.get("linear_issue_id")
        if not linear_issue_id:
            return
        status = job.get("status") or "unknown"
        lines = [f"**embry0 job `{job.get('job_id', '?')}` finished: {status}**"]
        if job.get("pr_url"):
            lines.append(f"Pull request: {job['pr_url']}")
        elif job.get("result_summary"):
            # RAV-601: non-code jobs deliver text, not a PR — surface the
            # finalize_output summary as the outcome.
            lines.append(f"Result:\n\n{str(job['result_summary'])[:4000]}")
        if job.get("error_message"):
            lines.append(f"Error: {job['error_message']}")
        if job.get("total_cost_usd") is not None:
            lines.append(f"Cost: ${job.get('total_cost_usd', 0):.2f}")
        await self.post_comment(str(linear_issue_id), "\n".join(lines))


def parse_repo_map(raw: str) -> dict[str, str]:
    """Parse the ``LINEAR_REPO_MAP`` config value (JSON object: project/team → repo)."""
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"LINEAR_REPO_MAP is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()):
        raise ValueError("LINEAR_REPO_MAP must be a JSON object of string → string")
    return parsed
