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
        VAL["Validator Agent<br/><small>Tests + Lint + Types</small>"]
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
    DEV --> VAL
    VAL -->|fail| DEV
    VAL -->|pass| REV
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
        FE["Frontend<br/>nginx :8201"]
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

- **Sandbox isolation** — Each job runs in a Docker container with `--cap-drop=ALL`, `--security-opt=no-new-privileges`, read-only filesystem
- **No customer code retention** — Sandbox clones repo internally; code is deleted when container is destroyed
- **Credential injection via proxies** — API keys and tokens never enter the sandbox; three proxy services inject credentials transparently
- **Dynamic network switching** — Agents get internet access only when needed (research), otherwise network-restricted
- **Safety patterns** — 16 blocked command patterns with NFKC unicode normalization prevent dangerous bash operations

## The Issue-to-PR Pipeline

```mermaid
stateDiagram-v2
    [*] --> Triage
    
    Triage --> Developer: proceed
    Triage --> AwaitInput: needs_info
    Triage --> Split: too_large

    Developer --> Validator
    Validator --> Reviewer: pass
    Validator --> Developer: fail (retry)
    Reviewer --> Output: approved
    Reviewer --> Developer: rejected (feedback)
    Output --> [*]
```

The **triage agent** uses an LLM to analyze each issue and configure the pipeline:
- **Confidence scoring** — Low confidence triggers a request for more information
- **Issue splitting** — Oversized tasks are autonomously decomposed
- **Pipeline customization** — Model tiers, validator modes, feedback loops, and sandbox profiles configured per job

The **developer agent** owns the full lifecycle: code changes, git operations, and PR creation. It uses Claude Code skills (e.g., superpowers) for structured workflows including sub-agent dispatch and worktree management.

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.12+
- Node.js 20+ (for frontend development)

### 1. Clone and configure

```bash
git clone https://github.com/your-org/legion.git
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

### 3. Start the stack

```bash
legion start
```

This builds all images, starts PostgreSQL + DinD + Orchestrator + Frontend, waits for health checks, and builds the sandbox image inside DinD.

### 4. Open the dashboard

```
http://localhost:8201
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

### Jobs (High-Level)

```bash
# Create a job
curl -X POST http://localhost:8201/api/v1/jobs \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"repo": "owner/repo", "task": "Fix the auth bug in login.py"}'

# List jobs
curl http://localhost:8201/api/v1/jobs

# Cancel a job
curl -X POST http://localhost:8201/api/v1/jobs/{job_id}/cancel \
  -H "X-Requested-With: XMLHttpRequest"
```

### Graph Execution (Low-Level)

```bash
# List available workflows
curl http://localhost:8201/api/v1/graphs/workflows

# Execute a workflow
curl -X POST http://localhost:8201/api/v1/graphs/execute \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"workflow": "issue-to-pr", "input_state": {"repo": "owner/repo", "task": "..."}}'
```

### Configuration

```bash
# Budget controls
curl http://localhost:8201/api/v1/config/budget
curl -X PUT http://localhost:8201/api/v1/config/budget \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"max_budget_per_job_usd": 25.0}'

# Context injection
curl http://localhost:8201/api/v1/config/context
curl -X PUT http://localhost:8201/api/v1/config/context \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"system_context": "Use TypeScript strict mode."}'

# Sandbox profiles
curl http://localhost:8201/api/v1/sandbox-profiles
curl -X POST http://localhost:8201/api/v1/sandbox-profiles \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"name": "java-17", "base_image": "legion-sandbox-java:17", "memory": "12g"}'
```

### WebSocket Streaming

```javascript
const ws = new WebSocket('ws://localhost:8201/ws/jobs/{job_id}/events');
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // { type: "agent_started", agent: "developer", ... }
  // { type: "progress", message: "Editing src/auth/login.py", ... }
  // { type: "agent_completed", cost_usd: 0.42, ... }
};
```

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
│   │   └── routing/            # Conditional edge functions
│   ├── workflows/              # Built-in + custom workflows
│   │   └── issue_to_pr/        # Issue-to-PR pipeline (StateGraph)
│   ├── execution/              # Sandbox management
│   │   ├── sandbox_manager.py  # Container lifecycle (DinD)
│   │   ├── agent_runner.py     # docker exec + stdout parsing
│   │   └── proxy/              # Auth, Git, GitHub API proxies
│   ├── sandbox/                # Code that runs inside containers
│   │   ├── runner.py           # Agent SDK execution
│   │   ├── safety.py           # Blocked command enforcement
│   │   └── github/             # Git ops + GitHub client (via proxies)
│   ├── storage/                # PostgreSQL persistence
│   ├── agents/                 # Agent registry metadata
│   ├── safety/                 # Shared safety patterns
│   └── audit/                  # Audit logging (JSONL + structlog)
├── frontend/                   # React 19 SPA
│   ├── src/
│   │   ├── components/
│   │   │   ├── pipeline-tree/  # Pipeline visualization
│   │   │   ├── agents/         # Agent popup + execution cards
│   │   │   ├── state/          # State flow panel
│   │   │   └── ui/             # Design system (IconBox, Card, ...)
│   │   ├── pages/              # Dashboard, Jobs, Demo, Pipelines, Settings
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
| `DATABASE_URL` | `postgresql://legion:legion@postgres:5432/legion` | PostgreSQL connection |
| `MAX_BUDGET_USD` | `10.0` | Default per-job budget |
| `DAILY_BUDGET_CAP_USD` | `100.0` | Daily spending cap |
| `MONTHLY_BUDGET_CAP_USD` | `500.0` | Monthly spending cap |
| `BUDGET_OVERRUN_MODE` | `soft` | `soft` (allow finish) or `hard` (stop immediately) |
| `PROD_PORT` | `8200` | Frontend port |
| `TRIGGER_LABELS` | `Legion` | GitHub labels that trigger jobs |

See `.env.example` for the complete list.

### Runtime Configuration (API)

Budget controls, context injection, and sandbox profiles are configurable via the API without restarting:

- **Budget** — `GET/PUT /api/v1/config/budget`
- **Context** — `GET/PUT /api/v1/config/context` (global), `/config/context/repos/{repo}` (per-repo)
- **Sandbox Profiles** — `CRUD /api/v1/sandbox-profiles`

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
TEST_DATABASE_URL=postgresql://legion:legion@localhost:5432/legion_test \
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
