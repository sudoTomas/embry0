<div align="center">

# Legion

**Autonomous Agent Orchestration Engine**

*LangGraph + Claude Agent SDK*

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)](https://python.org)
[![React 19](https://img.shields.io/badge/react-19-61DAFB?logo=react&logoColor=white)](https://react.dev)
[![LangGraph](https://img.shields.io/badge/langgraph-orchestration-06b6d4)](https://github.com/langchain-ai/langgraph)
[![License](https://img.shields.io/badge/license-proprietary-f97316)]()

</div>

---

Legion is a production-grade agent orchestration engine that autonomously resolves GitHub issues by dispatching AI agents through a configurable pipeline. It uses **LangGraph** for workflow orchestration and **Claude Agent SDK** for agent execution inside isolated Docker sandboxes.

**Core principle: no customer code retention.** All code lives exclusively inside sandbox containers. When a container is destroyed, customer code is gone.

## How It Works

```mermaid
graph TB
    subgraph Input
        GH["GitHub Webhook"]
        API["REST API"]
    end

    subgraph Legion["Legion Orchestrator"]
        TR["Triage Agent<br/><small>LLM-based pipeline config</small>"]
        LG["LangGraph Engine<br/><small>StateGraph + Checkpointing</small>"]
    end

    subgraph Sandbox["Docker Sandbox (per job)"]
        DEV["Developer Agent<br/><small>Code + Git + PR</small>"]
        REV["Reviewer Agent<br/><small>Code review</small>"]
    end

    subgraph Output
        PR["Pull Request"]
        WS["WebSocket<br/><small>Live events</small>"]
        DB[("PostgreSQL<br/><small>Jobs + Checkpoints</small>")]
    end

    GH -->|webhook| TR
    API -->|job request| TR
    TR --> LG
    LG -->|docker exec| DEV
    DEV --> REV
    REV -->|rejected| DEV
    REV -->|approved| PR
    LG --> WS
    LG --> DB

    style Legion fill:#0f1419,stroke:#06b6d4,color:#e4e4e7
    style Sandbox fill:#1a1a2e,stroke:#f59e0b,color:#e4e4e7
```

## Architecture

Legion runs as a Docker Compose stack with 4 services:

| Service | Purpose |
|---------|---------|
| **Orchestrator** | FastAPI + LangGraph — manages jobs, runs pipelines, streams events |
| **PostgreSQL** | Jobs, traces, checkpoints, sandbox profiles, budget/context config |
| **DinD** | Docker-in-Docker — runs isolated sandbox containers for agent execution |
| **Frontend** | React SPA — execution dashboard, pipeline visualization, configuration |

```mermaid
graph LR
    subgraph Stack["Docker Compose Stack"]
        FE["Frontend<br/>nginx :8200"]
        ORC["Orchestrator<br/>FastAPI :8000"]
        PG["PostgreSQL :5432"]
        DIND["DinD"]
        
        subgraph Proxies["sandbox-restricted network"]
            AP["Auth Proxy"]
            GP["Git Proxy"]
            GHP["GitHub API Proxy"]
        end

        S1["Sandbox A"]
        S2["Sandbox B"]
    end

    FE <-->|reverse proxy| ORC
    ORC <--> PG
    ORC -->|docker exec| DIND
    DIND --> S1
    DIND --> S2
    S1 -.-> AP
    S1 -.-> GP
    S2 -.-> GHP

    style Stack fill:#09090b,stroke:#06b6d4,color:#e4e4e7
    style Proxies fill:#1b4332,stroke:#2d6a4f,color:#e4e4e7
```

### Security Model

- **Sandbox isolation** — Each job runs in a Docker container with `--cap-drop=ALL`, `--security-opt=no-new-privileges`
- **No customer code retention** — Sandbox clones repo internally; code is deleted when container is destroyed
- **Credential injection via proxies** — API keys and tokens never enter the sandbox; three proxy services inject credentials transparently
- **Dynamic network switching** — Agents get internet access only when needed (research), otherwise network-restricted
- **Safety patterns** — 34 blocked command patterns with NFKC unicode normalization, Glob restriction, and symlink defense via `os.path.realpath()` prevent dangerous bash operations

## The Issue-to-PR Pipeline

```mermaid
stateDiagram-v2
    [*] --> Triage
    
    Triage --> Developer: proceed
    Triage --> AwaitInput: needs_info
    Triage --> Split: too_large

    Developer --> Reviewer
    Reviewer --> Output: approved
    Reviewer --> Developer: changes_requested (retry)
    Output --> [*]
```

The **triage agent** uses an LLM to analyze each issue and configure the pipeline:
- **Confidence scoring** — Low confidence triggers a request for more information
- **Issue splitting** — Oversized tasks are autonomously decomposed
- **Pipeline customization** — Model tiers, validator modes, feedback loops, and sandbox profiles configured per job

The **developer agent** owns the full lifecycle: code changes, git operations, and PR creation. It uses Claude Code skills (e.g., superpowers) for structured workflows including sub-agent dispatch and worktree management.

## Issues & Human-in-the-Loop

Legion includes a full-featured **issue tracker** with optional GitHub two-way sync:

- **Create issues** from the dashboard or receive them via GitHub webhook
- **Triage agent** analyzes issues, asks clarifying questions if needed, or decomposes complex issues into subtasks
- **Human-in-the-loop** — when the agent needs more info, the pipeline pauses (`awaiting_input`). Questions are dispatched to:
  - **Dashboard** — questions panel with inline answer inputs
  - **Telegram** — per-question messages with reply-to-message matching
  - **GitHub** — issue comment with numbered questions
- When all blocking questions are answered (from any channel), the pipeline resumes automatically
- **Board + List views** with drag-and-drop, filters, and animated agent indicators

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.12+
- Node.js 20+ (for frontend development)

### 1. Clone and configure

```bash
git clone https://github.com/Alchymie-Labs/legion.git
cd legion
cp .env.example .env
# Edit .env with your credentials:
#   PROVIDER_MODE=anthropic_api (or claude_max)
#   ANTHROPIC_API_KEY=sk-ant-...
#   GITHUB_TOKEN=ghp_...
```

### 2. Install the CLI

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 3. Environment Secrets

Legion encrypts per-repo secret environment variables at rest using Fernet. Set an encryption key before starting the stack:

```bash
# Generate a strong key (or use your favorite secret manager)
openssl rand -hex 32
```

Add to `.env`:
```
ENVIRONMENT_SECRET_KEY=<generated-value>
```

If unset, Legion logs a warning and uses an insecure default — DO NOT use that in production. Rotating the key will make previously-encrypted secrets undecryptable; the orchestrator will log `secret_decryption_failed` for each affected key on the next job that needs them.

Per-repo and global env vars are managed through the `/environments` page in the dashboard.

### 4. Start the stack

```bash
legion start
```

This builds all images, starts PostgreSQL + DinD + Orchestrator + Frontend, waits for health checks, and builds the sandbox image inside DinD.

### 5. Open the dashboard

```
http://localhost:8200
```

## CLI Reference

### Production Stack (`legion`)

```bash
legion start              # Start the full stack
legion start --port 8201  # Start on a custom port
legion stop               # Stop the stack
legion build              # Build images (clean, no cache)
legion build --cached     # Build with Docker cache
legion build-sandbox      # Rebuild sandbox image inside DinD
legion health             # Check stack health
legion config             # Validate and display config (secrets masked)
legion purge              # Remove all Docker artifacts
legion purge --volumes    # Remove only volumes
```

### Development (`./lab`)

```bash
./lab up      # Start the dev container
./lab down    # Stop the dev container
./lab claude  # Open Claude Code in the container (default)
```

## API

Two-level API: **low-level graph execution** + **high-level job management**.

### Issues

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

### Jobs (High-Level)

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
```

### Graph Execution (Low-Level)

```bash
# List available workflows
curl http://localhost:8200/api/v1/graphs/workflows

# Execute a workflow
curl -X POST http://localhost:8200/api/v1/graphs/execute \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"workflow": "issue-to-pr", "input_state": {"repo": "owner/repo", "task": "..."}}'
```

### Configuration

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
  -d '{"name": "java-17", "base_image": "athanor-sandbox-java:17", "memory": "12g"}'
```

### Environment Variables

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

### Repository Preferences

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

### Sandbox Visibility

```bash
# List running sandbox containers (ops)
curl http://localhost:8200/api/v1/sandboxes \
  -H "X-Requested-With: XMLHttpRequest"
```

### WebSocket Streaming

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

## Webhook Setup

Legion reacts to GitHub events (issues opened/labeled/edited/closed, issue comments, pull requests) via a single webhook endpoint at `POST /api/v1/webhook`. Because Legion usually runs on a private network, you need a way to get GitHub's webhook POSTs into your instance. Two supported approaches:

| Approach | Use when | Signature verification |
|----------|----------|------------------------|
| **Cloudflare Tunnel** | Production / always-on demo / shared team instance | **Required** — real HMAC secret |
| **smee.io relay** | Local dev on a laptop / ephemeral testing | **Skipped** — DEV_MODE=true, no secret |

### Option A — Cloudflare Tunnel (production)

Exposes your Legion instance on a public hostname via a zero-trust tunnel. The tunnel should be **restricted to webhook paths only** — never expose the full app.

**1. Install cloudflared:**

```bash
# macOS
brew install cloudflared
# Linux (Debian/Ubuntu)
curl -L https://pkg.cloudflare.com/install.sh | sudo bash
sudo apt-get install -y cloudflared
```

**2. Authenticate and create a tunnel:**

```bash
cloudflared tunnel login                       # opens browser, picks your CF zone
cloudflared tunnel create legion-webhooks       # prints a tunnel UUID
```

**3. Route a DNS name at the tunnel:**

```bash
cloudflared tunnel route dns legion-webhooks legion.your-domain.com
```

**4. Write the tunnel config** at `~/.cloudflared/config.yml`:

```yaml
tunnel: <UUID from step 2>
credentials-file: /home/you/.cloudflared/<UUID>.json

ingress:
  # Allow only the GitHub webhook path
  - hostname: legion.your-domain.com
    path: ^/api/v1/webhook$
    service: http://localhost:8200
  # Allow the Telegram callback path (optional)
  - hostname: legion.your-domain.com
    path: ^/api/v1/telegram/callback$
    service: http://localhost:8200
  # Reject everything else
  - service: http_status:404
```

> **Security:** the `path:` whitelist is critical. Without it the tunnel would expose your entire Legion UI and API to the internet.

**5. Run the tunnel** (as a service or in a tmux pane):

```bash
cloudflared tunnel run legion-webhooks
# Or install as a system service:
sudo cloudflared service install
```

**6. Generate a webhook secret and set it in `.env`:**

```bash
openssl rand -hex 20
```

```
GITHUB_WEBHOOK_SECRET=<generated-value>
DEV_MODE=false
```

Rebuild the orchestrator so the new secret is picked up:

```bash
cd infra && docker compose build orchestrator && docker compose up -d orchestrator --force-recreate
```

**7. Configure the GitHub webhook** — repo → Settings → Webhooks → Add webhook:

- **Payload URL:** `https://legion.your-domain.com/api/v1/webhook`
- **Content type:** `application/json`
- **Secret:** the value from step 6
- **SSL verification:** enabled
- **Events:** select "Let me select individual events", check **Issues**, **Issue comments**, **Pull requests**

**8. Verify:** trigger an event (e.g. label an issue with `Legion`) and tail the orchestrator logs:

```bash
docker logs -f infra-orchestrator-1 | grep webhook_received
```

### Option B — smee.io relay (local development)

For testing real GitHub events against a local Legion instance on your laptop, with no public hostname needed. smee.io re-serializes the webhook body before forwarding, which invalidates GitHub's HMAC — so this flow uses `DEV_MODE=true` and no secret.

**1. Get a smee channel:** visit [https://smee.io](https://smee.io), click **Start a new channel**, and copy the channel URL (e.g. `https://smee.io/aBcDeF1234`).

**2. Start the relay** (Node 20+ required):

```bash
npx smee-client --url https://smee.io/aBcDeF1234 --target http://localhost:8200/api/v1/webhook
```

Leave this running in a terminal pane — it prints every forwarded event.

**3. Enable DEV_MODE** in `.env` and clear the webhook secret:

```
DEV_MODE=true
GITHUB_WEBHOOK_SECRET=
```

Rebuild the orchestrator so the new config is picked up:

```bash
cd infra && docker compose build orchestrator && docker compose up -d orchestrator --force-recreate
```

**4. Configure the GitHub webhook** — repo → Settings → Webhooks → Add webhook:

- **Payload URL:** your smee channel URL (e.g. `https://smee.io/aBcDeF1234`)
- **Content type:** `application/json`
- **Secret:** *(leave blank)*
- **Events:** Issues, Issue comments, Pull requests

**5. Verify:** trigger an event in the repo. You should see the event appear in the smee-client terminal AND in `docker logs -f infra-orchestrator-1 | grep webhook_received`.

> **Note:** smee caches recent events and replays them on reconnect, which can cause duplicate job triggers after restarting the relay. For demos or production, always use Cloudflare Tunnel with HMAC verification.

### Without webhooks

You can trigger jobs manually via the dashboard — open the Issues page, find your issue, and click **Send to Agent**. No webhook setup required.

## Project Structure

```
legion/
├── legion/                     # Python backend
│   ├── api/                    # FastAPI endpoints + WebSocket
│   │   ├── v1/                 # REST routes (jobs, graphs, config, ...)
│   │   └── ws/                 # WebSocket streaming
│   ├── orchestration/          # LangGraph integration
│   │   ├── state.py            # JobState TypedDict + reducers
│   │   ├── nodes/              # Agent, triage, validation, output nodes
│   │   ├── routing/            # Conditional edge functions
│   │   └── checkpoint.py       # AsyncPostgresSaver integration
│   ├── workflows/              # Built-in + custom workflows
│   │   └── issue_to_pr/        # Issue-to-PR pipeline (StateGraph)
│   ├── execution/              # Sandbox management
│   │   ├── sandbox_manager.py  # Container lifecycle (DinD)
│   │   ├── agent_runner.py     # docker exec + stdout parsing
│   │   ├── image_manager.py    # Sandbox image auto-build + reaper
│   │   └── proxy/              # Auth, Git, GitHub API, Legion proxies
│   ├── sandbox/                # Code that runs inside containers
│   │   ├── runner.py           # Agent SDK execution
│   │   ├── safety.py           # Blocked command enforcement
│   │   └── github/             # Git ops + GitHub client (via proxies)
│   ├── services/               # Business logic services
│   │   ├── issue_executor.py   # Issue→job→workflow orchestration
│   │   └── github_sync.py      # Two-way GitHub issue sync
│   ├── notifications/          # Multi-channel notifications
│   │   ├── telegram.py         # Telegram Bot API integration
│   │   ├── github.py           # GitHub issue comment notifications
│   │   └── dispatcher.py       # Routes to configured channels
│   ├── storage/                # PostgreSQL persistence
│   ├── agents/                 # Agent definitions + SDK wrapper
│   │   ├── sdk.py              # Claude Agent SDK wrapper (OAuth)
│   │   └── resolver.py         # Agent config resolution chain
│   ├── safety/                 # Shared safety patterns
│   └── audit/                  # Audit logging (JSONL + DB + structlog)
├── frontend/                   # React 19 SPA
│   ├── src/
│   │   ├── components/
│   │   │   ├── issues/         # Issue tracker (list, board, detail, questions)
│   │   │   ├── jobs/           # Agent cards, tool call stream, thinking blocks, paused banner
│   │   │   ├── pipeline-editor/# Pipeline visualization + editor
│   │   │   ├── layout/         # Sidebar, TopBar, AppLayout
│   │   │   └── ui/             # Design system (Card, Button, Input, ...)
│   │   ├── pages/              # Dashboard, Issues, Jobs, Agents, Pipelines, Settings
│   │   └── hooks/              # Data fetching (React Query + WebSocket)
│   ├── Dockerfile              # Multi-stage (Node build + nginx)
│   └── nginx.conf              # Reverse proxy to orchestrator
├── infra/
│   ├── docker-compose.yml      # 4-service stack
│   ├── Dockerfile.orchestrator
│   ├── Dockerfile.sandbox
│   └── scripts/
├── tests/
│   ├── unit/                   # 183+ unit tests
│   └── integration/            # 18 integration tests (real PostgreSQL)
├── docs/
│   └── superpowers/
│       ├── specs/              # Architecture design + ADRs
│       └── plans/              # Implementation plans (1-9)
├── lab                         # Dev container helper (bash)
├── pyproject.toml              # Python project config + CLI entry point
└── .env.example                # Configuration template
```

## Configuration

Legion uses environment variables for infrastructure config and API endpoints for runtime config.

### Environment Variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `PROVIDER_MODE` | `anthropic_api` | LLM provider: `anthropic_api`, `claude_max`, `ollama` |
| `ANTHROPIC_API_KEY` | — | API key (for `anthropic_api` mode) |
| `CLAUDE_MAX_OAUTH_TOKEN` | — | OAuth token (for `claude_max` mode) |
| `GITHUB_TOKEN` | — | GitHub personal access token |
| `GITHUB_WEBHOOK_SECRET` | — | HMAC secret for webhook verification |
| `DATABASE_URL` | `postgresql://athanor:athanor@postgres:5432/athanor` | PostgreSQL connection |
| `MAX_BUDGET_USD` | `10.0` | Default per-job budget |
| `DAILY_BUDGET_CAP_USD` | `100.0` | Daily spending cap |
| `MONTHLY_BUDGET_CAP_USD` | `500.0` | Monthly spending cap |
| `BUDGET_OVERRUN_MODE` | `soft` | `soft` (allow finish) or `hard` (stop immediately) |
| `PROD_PORT` | `8200` | Frontend port (nginx) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token for notifications |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID for notifications |
| `TELEGRAM_WEBHOOK_URL` | — | Public URL for Telegram callback (e.g. Cloudflare tunnel) |
| `TRIGGER_LABELS` | `Legion` | GitHub labels that trigger jobs |
| `ENVIRONMENT_SECRET_KEY` | — | Fernet key for encrypting env var secrets at rest |
| `PAUSED_JOB_TTL_HOURS` | `48` | Hours before a paused job's sandbox is expired |

See `.env.example` for the complete list.

### Runtime Configuration (API)

Budget controls, context injection, and sandbox profiles are configurable via the API without restarting:

- **Budget** — `GET/PUT /api/v1/config/budget`
- **Context** — `GET/PUT /api/v1/config/context` (global), `/config/context/repos/{repo}` (per-repo)
- **Sandbox Profiles** — `CRUD /api/v1/sandbox-profiles`

## Agent Execution & Auth Modes

Legion invokes Claude through a pluggable executor layer with two orthogonal config dimensions:

| Dimension | Values | Meaning |
|---|---|---|
| `execution_mode` | `sdk` (default), `cli` (Phase 2) | Agent SDK Python wrapper vs direct `claude -p` CLI subprocess |
| `auth_mode` | `oauth` (default), `api_key` | Claude Max OAuth token vs Anthropic API key |

All 4 combinations are valid in Phase 2+. **Phase 1 supports `sdk` only**; requesting `cli` at any level raises `ERR_INVALID_CONFIG` at resolve time.

### Five-level precedence

Later levels win:

1. **Global** — `LegionConfig.default_execution_mode`, `.default_auth_mode` (env vars `DEFAULT_EXECUTION_MODE`, `DEFAULT_AUTH_MODE`).
2. **Per-repo** — `repo_preferences.execution_mode`, `.auth_mode` columns.
3. **Per-job** — `JobCreateRequest.execution_mode_override`, `.auth_mode_override`.
4. **Per-agent-type** — `agent_definitions.execution_mode`, `.auth_mode` columns.
5. **Pipeline config (triage output)** — `pipeline_config.execution_modes[agent_type]`, `.auth_modes[agent_type]`.

NULL at any level falls through to the previous level.

### Credentials

- `auth_mode=oauth` requires `CLAUDE_CODE_OAUTH_TOKEN` (loaded from the host's `~/.claude/.credentials.json` by the sandbox manager). Missing token → `ERR_MISSING_OAUTH_TOKEN`.
- `auth_mode=api_key` requires `ANTHROPIC_API_KEY` in `LegionConfig`. Missing key → `ERR_MISSING_API_KEY`.

### Safety Policy (three rings)

1. **Container isolation** — ephemeral Docker sandbox, non-root, tmpfs workspace.
2. **Declarative permissions** — rendered into `/workspace/.claude/settings.json` per run (`permissions.allow` / `permissions.deny`). Enforced by Claude Code before tool dispatch.
3. **Programmable hook** — `evaluate_policy()` runs dangerous-Bash-pattern checks as a `PreToolUse` callable. Fail-closed.

Both execution modes consume the same `SafetyPolicy` data structure via different delivery mechanisms.

## Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run linting
ruff check legion/ tests/

# Frontend dev server
cd frontend && npm install && npm run dev
```

### Testing

```bash
# Unit tests (no external dependencies)
pytest tests/unit/ -v

# Integration tests (requires PostgreSQL)
TEST_DATABASE_URL=postgresql://athanor:athanor@localhost:5432/athanor_test \
  pytest tests/integration/ -v

# Full suite
pytest tests/ -v
```

## Future Roadmap

- **Spring Boot SaaS Layer** — Multi-tenant fleet management (one Legion instance per tenant)
- **Kubernetes Deployment** — Helm chart, DinD replaced by K8s pod launching
- **Custom Workflows** — User-defined LangGraph graphs via API
- **Pipeline Template Marketplace** — Pre-built workflows for common tasks
- **Claude Code Skills Integration** — Configurable skills per agent (superpowers, TDD, debugging)

---

<div align="center">
  <sub>Built with LangGraph, Claude Agent SDK, FastAPI, React, and PostgreSQL</sub>
</div>
