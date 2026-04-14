# Legion Architecture

Detailed technical documentation of the Legion agent orchestration engine.

---

## System Overview

Legion is a general-purpose agent orchestration engine built on LangGraph and Claude Agent SDK. GitHub issues or API requests create issues which are triaged by an LLM agent, questions are dispatched to the user via multiple channels (Dashboard, Telegram, GitHub), agents execute inside Docker sandboxes, and on success a PR is opened automatically. Customer code never persists on the host — it lives only inside ephemeral sandbox containers.

```mermaid
graph TB
    GH["GitHub Issue"] -->|webhook POST| API["FastAPI<br/>/api/v1/webhook"]
    TG["Telegram"] -->|callback POST| API
    API -->|create issue + triage| EX["Issue Executor"]
    EX -->|"run workflow"| LG["LangGraph Engine<br/>(StateGraph + Checkpoints)"]
    LG -->|checkpoints| PG[("PostgreSQL")]

    LG --> SM["Sandbox Manager"]
    SM -->|"docker run/exec<br/>via DinD"| SB["Sandbox Container<br/>(Agent SDK + git/GitHub)"]
    SB -.->|"Claude API"| AP["Auth Proxy"]
    SB -.->|"git clone/push"| GP["Git Remote Proxy"]
    SB -.->|"GitHub REST API"| GHP["GitHub API Proxy"]
    SB -.->|"create issues, request input"| LP["Legion API Proxy"]

    SB -->|"create PR, post comments<br/>(via proxy)"| GH

    LG -->|events| WS["WebSocket<br/>(live streaming)"]
    EX --> NT["Notifications<br/>(Telegram / GitHub)"]

    style GH fill:#24292e,color:#fff
    style SB fill:#1a1a2e,color:#fff
```

---

## Deployment Topology

One Docker Compose stack per tenant. No shared workspace volumes — sandbox containers clone repos internally and code is deleted on teardown.

```mermaid
graph TB
    subgraph Stack["Docker Compose Stack"]
        FE["Frontend<br/>nginx + React SPA<br/>:8200"]

        subgraph Orchestrator["Orchestrator Container"]
            API["FastAPI :8000"]
            LG["LangGraph Engine"]
            EX["Issue Executor"]
            SM["Sandbox Manager"]
            IM["Image Manager"]
        end

        subgraph Proxies["Proxy Services (sandbox-restricted network)"]
            AP["Auth Proxy<br/>(Claude API)"]
            GP["Git Remote Proxy<br/>(git clone/push)"]
            GHP["GitHub API Proxy<br/>(PRs, comments)"]
            LP["Legion API Proxy<br/>(create issues, request input)"]
        end

        PG["PostgreSQL 16<br/>Jobs + Issues + Checkpoints"]

        subgraph DinD["Docker-in-Docker"]
            DD["Docker Daemon"]
            S1["Sandbox (Job A)<br/>clones repo internally"]
            S2["Sandbox (Job B)<br/>clones repo internally"]
        end
    end

    FE <-->|reverse proxy| API
    API --> EX
    EX --> LG
    LG --> SM
    SM -->|"docker run/exec via TLS"| DD
    DD --> S1
    DD --> S2
    S1 -.-> AP
    S1 -.-> GP
    S1 -.-> LP
    S2 -.-> AP
    S2 -.-> GHP
    LG <--> PG

    GH["GitHub"] -->|webhooks| API
    TG["Telegram"] -->|callback| API
    CF["Cloudflare Tunnel"] -->|webhook-only| API

    style Stack fill:#1a1a2e,stroke:#333,color:#fff
    style Orchestrator fill:#0f3460,stroke:#533483,color:#fff
    style Proxies fill:#1b4332,stroke:#2d6a4f,color:#fff
    style DinD fill:#2c2c54,stroke:#474787,color:#fff
    style GH fill:#24292e,color:#fff
```

### Docker Network Segmentation

| Network | Services | Purpose |
|---------|----------|---------|
| `frontend` | Frontend, Orchestrator | Isolates frontend from database/DinD |
| `backend` | Orchestrator, PostgreSQL, DinD | Backend services only |
| `sandbox-restricted` | Proxy services, Sandbox containers | No internet, proxy-only access |
| `sandbox-internet` | Sandbox containers (research only) | Filtered egress for web search |

### Services

| Service | Image | Purpose | Resources |
|---------|-------|---------|-----------|
| Frontend | `legion-frontend` | nginx serving React SPA, reverse proxy | — |
| Orchestrator | `legion-orchestrator` | FastAPI + LangGraph, job management, proxy services. Never touches customer code. | configurable via `.env` |
| PostgreSQL | `postgres:16` | Application data + LangGraph checkpoints | configurable via `.env` |
| DinD | `docker:dind` | Runs sandbox containers (privileged) | configurable via `.env` |

No shared workspace volumes. All resource limits, ports, image tags, and credentials are configurable via `.env` file.

---

## Issues & Human-in-the-Loop

Issues are first-class entities in Legion — independent of jobs. An issue is the **what** (the problem/request), a job is the **how** (the execution). Issues support optional two-way GitHub sync and can be decomposed into child issues by the triage agent.

### Issue Lifecycle

```mermaid
stateDiagram-v2
    [*] --> open: created (API / webhook)
    open --> triaging: auto-triage or manual
    triaging --> awaiting_input: needs_info (blocking questions)
    triaging --> in_progress: proceed (jobs running)
    triaging --> open: split (child issues created)
    awaiting_input --> triaging: all questions answered
    in_progress --> closed: all jobs completed
    open --> cancelled: user cancels
    closed --> [*]
    cancelled --> [*]
```

### Multi-Channel Notifications

When the pipeline needs human input (`interrupt()`), questions are dispatched to three channels simultaneously:

| Channel | Mechanism | Answer Matching |
|---------|-----------|----------------|
| **Dashboard** | Questions panel on issue detail page | `POST /api/v1/issues/{id}/inputs/{input_id}/answer` |
| **Telegram** | Per-question message with inline buttons | Reply-to-message matching via `telegram_message_id` |
| **GitHub** | Comment on linked GitHub issue | `issue_comment.created` webhook |

Answers from any channel trigger cross-channel sync (e.g., answer via Telegram → edit GitHub comment). When all blocking questions are answered, the pipeline resumes automatically.

### GitHub Two-Way Sync

- **Outbound (Legion → GitHub):** Issue create/update pushes to GitHub API
- **Inbound (GitHub → Legion):** Webhook handles `issues.opened`, `issues.edited`, `issues.closed`, `issues.reopened`, `issues.labeled`, `issues.unlabeled`, `issue_comment.created`
- **Conflict handling:** Last-write-wins with timestamps

---

## Request Lifecycle

End-to-end flow from issue creation to PR:

```mermaid
sequenceDiagram
    participant C as Client / GitHub
    participant API as FastAPI
    participant EX as Issue Executor
    participant LG as LangGraph (astream)
    participant SM as Sandbox Manager
    participant SB as Sandbox
    participant SDK as Agent SDK
    participant NT as Notifications

    C->>API: POST /api/v1/issues (or webhook)
    API->>API: Create issue, GitHub sync
    API->>EX: execute(issue_id) — auto-triage

    EX->>EX: Create job, link to issue
    EX->>SM: create sandbox container
    SM->>SB: docker run (DinD)
    Note over SB: Clone repo via Git Proxy

    EX->>LG: graph.astream(initial_state)

    loop For each node (triage → developer → review)
        LG->>SM: run_agent(container_id, config)
        SM->>SB: docker exec → runner.py
        SB->>SDK: query(prompt, tools, model)
        SDK->>SDK: Tool calls (Read/Write/Bash)
        SB-->>SM: JSON lines (stdout)
        SM-->>LG: AgentOutput → state update
        LG-->>EX: StreamWriter events (custom)
        EX-->>C: WebSocket events (live)
    end

    alt Triage needs info
        LG->>LG: interrupt(questions)
        EX->>EX: Create issue_inputs records
        EX->>NT: Dispatch to Telegram + GitHub + Dashboard
        NT-->>C: Questions via all channels
        C->>API: Answer (any channel)
        API->>EX: resume — Command(resume=answers)
        EX->>LG: Continue from checkpoint
    end

    alt Review approved
        Note over SB: Developer already created PR
        SB-->>LG: pr_url in state
    else Review rejected (retry ≤ 3)
        LG->>LG: Inject feedback, retry developer
    else Max retries reached
        LG->>LG: interrupt(options) — ask user
    end

    EX->>SM: destroy sandbox (code deleted)
    EX->>API: Update job (pr_url, cost, status)
    API-->>C: Job result
```

---

## Issue-to-PR Pipeline

The built-in workflow. The triage node uses an LLM to assess complexity, configure the pipeline, and determine if more information is needed or the issue should be split.

```mermaid
stateDiagram-v2
    [*] --> Init
    Init --> Triage: create sandbox, clone repo

    state Triage {
        Assess: Assess complexity + confidence
        Assess --> Proceed: confidence >= threshold
        Assess --> NeedsInfo: confidence < threshold
        Assess --> Split: issue too large
        NeedsInfo --> Await: interrupt() — ask for info
        Split --> CreateChildren: create child issues (via Legion proxy)
    }

    Triage --> Developer: action = proceed

    state Developer {
        Code: Write code + tests
        Code --> Git: git branch, commit, push
        Git --> PR: Create PR (Closes #issue)
    }

    Developer --> BudgetCheck: check cost vs. budget
    BudgetCheck --> Review: within budget
    BudgetCheck --> AskUser: budget exceeded

    state Review {
        Assess2: Code review
        Assess2 --> Approved: decision = approved
        Assess2 --> ChangesReq: decision = changes_requested
    }

    Review --> End1: approved
    ChangesReq --> Retry: inject feedback
    Retry --> Developer: retry (max 3)
    Retry --> AskUser: max retries reached
    AskUser --> Developer: user says continue
    AskUser --> End2: user says merge or abandon

    End1 --> [*]
    End2 --> [*]
```

### Graph Nodes

| Node | Agent? | Tools | Purpose |
|------|--------|-------|---------|
| `init` | No | — | Create sandbox container, clone repo |
| `triage` | Yes | Read, Glob, Grep | Analyze issue, configure pipeline. Can `interrupt()` for questions. |
| `developer` | Yes | Read, Write, Edit, Bash, Glob, Grep | Write code, create branch, commit, push, open PR |
| `review` | Yes | Read, Bash, Glob, Grep | Code review with structured JSON output. Can request changes (triggers retry). |
| `retry` | No | — | Inject review feedback into state, increment counter |
| `max_retries` | No | — | `interrupt()` — ask user: continue, merge as-is, or abandon |

### Triage Decision

The LLM triage node outputs a pipeline configuration.

| Field | Type | Purpose |
|-------|------|---------|
| `action` | `proceed \| needs_info \| split` | What to do next |
| `confidence` | `float (0.0-1.0)` | Confidence in implementation approach |
| `pipeline_template` | `str` | Template name or "custom" |
| `pipeline_config.sandbox_profile` | `str` | Sandbox profile name |
| `pipeline_config.agent_models` | `dict[str, str]` | Model per agent |
| `pipeline_config.max_feedback_loops` | `int` | Review → retry cycles (default: 3) |
| `pipeline_config.reviewer_enabled` | `bool` | Whether review is included (default: true) |
| `pipeline_config.validator_modes` | `list[str]` | Which validators to run (test, lint, typecheck) |
| `pipeline_config.budget_usd` | `float` | Suggested budget for this job |
| `questions` | `list[dict]` | When `needs_info`: `{question, importance, suggested_answer}` |
| `sub_tasks` | `list[dict]` | When `split`: `{task, description}` objects |
| `reasoning` | `str` | Why this configuration was chosen |

### LangGraph Native Patterns

| Pattern | Usage |
|---------|-------|
| `interrupt(value)` | Pause graph for human input (triage questions, max retries) |
| `Command(resume=value)` | Resume paused graph with user's answer |
| `get_stream_writer()` | Nodes emit custom events (progress, tool calls, PR created) |
| `graph.astream(stream_mode=["updates", "custom"])` | Executor captures live events |
| `config["configurable"]` | Dependency injection: AgentRunner, SandboxManager, proxy URLs |

---

## Sandbox Container Architecture

Each job gets one Docker sandbox inside DinD. The sandbox clones the repo, agents execute inside it, and all git/GitHub operations happen within. When destroyed, all customer code is deleted.

```mermaid
graph LR
    subgraph Orchestrator["Orchestrator"]
        AR["agent_runner.py"]
        SM["sandbox_manager.py"]
        IM["image_manager.py"]
    end

    subgraph ProxyNet["sandbox-restricted network"]
        AP["Auth Proxy"]
        GP["Git Remote Proxy"]
        GHP["GitHub API Proxy"]
        LP["Legion API Proxy<br/>(CreateIssue, RequestInput)"]
    end

    subgraph Sandbox["Sandbox Container (legion-sandbox)"]
        RN["runner.py"]
        SDK["Agent SDK<br/>query()"]
        SF["safety.py"]
        GIT["github/<br/>git_ops.py + client.py"]
    end

    SM -->|"docker run -d<br/>--cap-drop=ALL<br/>--security-opt=no-new-privileges"| Sandbox
    AR -->|"docker exec<br/>JSON lines stdout"| RN
    RN --> SDK
    SDK --> SF
    GIT -->|"git clone/push"| GP
    GIT -->|"create PR, comments"| GHP
    SDK -.->|"Claude API"| AP

    style Sandbox fill:#1a1a2e,color:#fff
    style ProxyNet fill:#1b4332,stroke:#2d6a4f,color:#fff
```

### Container Security

| Control | Implementation |
|---------|---------------|
| Capability drop | `--cap-drop=ALL` (configurable: `cap_add` escape hatch) |
| Privilege escalation | `--security-opt=no-new-privileges` |
| Resource limits | `--memory`, `--cpus`, `--pids-limit` (all configurable) |
| Filesystem | Writable root (read-only disabled — Claude CLI requires writable fs), writable `/tmp` (tmpfs, noexec, nosuid), writable `/workspace` |
| Network | `sandbox-restricted` (default) or `sandbox-internet` (research agents) |
| Credentials | Injected via proxies (see Proxy Services) — `GITHUB_TOKEN` and `ANTHROPIC_API_KEY` never enter the sandbox env. **Scoped exception:** `CLAUDE_CODE_OAUTH_TOKEN` is passed to the sandbox env in Claude Max mode because the Claude CLI reads it directly from env (product constraint, not a deferred fix). |
| Command blocking | 34 regex patterns, NFKC unicode normalization, Glob path restriction, symlink defense via `os.path.realpath()` |
| Code retention | None — repo cloned inside container, deleted on teardown |
| OAuth credentials | Mounted read-only from orchestrator (`~/.claude`) |

**Sandbox env surface:** The container env contains only non-sensitive config: `LEGION_GIT_PROXY_URL` (just the proxy URL; not a secret), plus `CLAUDE_CODE_OAUTH_TOKEN` when Claude Max OAuth mode is active (see the Credentials row above). No `GITHUB_TOKEN`. No `ANTHROPIC_API_KEY`.

### Sandbox Image Management

The `SandboxImageManager` handles image lifecycle inside DinD:

- **Auto-build on startup** — computes SHA256 hash of `Dockerfile.sandbox` + sandbox source files, compares with image label `legion.build-hash`, rebuilds if stale
- **Profile-specific images** — `legion-sandbox:{profile_name}` extends the base image with additional packages/commands
- **Container reaper** — background task destroys containers older than 24 hours

---

## Proxy Services

Four proxy services run on the `sandbox-restricted` network.

**Credential injection via proxies** — GitHub auth flows exclusively through the git credential proxy (port 9101): the sandbox's git credential helper curls the proxy, which injects the orchestrator's `GITHUB_TOKEN` in the response. `GITHUB_TOKEN` is never present in the sandbox env. The auth proxy (port 9100) for Anthropic API keys is started but not currently wired into the sandbox runner — currently unused. **Scoped exception:** `CLAUDE_CODE_OAUTH_TOKEN` is passed into the sandbox env when Claude Max OAuth mode is active, because the Claude CLI reads it directly from env. This is a product-level constraint (the CLI doesn't support per-request injection for OAuth), not a deferred fix.

| Proxy | Port | Purpose | Credential Injected |
|-------|------|---------|-------------------|
| Auth Proxy | 9100 | Claude API requests | `x-api-key` header |
| Git Remote Proxy | 9101 | `git clone/push` | Git credential helper response |
| GitHub API Proxy | — | GitHub REST API (PRs, comments) | `Authorization: Bearer` header |
| Legion API Proxy | 9102 | Legion internal API (per-job) | Scoped to current issue/job |

### Legion API Proxy

Gives sandbox agents controlled access to Legion's own API, scoped to the current job's issue:

| Tool | Endpoint | Used By |
|------|----------|---------|
| `CreateIssue` | `POST /create-issue` | Triage (split), Developer (scope expansion) |
| `RequestInput` | `POST /request-input` | Any agent — triggers `interrupt()` |
| `UpdateStatus` | `POST /update-status` | Any agent — activity feed update |

---

## Context Injection

Agents receive injectable context at two levels, configurable via API:

| Level | Purpose | Example |
|-------|---------|---------|
| System prompt | Coding standards, architecture guidelines, team conventions | "Use TypeScript strict mode. Follow hexagonal architecture." |
| Assistant prompt | Issue-specific instructions, prior context, exploration hints | "The auth module was refactored last week — see PR #142." |

Context scopes (merged in order, later overrides earlier):

| Scope | Config via | Applies to |
|-------|-----------|------------|
| Global | `PUT /api/v1/config/context` | All jobs |
| Per-repo | `PUT /api/v1/config/context/repos/{repo}` | All jobs for that repo |
| Per-job | `POST /api/v1/jobs` body | Single job |

---

## API Structure

Three-level API: issues (domain), jobs (execution), graph engine (low-level). Plus configuration, streaming, and webhooks.

```mermaid
graph TB
    subgraph "Issues (Domain)"
        IC["POST /api/v1/issues"]
        IL["GET /api/v1/issues"]
        ID["GET /api/v1/issues/{id}"]
        IU["PUT /api/v1/issues/{id}"]
        IT["POST /api/v1/issues/{id}/triage"]
        IS["POST /api/v1/issues/{id}/sync"]
        IA["GET /api/v1/issues/{id}/activity"]
        II["GET /api/v1/issues/{id}/inputs"]
        IIA["POST /api/v1/issues/{id}/inputs/{inp}/answer"]
    end

    subgraph "Jobs (Execution)"
        JC["POST /api/v1/jobs"]
        JL["GET /api/v1/jobs"]
        JD["GET /api/v1/jobs/{id}"]
        JX["POST /api/v1/jobs/{id}/cancel"]
    end

    subgraph "Graph Engine (Low-Level)"
        EX["POST /api/v1/graphs/execute"]
        GS["GET /api/v1/graphs/{id}/state"]
        RS["POST /api/v1/graphs/{id}/resume"]
    end

    subgraph "Configuration"
        BG["GET/PUT /api/v1/config/budget"]
        CX["GET/PUT /api/v1/config/context"]
        CR["GET/PUT /api/v1/config/context/repos/{repo}"]
        PR["GET/PUT /api/v1/config/provider"]
        IG["GET/PUT /api/v1/config/integrations"]
        SP["CRUD /api/v1/sandbox-profiles"]
        PT["CRUD /api/v1/pipeline-templates"]
        AG["CRUD /api/v1/agents"]
    end

    subgraph "Webhooks & Callbacks"
        WH["POST /api/v1/webhook (GitHub)"]
        TC["POST /api/v1/telegram/callback"]
    end

    subgraph "Streaming"
        WSK["WS /ws/jobs/{id}/events"]
    end
```

---

## Storage

PostgreSQL serves dual duty: application data and LangGraph checkpoints.

```mermaid
erDiagram
    issues {
        string id PK
        string title
        string body
        string status "open|triaging|in_progress|awaiting_input|closed|cancelled"
        string priority "critical|high|medium|low"
        json labels
        string repo "owner/name"
        string parent_issue_id FK "self-referencing"
        int github_number
        string github_url
        bool github_sync_enabled
        string created_by "user|webhook|triage_agent"
        timestamp created_at
        timestamp updated_at
    }

    issue_inputs {
        string id PK
        string issue_id FK
        string job_id FK
        string asking_node "triage|developer|review"
        string question
        string importance "blocking|auto_answerable"
        string auto_answer
        string answer
        string answered_by "user|telegram|github"
        int telegram_message_id
        string status "pending|auto_answered|answered"
        timestamp created_at
        timestamp answered_at
    }

    jobs {
        string job_id PK
        string status "pending|running|completed|failed|cancelled|awaiting_input|pr_merged|pr_closed"
        string repo
        string task
        int issue_number
        string issue_id FK
        string pipeline_template
        json pipeline_config
        float total_cost_usd
        float budget_overrun_usd
        string pr_url
        string error_message
        timestamp created_at
        timestamp started_at
        timestamp finished_at
    }

    traces {
        string trace_id PK
        string job_id FK
        string agent_type
        string model
        float cost_usd
        int duration_ms
        json tools_called
        string result_summary
    }

    pipeline_templates {
        string id PK
        string name
        string description
        json graph_definition
        json agent_models
        string sandbox_profile
        timestamp created_at
        timestamp updated_at
    }

    agent_definitions {
        string type PK
        string description
        string model
        json tools
        json skills
        text system_prompt
        bool is_builtin
    }

    context_config {
        string id PK
        string scope "global|repo"
        string repo "nullable"
        text system_context
        text assistant_context
        timestamp updated_at
    }

    budget_config {
        string id PK
        float max_budget_per_job_usd
        float daily_cap_usd
        float monthly_cap_usd
        int rate_limit_per_author_per_hour
        string overrun_mode "soft|hard"
        timestamp updated_at
    }

    sandbox_profiles {
        string name PK
        string base_image
        json additional_packages
        json setup_commands
        string memory
        string cpus
        int pids_limit
        json cap_drop
        json cap_add
        json security_opt
        int agent_timeout_seconds
        int container_timeout_seconds
    }

    audit_log {
        bigint id PK
        string action
        string actor
        json details
        string issue_id "nullable"
        timestamp created_at
    }

    issues ||--o{ jobs : "spawns"
    issues ||--o{ issue_inputs : "has questions"
    issues ||--o{ issues : "parent/child"
    jobs ||--o{ traces : "has"
    jobs ||--o{ issue_inputs : "produced by"
    sandbox_profiles ||--o{ pipeline_templates : "referenced by"
```

LangGraph checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`) are managed by `langgraph-checkpoint-postgres` and provide pause/resume, time-travel debugging, and state inspection.

---

## Budget Controls

Three layers of cost protection, all configurable via API (`GET/PUT /api/v1/config/budget`):

| Control | Config Variable | Default | API Endpoint |
|---------|----------------|---------|--------------|
| Per-job budget | `MAX_BUDGET_USD` | $10.00 | `PUT /api/v1/config/budget` |
| Daily cap | `DAILY_BUDGET_CAP_USD` | $100.00 | `PUT /api/v1/config/budget` |
| Monthly cap | `MONTHLY_BUDGET_CAP_USD` | $500.00 | `PUT /api/v1/config/budget` |
| Rate limit | `RATE_LIMIT_PER_AUTHOR_PER_HOUR` | 5 | `PUT /api/v1/config/budget` |
| Overrun mode | `BUDGET_OVERRUN_MODE` | `soft` | `PUT /api/v1/config/budget` |

---

## Module Dependency Graph

```mermaid
graph TB
    subgraph API["api/"]
        APP["app.py<br/>(FastAPI factory)"]
        V1["v1/ routers<br/>(issues, jobs, agents, config,<br/>webhooks, telegram, ...)"]
        WSK["ws/ streaming"]
    end

    subgraph Services["services/"]
        IEX["issue_executor.py<br/>(job creation + workflow)"]
        GHS["github_sync.py<br/>(two-way issue sync)"]
    end

    subgraph Orchestration["orchestration/"]
        ST["state.py<br/>(JobState + TriageDecision)"]
        ND["nodes/<br/>(agent, triage)"]
        RT["routing/<br/>(conditions)"]
        CK["checkpoint.py"]
    end

    subgraph Workflows["workflows/"]
        REG["registry.py"]
        ITP["issue_to_pr/<br/>(graph + nodes + routing)"]
    end

    subgraph Execution["execution/"]
        SM["sandbox_manager.py"]
        AR["agent_runner.py"]
        DC["docker_client.py"]
        IMG["image_manager.py"]
    end

    subgraph ProxySvc["execution/proxy/"]
        PM["manager.py"]
        APR["auth_proxy.py"]
        GPR["git_proxy.py"]
        GHPR["github_proxy.py"]
        LPR["legion_proxy.py"]
    end

    subgraph Sandbox["sandbox/ (in container)"]
        RN["runner.py"]
        SF["safety.py"]
        GIT["github/<br/>git_ops + client"]
    end

    subgraph Storage["storage/"]
        DB["database.py"]
        RP["repositories/<br/>(issues, jobs, traces,<br/>issue_inputs, agents, ...)"]
        MG["migrations/"]
    end

    subgraph Agents["agents/"]
        SDK["sdk.py<br/>(Agent SDK wrapper)"]
        RES["resolver.py"]
    end

    NT["notifications/<br/>(telegram, github, dispatcher)"]
    AU["audit/<br/>(logger, db_logger, helpers)"]

    APP --> V1
    APP --> WSK
    V1 --> Services
    Services --> REG
    Services --> ND
    REG --> ITP
    ITP --> ST
    ITP --> RT
    ND --> AR
    AR -.->|docker exec| RN
    RN --> GIT
    GIT -.->|via proxy| GPR
    GIT -.->|via proxy| GHPR
    Services --> DB
    SM --> DC
    DC --> ProxySvc
    Services --> NT
    Services --> AU
    ND --> SDK

    style Sandbox fill:#1a1a2e,color:#fff
    style ProxySvc fill:#1b4332,stroke:#2d6a4f,color:#fff
```

---

## External Integrations

### Cloudflare Tunnel (production webhook ingress)

The public endpoint at `legion.alchymielabs.com` is restricted to webhook paths only:

| Path | Service | Purpose |
|------|---------|---------|
| `/api/v1/webhook` | GitHub webhook | Issue events, PR events, comments |
| `/api/v1/telegram/callback` | Telegram Bot API | Reply-to-message answers |
| Everything else | **403 Forbidden** | Dashboard only accessible on LAN |

Signature verification is always on in production: `GITHUB_WEBHOOK_SECRET` is set and the handler enforces HMAC-SHA256 (`verify_webhook_signature` in `legion/api/auth.py`).

### smee.io Relay (local development webhook ingress)

For developer laptops without a public URL, Legion supports the [smee.io](https://smee.io) relay pattern. The GitHub webhook is pointed at a smee channel URL, and `smee-client` forwards received events to `http://localhost:8200/api/v1/webhook`. Because smee re-serializes the JSON payload before forwarding, GitHub's HMAC signature is invalidated — so this flow requires `DEV_MODE=true` and an empty `GITHUB_WEBHOOK_SECRET`.

The handler in `legion/api/v1/webhooks.py` supports this flow in two ways:

1. `verify_webhook_signature(..., dev_mode=True)` skips HMAC verification when no secret is configured.
2. After JSON parsing, the handler detects and unwraps smee's `{"payload": "<json-string>"}` envelope so downstream code sees the real GitHub payload unchanged.

See README "Webhook Setup → Option B — smee.io relay" for the end-to-end setup. **Never enable this configuration in production** — any unsigned `POST` to `/api/v1/webhook` will be accepted and could trigger jobs.

### Telegram Bot

- Webhook registered on app startup via `setWebhook` API
- Secret token verification via `X-Telegram-Bot-Api-Secret-Token` header
- Per-question messages with inline "Answer in Dashboard" buttons
- Reply-to-message matching for free-text answers

---

## Future: Spring Boot SaaS Layer

Spring Boot manages a fleet of Legion instances — one per tenant. Legion remains single-tenant internally.

```mermaid
graph TB
    subgraph SaaS["Spring Boot SaaS Platform"]
        GW["API Gateway"]
        TM["Tenant Manager"]
        BL["Billing"]
    end

    subgraph Fleet["Legion Fleet (K8s)"]
        L1["Legion<br/>Tenant A"]
        L2["Legion<br/>Tenant B"]
        L3["Legion<br/>Tenant C"]
    end

    GW --> L1
    GW --> L2
    GW --> L3
    TM -->|create/destroy| Fleet
    L1 -->|metrics| BL
    L2 -->|metrics| BL
    L3 -->|metrics| BL

    style SaaS fill:#264653,stroke:#2a9d8f,color:#fff
```
