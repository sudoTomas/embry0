# API Reference

Two-level API: **low-level graph execution** + **high-level job management**. All endpoints live under `/api/v1/` behind the frontend's nginx on port 8200.

Authentication: send `Authorization: Bearer <API_KEY>` on every request (omitted from the examples below for brevity). An empty `API_KEY` is only tolerated when a dev-mode flag is set — see [configuration.md](configuration.md).

See [architecture.md](architecture.md#api-structure) for the full route map.

## Issues

```bash
# Create an issue (with optional auto-triage and GitHub sync)
curl -X POST http://localhost:8200/api/v1/issues \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"title": "Fix the auth bug", "repo": "owner/repo", "auto_triage": true, "github_sync_enabled": true}'

# List issues
curl http://localhost:8200/api/v1/issues -H "X-Requested-With: XMLHttpRequest"

# Get issue with children and jobs
curl http://localhost:8200/api/v1/issues/{issue_id} -H "X-Requested-With: XMLHttpRequest"

# Answer a triage question
curl -X POST http://localhost:8200/api/v1/issues/{issue_id}/inputs/{input_id}/answer \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"answer": "The bug is in the JWT validation logic"}'
```

## Jobs (high-level)

```bash
# Create a job
curl -X POST http://localhost:8200/api/v1/jobs \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"repo": "owner/repo", "task": "Fix the auth bug in login.py"}'

# List jobs
curl http://localhost:8200/api/v1/jobs

# Cancel a job
curl -X POST http://localhost:8200/api/v1/jobs/{job_id}/cancel \
  -H "X-Requested-With: XMLHttpRequest"

# Submit a QA job (skips triage; needs branch + pipeline=qa). See docs/running-qa.md.
curl -X POST http://localhost:8200/api/v1/jobs \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"repo": "owner/repo", "branch": "main", "pipeline": "qa",
       "qa": {"acceptance_criteria": ["home page loads with no console errors"]}}'

# QA artifact endpoints (presigned-GET redirects + SSE log stream).
curl http://localhost:8200/api/v1/jobs/{job_id}/qa/attempts
curl http://localhost:8200/api/v1/jobs/{job_id}/qa/attempts/1/result
curl http://localhost:8200/api/v1/jobs/{job_id}/artifacts/screenshots/latest
curl "http://localhost:8200/api/v1/jobs/{job_id}/artifacts/logs/gateway?follow=true"
```

## Graph execution (low-level)

```bash
# List available workflows
curl http://localhost:8200/api/v1/graphs/workflows

# Execute a workflow
curl -X POST http://localhost:8200/api/v1/graphs/execute \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"workflow": "issue-to-pr", "input_state": {"repo": "owner/repo", "task": "..."}}'
```

## Runtime configuration

```bash
# Budget controls
curl http://localhost:8200/api/v1/config/budget
curl -X PUT http://localhost:8200/api/v1/config/budget \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"max_budget_per_job_usd": 25.0}'

# Context injection
curl http://localhost:8200/api/v1/config/context
curl -X PUT http://localhost:8200/api/v1/config/context \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"system_context": "Use TypeScript strict mode."}'

# Sandbox profiles
curl http://localhost:8200/api/v1/sandbox-profiles
curl -X POST http://localhost:8200/api/v1/sandbox-profiles \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"name": "java-17", "base_image": "embry0-sandbox-java:17", "memory": "12g"}'
```

## Environment variables

```bash
# List global environment variables (secrets masked)
curl http://localhost:8200/api/v1/environment/global \
  -H "X-Requested-With: XMLHttpRequest"

# Set global environment variables
curl -X PUT http://localhost:8200/api/v1/environment/global \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"variables": [{"key": "DATABASE_URL", "value": "postgres://...", "var_type": "secret"}]}'

# Reveal a masked secret (audit-logged)
curl http://localhost:8200/api/v1/environment/global/DATABASE_URL/reveal \
  -H "X-Requested-With: XMLHttpRequest"

# Per-repo environment (same pattern, scoped)
curl http://localhost:8200/api/v1/repos/owner/repo/environment \
  -H "X-Requested-With: XMLHttpRequest"

# Auto-detect env vars from repo's .env.example
curl http://localhost:8200/api/v1/repos/owner/repo/environment/detect \
  -H "X-Requested-With: XMLHttpRequest"
```

## Repository preferences

```bash
# Get per-repo sandbox profile override
curl http://localhost:8200/api/v1/repos/owner/repo/preferences \
  -H "X-Requested-With: XMLHttpRequest"

# Set sandbox profile + language hint for a repo
curl -X PUT http://localhost:8200/api/v1/repos/owner/repo/preferences \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"sandbox_profile": "java-17", "language_hint": "Java", "notes": "Uses Maven"}'
```

## Sandbox visibility

```bash
# List running sandbox containers (ops)
curl http://localhost:8200/api/v1/sandboxes/active \
  -H "X-Requested-With: XMLHttpRequest"
```

## WebSocket streaming

```javascript
const ws = new WebSocket('ws://localhost:8200/ws/jobs/{job_id}/events');
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // { type: "agent_started", agent: "developer", ... }
  // { type: "progress", message: "Editing src/auth/login.py", ... }
  // { type: "agent_completed", cost_usd: 0.42, ... }
  // Rich event types:
  // { type: "thinking", text: "...", node: "developer" }
  // { type: "tool_call", tool_name: "Edit", input: "main.py", node: "developer" }
  // { type: "tool_result", tool_use_id: "...", content: "...", is_error: false }
  // { type: "text", text: "...", node: "developer" }
  // { type: "cost_update", cost_usd: 1.23, tokens_in: 5000, tokens_out: 2000 }
};
```
