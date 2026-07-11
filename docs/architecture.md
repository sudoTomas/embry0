# embry0 Architecture

Detailed technical documentation of the embry0 agent orchestration engine.

---

## System Overview

embry0 is a general-purpose agent orchestration engine built on LangGraph and Claude Agent SDK. GitHub issues or API requests create issues which are triaged by an LLM agent, questions are dispatched to the user via multiple channels (Dashboard, Telegram, GitHub), agents execute inside Docker sandboxes, and on success a PR is opened automatically. Customer code never persists on the host — it lives only inside ephemeral sandbox containers.

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
    SB -.->|"create issues, request input"| LP["embry0 API Proxy"]

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
            LP["embry0 API Proxy<br/>(create issues, request input)"]
        end

        PG["PostgreSQL 16<br/>Jobs + Issues + Checkpoints"]

        subgraph DinD["Docker-in-Docker"]
            DD["Docker Daemon"]
            S1["Sandbox (Job A)<br/>clones repo internally"]
            S2["Sandbox (Job B)<br/>clones repo internally"]
            SQ["QA Sandbox (Job C)<br/>+ Chromium + Docker CLI"]
            QAN["qa-net-jobC<br/>(per-QA-job network)"]
            APP["Target app stack<br/>(compose -p qa_jobC)"]
            MP["minio-proxy"]
            PP["presign-proxy"]
        end

        MIN["MinIO<br/>qa-artifacts bucket"]
    end

    FE <-->|reverse proxy| API
    API --> EX
    EX --> LG
    LG --> SM
    SM -->|"docker run/exec via TLS"| DD
    DD --> S1
    DD --> S2
    DD --> SQ
    S1 -.-> AP
    S1 -.-> GP
    S1 -.-> LP
    S2 -.-> AP
    S2 -.-> GHP
    SQ -.->|launches| APP
    SQ -.->|attached to| QAN
    APP -.->|attached to| QAN
    SQ -.->|presigned PUT/GET| MP
    SQ -.->|refresh URLs| PP
    MP -.-> MIN
    PP --> API
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
| `qa-net-{job_id}` | One QA sandbox + the target app's compose stack | Per-QA-job DinD network created at `init_qa` and torn down by `cleanup_qa_resources`. Lets the target app's services reach each other and the agent's Playwright reach the frontend by short DNS names (`gateway`, `redis`, `frontend`). |

> **Networks are asserted at orchestrator startup, not auto-created.** The
> orchestrator inspects each network and refuses to start if `sandbox-restricted`
> lacks `enable_ip_masquerade=false`. The compose `init-sandbox-networks`
> one-shot service runs `infra/scripts/setup-sandbox-networks.sh` inside DinD
> as a prerequisite; the orchestrator depends on its successful completion.

### Services

| Service | Image | Purpose | Resources |
|---------|-------|---------|-----------|
| Frontend | `embry0-frontend` | nginx serving React SPA, reverse proxy | — |
| Orchestrator | `embry0-orchestrator` | FastAPI + LangGraph, job management, proxy services. Never touches customer code. | configurable via `.env` |
| PostgreSQL | `postgres:16` | Application data + LangGraph checkpoints | configurable via `.env` |
| DinD | `docker:dind` | Runs sandbox containers (privileged) | configurable via `.env` |
| MinIO | `minio/minio` | S3-compatible artifact store. `qa-artifacts` bucket holds QA `result.json`, screenshots, traces, and `logs/full.log` per `<job_id>/<attempt_n>/...`. | configurable via `.env` |

No shared workspace volumes. All resource limits, ports, image tags, and credentials are configurable via `.env` file.

Beyond these five core services, the compose file defines 13 services in total: supporting long-running services (`postgres-backup` for daily dumps, an in-stack `registry` sidecar that DinD pulls sandbox images from, and the optional `cloudflared` webhook tunnel), two one-shot init services (`init-sandbox-networks`, `init-push-images`), and three image builders behind the `images` profile.

---

## Issues & Human-in-the-Loop

Issues are first-class entities in embry0 — independent of jobs. An issue is the **what** (the problem/request), a job is the **how** (the execution). Issues support optional two-way GitHub sync and can be decomposed into child issues by the triage agent.

### Issue Lifecycle

```mermaid
stateDiagram-v2
    [*] --> open: created (API / webhook)
    open --> triaging: auto-triage or manual
    triaging --> awaiting_input: needs_info (blocking questions)
    triaging --> in_progress: proceed (jobs running)
    triaging --> open: split (child issues created)
    triaging --> paused: paused explicitly
    awaiting_input --> triaging: all questions answered
    awaiting_input --> paused: TTL exceeded
    paused --> in_progress: resumed
    paused --> open: reaper expired
    in_progress --> closed: all jobs completed
    open --> cancelled: user cancels
    closed --> [*]
    cancelled --> [*]
```

When a blocking question sits unanswered past the configured `paused_job_ttl_hours`, the `ContainerReaper` transitions the issue to `paused` and destroys its sandbox. Resumption rebuilds the sandbox.

### Multi-Channel Notifications

When the pipeline needs human input (`interrupt()`), questions are dispatched to three channels simultaneously:

| Channel | Mechanism | Answer Matching |
|---------|-----------|----------------|
| **Dashboard** | Questions panel on issue detail page | `POST /api/v1/issues/{id}/inputs/{input_id}/answer` |
| **Telegram** | Per-question message with inline buttons | Reply-to-message matching via `telegram_message_id` |
| **GitHub** | Comment on linked GitHub issue | `issue_comment.created` webhook |

Answers from any channel trigger cross-channel sync (e.g., answer via Telegram → edit GitHub comment). When all blocking questions are answered, the pipeline resumes automatically.

### GitHub Two-Way Sync

- **Outbound (embry0 → GitHub):** Issue create/update pushes to GitHub API
- **Inbound (GitHub → embry0):** Webhook handles `issues.opened`, `issues.edited`, `issues.closed`, `issues.reopened`, `issues.labeled`, `issues.unlabeled`, `issue_comment.created`
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
    }

    Triage --> Developer: action = proceed
    Triage --> [*]: action = split

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

    Review --> QAGate: approved
    ChangesReq --> Retry: inject feedback
    Retry --> Developer: retry (max 3)
    Retry --> AskUser: max retries reached
    AskUser --> Developer: user says continue
    AskUser --> End2: user says merge or abandon

    state QAGate {
        Decide: needs_qa? (set by triage)
        Decide --> QAFlow: yes
        Decide --> End1: no
    }

    state QAFlow {
        InitQA: init_qa<br/>start sandbox + qa-net + load qa.yaml
        QA: qa<br/>agent runs 5 phases via Playwright MCP
        QAReport: qa_report<br/>parse result.json + cleanup
        Bookkeep: qa_failure_bookkeeping<br/>bump failure_rounds on failed
        InitQA --> QA --> QAReport --> Bookkeep
        Bookkeep --> Pass: passed → END
        Bookkeep --> Fail: failed → triage (retry_developer / rerun_qa / ask_user)
        Bookkeep --> Exhaust: failure_rounds at cap → ERR_QA_FAILURES_UNRESOLVED
    }

    Fail --> Triage: re-invoke with failure summary

    End1 --> [*]
    End2 --> [*]
```

> **Note on `split`:** The `split` action creates child issues as a side effect inside `run_triage_node` (calling the embry0 API proxy) and then terminates the current workflow; there is no dedicated child-creation node in the graph.

> **Note on QA gate:** Triage emits `set_qa_decision` (initial pass) and `qa_failure_action` (after a failed QA run) inline in its JSON output. The QA subpath reuses the standalone QA workflow's nodes verbatim — see the QA Pipeline section below for the inner workings.

### Graph Nodes

| Node | Agent? | Tools | Purpose |
|------|--------|-------|---------|
| `init` | No | — | Create sandbox container, clone repo |
| `triage` | Yes | Read, Glob, Grep | Analyze issue, configure pipeline. Can `interrupt()` for questions. Also emits `set_qa_decision` (whether QA gate runs) and, on re-invocation after QA failure, `qa_failure_action` (retry_developer / rerun_qa / ask_user). |
| `developer` | Yes | Read, Write, Edit, Bash, Glob, Grep | Write code, create branch, commit, push, open PR |
| `review` | Yes | Read, Bash, Glob, Grep | Code review with structured JSON output. Can request changes (triggers retry). |
| `retry` | No | — | Inject review feedback into state, increment counter |
| `max_retries` | No | — | `interrupt()` — ask user: continue, merge as-is, or abandon |
| `init_qa` | No | — | (QA gate, when `needs_qa=true`) Create QA sandbox + per-job network, parse `.embry0/qa.yaml`, mint MinIO presigned PUT URLs, write `/workspace/.qa/job.json` |
| `qa` | Yes | Read, Glob, Grep, Bash, Edit, Playwright MCP (22 tools) | Run the 5 QA phases (boot, seed, e2e, exploratory, report) and write `result.json` |
| `qa_report` | No | — | Read + Pydantic-validate `result.json`, set `qa.final_status`, upload artifacts, destroy sandbox, cleanup network |
| `qa_failure_bookkeeping` | No | — | Increment `qa.failure_rounds` when `final_status=failed` |
| `qa_exhausted` | No | — | Terminal node when `failure_rounds` hits the cap; sets `error_code=ERR_QA_FAILURES_UNRESOLVED` |

### Triage Decision

The LLM triage node outputs a pipeline configuration.

| Field | Type | Purpose |
|-------|------|---------|
| `action` | `proceed \| needs_info \| split` | What to do next |
| `confidence` | `float (0.0-1.0)` | Confidence in implementation approach |
| `pipeline_template` | `str` | Template name or "custom" |
| `pipeline_config.sandbox_profile` | `str` | Sandbox profile name |
| `pipeline_config.agent_models` | `dict[str, str]` | Model per agent |
| `pipeline_config.agent_tools` | `dict[str, list[str]]` | Per-agent tool allowlist overrides |
| `pipeline_config.agent_skills` | `dict[str, list[str]]` | Per-agent Claude Code skills to load |
| `pipeline_config.max_feedback_loops` | `int` | Review → retry cycles (default: 3) |
| `pipeline_config.reviewer_enabled` | `bool` | Whether review is included (default: true) |
| `pipeline_config.validator_modes` | `list[str]` | Which validators to run (test, lint, typecheck) |
| `pipeline_config.budget_usd` | `float` | Suggested budget for this job |
| `questions` | `list[dict]` | When `needs_info`: `{question, importance, suggested_answer}` |
| `sub_tasks` | `list[dict]` | When `split`: `{task, description}` objects |
| `reasoning` | `str` | Why this configuration was chosen |

**Per-repo preference override (Plan K).** If a row exists in `repo_preferences` for the current `owner/repo`, its `sandbox_profile` value **overrides** whatever the triage LLM chose in `pipeline_config.sandbox_profile`. The override is applied after triage parsing and before graph execution; the LLM is still free to reason about profiles, but the repo preference wins at execution time. See the ER diagram for the `repo_preferences` table.

### LangGraph Native Patterns

| Pattern | Usage |
|---------|-------|
| `interrupt(value)` | Pause graph for human input (triage questions, max retries) |
| `Command(resume=value)` | Resume paused graph with user's answer |
| `get_stream_writer()` | Nodes emit custom events (progress, tool calls, PR created) |
| `graph.astream(stream_mode=["updates", "custom"])` | Executor captures live events |
| `config["configurable"]` | Dependency injection: AgentRunner, SandboxManager, proxy URLs |

---

## QA Pipeline

The QA agent boots a target full-stack application inside DinD, drives a headless Chromium via Playwright MCP, and validates each acceptance criterion with screenshots, browser console, network activity, and per-service container logs as evidence. Two modes:

- **Standalone** (`POST /api/v1/jobs` with `pipeline=qa`) — run QA against any branch on demand. Used for ad-hoc validation and CI smoke.
- **PR-gated** (issue→PR with `needs_qa=true`) — triage decides whether the resulting PR should be QA-validated; the issue→PR graph routes through `init_qa → qa → qa_report` after `review` and re-invokes triage on failure with the failure summary so it can pick `retry_developer` / `rerun_qa` / `ask_user`.

Both modes share the same nodes (`init_qa`, `qa`, `qa_report`) and the same `qa-net-{job_id}` per-job network.

```mermaid
graph LR
    subgraph DinD
        QS["QA Sandbox<br/>embry0-sandbox-qa<br/>(JDK 21, Docker CLI, Chromium,<br/>Playwright MCP)"]
        QN["qa-net-jobC"]
        APP["Target app stack<br/>compose -p qa_jobC<br/>(gateway, frontend, db, ...)"]
        MP["minio-proxy"]
        PP["presign-proxy"]
    end

    subgraph Orch["Orchestrator"]
        IQ["init_qa node"]
        QR["qa_report node"]
        AR["agent_runner.py"]
    end

    MIN["MinIO<br/>qa-artifacts"]

    IQ -->|"docker network create"| QN
    IQ -->|"sandbox_mgr.create + attach"| QS
    IQ -->|"presign PUT URLs<br/>(internal endpoint)"| MIN
    IQ -->|"write /workspace/.qa/job.json"| QS
    AR -->|"docker exec runner"| QS
    QS -->|"docker compose up<br/>-p qa_jobC"| APP
    QS -.->|"Playwright headless<br/>http://frontend:3000"| APP
    QS -->|"PUT screenshots/result.json<br/>(presigned URL via DNS)"| MP
    QS -->|"refresh URLs"| PP
    PP -->|"POST /internal/qa/presign"| Orch
    MP -.-> MIN
    QR -->|"cat result.json + validate"| QS
    QR -->|"sandbox.destroy → network rm"| QN
```

### Per-job lifecycle

| Step | Owner | Action |
|------|-------|--------|
| 1. network | `init_qa` | `docker network create qa-net-{job_id}` (DinD) |
| 2. sandbox | `init_qa` | Create QA sandbox container, attach to qa-net + sandbox-restricted, write OAuth token |
| 3. presign | `init_qa` | Generate two MinIO presigned PUT URLs (`result.json`, `logs/full.log`) signed against the **sandbox-facing** endpoint (`minio-proxy:9100`) so the URL host resolves over qa-net |
| 4. job.json | `init_qa` | Write `/workspace/.qa/job.json` with the URLs, `presign_refresh_url`, `sandbox_token`, parsed `qa.yaml`, and acceptance criteria |
| 5. clone | `init_qa` | `git clone --branch <branch>` from inside sandbox via git-proxy |
| 6. agent | `qa` | `python -m embry0.sandbox.runner` invokes the QA agent — runs the 5 phases, uploads artifacts via curl |
| 7. parse | `qa_report` | `cat /workspace/.qa/result.json` → `QAResult.model_validate_json` → set `qa.final_status` |
| 8. teardown | `qa_report` | sandbox destroy → `cleanup_qa_resources` (containers/volumes/network by dual-label filter). Order matters — sandbox must die before `network rm` or it stays attached. |

### Two MinIO clients

Phase 1.5 architecture decision: presigned URLs are signed against whichever endpoint the *requester* will use to hit MinIO. The orchestrator and the sandbox live on different networks (host-side `backend` vs DinD-side `sandbox-restricted`), so two `QAMinioClient` instances are wired into `app.state`:

| Client | Endpoint | Used by |
|--------|----------|---------|
| `qa_minio` | `minio:9000` (host backend network) | Orchestrator-side reads (`/jobs/{id}/qa/attempts/{n}/result` GET) |
| `qa_minio_sandbox` | `minio-proxy:9100` (DinD sandbox-restricted) | `init_qa_node` mints PUT URLs the sandbox will follow |

`minio-proxy` and `presign-proxy` are containers running inside DinD on `sandbox-restricted` (paired with the `git-proxy` family). `minio-proxy` is a thin reverse proxy onto the host MinIO; `presign-proxy` forwards `POST /api/v1/internal/qa/presign` calls to the orchestrator so the agent can mint additional URLs at runtime (screenshots, traces).

### `result.json` contract

The agent writes a Pydantic-validated JSON file at end of run. Top-level shape:

```python
class QAResult:
    schema_version: Literal[1]
    job_id: str
    attempt_n: int
    phase_reached: Literal["boot", "seed", "e2e", "exploratory", "report"]
    overall: Literal["passed", "failed", "inconclusive"]
    boot: QABootResult                                   # command, duration_ms, ready_checks
    seed: QASeedResult | None
    e2e: QAE2EResult | None
    acceptance_results: list[QAAcceptanceResult]         # criterion, status, evidence, console_errors, network_failures, log_excerpts
    anomalies: list[QAAnomaly]                           # console_error | network_error | unexpected_state | crash
```

Validation failures land an `inconclusive` attempt with `result_json_invalid` in the exit reason; the runtime prompt sent to the agent includes the schema verbatim to keep field shapes correct.

### Triage QA decision (PR-gated mode)

Triage emits two distinct decisions inline in its JSON output (parsed via `TriageDecisionModel`):

| Field | When | Shape |
|-------|------|-------|
| `set_qa_decision` | Initial pass | `{needs_qa, reason, acceptance_criteria}` |
| `qa_failure_action` | Re-invocation after `qa.final_status=failed` | `{kind: retry_developer\|rerun_qa\|ask_user, ...action-specific}` |

`route_after_qa_report` consumes `qa.final_status` + `qa.failure_rounds`:
- `passed` → END
- `exhausted` → END
- `failed` and `failure_rounds < max_qa_failure_rounds` (default 2) → `triage` (with failure context)
- `failed` and at cap → `qa_exhausted` → END with `ERR_QA_FAILURES_UNRESOLVED`

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
        LP["embry0 API Proxy<br/>(CreateIssue, RequestInput)"]
    end

    subgraph Sandbox["Sandbox Container (embry0-sandbox)"]
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
| Command blocking | 43 regex patterns, NFKC unicode normalization, Glob path restriction, symlink defense via `os.path.realpath()` |
| Code retention | None — repo cloned inside container, deleted on teardown |
| OAuth credentials | The orchestrator receives the Claude OAuth token via the `CLAUDE_CODE_OAUTH_TOKEN` env var and passes it to the sandbox (see the Credentials row above) |
| Ring-3 enforcement | Asserted at orchestrator startup (refuses to boot if the installed `claude_agent_sdk` does not expose `hooks`). Per-run hook attachment is unconditional — no fall-back. The in-process executor fallback was removed in 2026-04 hardening. |

**Sandbox env surface:** The container env contains only non-sensitive config: `EMBRY0_GIT_PROXY_URL` (just the proxy URL; not a secret), plus `CLAUDE_CODE_OAUTH_TOKEN` when Claude Max OAuth mode is active (see the Credentials row above). No `GITHUB_TOKEN`. No `ANTHROPIC_API_KEY`.

### Sandbox Image Management

The `SandboxImageManager` handles image lifecycle inside DinD:

- **Auto-build on startup** — computes SHA256 hash of `Dockerfile.sandbox` + sandbox source files, compares with image label `embry0.build-hash`, rebuilds if stale
- **Profile-specific images** — `embry0-sandbox:{profile_name}` extends the base image with additional packages/commands
- **QA sandbox variant** — `embry0-sandbox-qa:latest` is a separate image (built from `infra/Dockerfile.sandbox.qa`) with the superset needed by the QA agent: JDK 21 (Adoptium Temurin), Docker CLI + compose plugin (talks to DinD daemon over TCP+TLS), Chromium + headless-shell pre-installed at `/opt/playwright`, the `playwright-mcp` global binary, and the qa-compose / qa-ready-check helpers in `/usr/local/bin`. Selected by sandbox profile `qa-jvm` (or `qa-node` / `qa-python`). The Chromium revision is pinned to whatever `@playwright/mcp`'s bundled `playwright-core` expects — installing via the standalone `playwright` package's CLI gives a different revision and breaks MCP launches.
- **Container reaper** — background task destroys containers older than 24 hours, **unless the matching job is still active in the DB**. Active-status lookup failures fail closed (the reaper does nothing rather than risk killing live work). The reaper also sweeps DinD-side QA orphans — containers with `com.docker.compose.project=qa_*` OR `embry0.qa_job_id=*` whose owning job is no longer active.

---

## Proxy Services

Three credential-injecting proxies (`git-proxy`, `github-proxy`, `auth-proxy`) run as **containers inside DinD** on the `sandbox-restricted` network. They are launched by `ProxyManager` at orchestrator startup using the `embry0-proxy:latest` image. Sandboxes resolve them by Docker DNS (`http://git-proxy:9101`, `http://github-proxy:9103`, `http://auth-proxy:9100`) — not via `host.docker.internal`.

Proxies that need outbound internet (`github-proxy`, `auth-proxy`) are also attached to `sandbox-internet`. The `git-proxy` only returns a static credential helper response and needs no egress.

A fourth proxy — the **per-job embry0 API proxy** that exposes CreateIssue / RequestInput / UpdateStatus to agents — is **currently deferred**. `start_embry0_proxy_for_job()` raises `NotImplementedError`, and `WorkflowRegistry.register()` rejects any `pipeline_template` that declares `CreateIssue`, `RequestInput`, or `UpdateStatus` tools at registration time. Until this is implemented, those tools are unavailable to all workflows. Tracked as a follow-up.

**Credential injection via proxies** — GitHub auth flows exclusively through the git credential proxy (port 9101): the sandbox's git credential helper curls the proxy, which injects the orchestrator's `GITHUB_TOKEN` in the response. `GITHUB_TOKEN` is never present in the sandbox env. The auth proxy (port 9100) for Anthropic API keys is launched but not currently consumed by the sandbox runner — currently unused. **Scoped exception:** `CLAUDE_CODE_OAUTH_TOKEN` is passed into the sandbox env when Claude Max OAuth mode is active, because the Claude CLI reads it directly from env. This is a product-level constraint (the CLI doesn't support per-request injection for OAuth), not a deferred fix.

| Proxy | Port | Purpose | Credential Injected |
|-------|------|---------|-------------------|
| Auth Proxy | 9100 | Claude API requests | `x-api-key` header |
| Git Remote Proxy | 9101 | `git clone/push` | Git credential helper response |
| GitHub API Proxy | — | GitHub REST API (PRs, comments) | `Authorization: Bearer` header |
| embry0 API Proxy | 9102 (deferred) | embry0 internal API (per-job) | Scoped to current issue/job |
| MinIO Proxy | 9100 (DinD-side) | QA agent → MinIO PUT/GET via DNS-resolvable name | None — only forwards to host MinIO |
| Presign Proxy | 9104 (DinD-side) | QA agent → orchestrator `POST /api/v1/internal/qa/presign` to mint additional URLs at runtime | Per-sandbox bearer (same scheme as the credential proxies) |

> **Per-sandbox bearer authentication.** Each credential proxy validates an
> `Authorization: Bearer <token>` header on every request. Bearers are minted
> by `ProxyManager.enroll_sandbox()` at sandbox-create time (one per sandbox),
> stored in each proxy as `sha256(token)`, and verified with `hmac.compare_digest`.
> The orchestrator's `GITHUB_TOKEN` and `ANTHROPIC_API_KEY` are never exposed
> over the network; only sandbox-scoped bearers are. Bearers are revoked when
> the sandbox is destroyed (`unenroll_sandbox`).

### embry0 API Proxy

> **Status:** Deferred. `start_embry0_proxy_for_job()` raises `NotImplementedError` and `WorkflowRegistry.register()` rejects any `pipeline_template` that declares `CreateIssue`/`RequestInput`/`UpdateStatus` tools. Until this is implemented, those tools are unavailable to all workflows. The table below describes the intended interface; tracked as a follow-up.

Gives sandbox agents controlled access to embry0's own API, scoped to the current job's issue:

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
        IX["DELETE /api/v1/issues/{id}"]
        IT["POST /api/v1/issues/{id}/triage"]
        IS["POST /api/v1/issues/{id}/sync"]
        IA["GET /api/v1/issues/{id}/activity"]
        II["GET /api/v1/issues/{id}/inputs"]
        IIA["POST /api/v1/issues/{id}/inputs/{inp}/answer"]
    end

    subgraph "Jobs (Execution)"
        JC["POST /api/v1/jobs<br/>(supports agent_models override)"]
        JL["GET /api/v1/jobs"]
        JD["GET /api/v1/jobs/{id}<br/>(includes cost_breakdown)"]
        JX["POST /api/v1/jobs/{id}/cancel"]
    end


    subgraph "Ops & Debug"
        SBL["GET /api/v1/sandboxes/active"]
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
        RP["GET/PUT/DELETE /api/v1/repos/{owner}/{repo}/preferences"]
        ENV["CRUD /api/v1/environments (global + per-repo env vars)"]
    end

    subgraph "Webhooks & Callbacks"
        WH["POST /api/v1/webhook (GitHub)"]
        TC["POST /api/v1/telegram/callback"]
    end

    subgraph "Streaming"
        WSK["WS /ws/jobs/{id}/events<br/>(optional ?event_types=a,b server-side filter)"]
    end
```

**Plan G/H endpoints & fields:**

- `GET /api/v1/sandboxes/active` — lists live sandbox containers (ops visibility).
- `POST /api/v1/jobs` — accepts an `agent_models` field for per-agent model override at job creation.
- `GET /api/v1/jobs/{id}` — response includes a `cost_breakdown` field (per-agent aggregated cost/duration/tools).
- `GET /ws/jobs/{id}/events?event_types=a,b` — optional server-side event filter; only the named event types are streamed.
- `GET/PUT/DELETE /api/v1/repos/{owner}/{repo}/preferences` (Plan K) — per-repo preferences override triage (see Triage Decision note below).
- `CRUD /api/v1/environments` (Plan J) — global + per-repo environment variables with Fernet-encrypted secrets (see Environment Variable Storage below).

---

## Storage

PostgreSQL serves dual duty: application data and LangGraph checkpoints.

```mermaid
erDiagram
    issues {
        string id PK
        string title
        string body
        string status "open|triaging|in_progress|awaiting_input|paused|closed|cancelled"
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
        string status "pending|running|completed|failed|cancelled|awaiting_input|paused|expired|pr_merged|pr_closed"
        string error_code "ERR_AGENT_TIMEOUT|ERR_NO_RESULT|ERR_BUDGET_OVERRUN|ERR_MAX_RETRIES|ERR_TRIAGE_MALFORMED|ERR_ORPHANED|ERR_WORKFLOW_UNKNOWN|ERR_SANDBOX_INIT|ERR_DOCKER_TIMEOUT|ERR_MAX_AGENT_QUESTIONS|ERR_MAX_TRIAGE_QUESTIONS|ERR_UNKNOWN"
        string trace_id "trc-<12hex> — grep key for the full job timeline"
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
        timestamp updated_at
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
        string trace_id "nullable — same trace_id stamped on jobs row"
        timestamp created_at
    }

    repo_preferences {
        string repo PK "owner/name"
        string sandbox_profile "override of triage decision"
        timestamp created_at
        timestamp updated_at
    }

    global_environment {
        string key PK
        string value "plaintext OR Fernet-encrypted (see type)"
        string type "plain|secret"
        timestamp updated_at
    }

    repo_environment {
        string repo PK "owner/name — composite with key"
        string key PK
        string value "plaintext OR Fernet-encrypted (see type)"
        string type "plain|secret"
        timestamp updated_at
    }

    issues ||--o{ jobs : "spawns"
    issues ||--o{ issue_inputs : "has questions"
    issues ||--o{ issues : "parent/child"
    jobs ||--o{ traces : "has"
    jobs ||--o{ issue_inputs : "produced by"
    sandbox_profiles ||--o{ pipeline_templates : "referenced by"
    repo_preferences ||--o{ jobs : "may override triage for"
```

LangGraph checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`) are managed by `langgraph-checkpoint-postgres` and provide pause/resume, time-travel debugging, and state inspection.

### Environment Variable Storage (Plan J)

Per-repo and global environment variables are stored in `global_environment` / `repo_environment`. Variables of type `secret` are Fernet-encrypted at rest using a key derived from `ENVIRONMENT_SECRET_KEY` via PBKDF2-HMAC-SHA256. Responses mask secret values (`****`); a separate `/reveal` endpoint returns plaintext and emits an `environment.secret_revealed` audit event.

Reserved keys (`EMBRY0_GIT_PROXY_URL`, `GITHUB_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`) are blocked at the API layer and again at sandbox injection time to prevent users from hijacking orchestrator-injected infrastructure variables. If `ENVIRONMENT_SECRET_KEY` is unset the backend falls back to an insecure default and logs a startup warning; rotating the key makes prior secrets undecryptable and surfaces as `secret_decryption_failed` log events on the next job that needs them.

### ID Prefix Conventions

All primary keys and correlation IDs follow a `<type>-<12-char-hex>` format:

| Prefix | Column | Table | Notes |
|--------|--------|-------|-------|
| `job-` | `job_id` | `jobs` | Primary key; generated by `JobsRepository` |
| `issue-` | `id` | `issues` | Primary key; generated by `IssuesRepository` |
| `trc-` | `trace_id` | `traces` | Primary key; generated by `TracesRepository` |
| `trc-` | `trace_id` | `jobs`, `audit_log` | Cross-span correlation key; generated by `IssueExecutor` |

Note: `traces.trace_id` and `jobs.trace_id` / `audit_log.trace_id` share the `trc-` prefix but are distinct concepts. A `traces` row captures one agent's telemetry; the correlation `trace_id` groups all agents across a full issue execution. They are never joined on this column.

### Migrations Reference

The migration runner (`embry0/storage/migrations/runner.py`) applies these idempotently, in order, on orchestrator startup:

| # | Description | Tables/Columns |
|---|---|---|
| 1 | Initial schema | `jobs`, `traces`, `audit_log`, `job_logs` |
| 2 | Agent definitions, integration config, provider config | `agent_definitions`, `integration_config`, `provider_config` |
| 3 | Pipeline templates description column | `pipeline_templates.description` |
| 4 | Issues table + issue_id FK on jobs | `issues`, `audit_log.issue_id` |
| 5 | Human-in-the-loop questions | `issue_inputs` |
| 6 | Remove validator/output agents, rename reviewer → review | `agent_definitions` seed |
| 7 | Add updated_at to jobs | `jobs.updated_at` + trigger |
| 8 | Add error_code to jobs | `jobs.error_code` |
| 9 | Add trace_id threading | `jobs.trace_id`, `audit_log.trace_id` |
| 10 | Environment variables with encryption | `global_environment`, `repo_environment` |
| 11 | Per-repo preferences | `repo_preferences` |
| 12 | Pluggable agent execution modes | `repo_preferences.execution_mode`, `agent_definitions.execution_mode` |
| 13 | Drop untouched seed rows for provider/integration config | `provider_config`, `integration_config` |
| 14 | Bump default developer agent model to claude-opus-4-7 | `agent_definitions` seed |
| 15 | FK ON DELETE policies — CASCADE / SET NULL | All FK constraints |
| 16 | Performance indexes — GIN + composite | `issues`, `job_logs`, `traces` |
| 17 | Unify trace_id prefix from `trace-` to `trc-` | `traces.trace_id` |
| 18 | Sandbox profile QA fields | `sandbox_profiles.{base_image, additional_packages, additional_commands, env_vars}` for Phase 0 QA foundation |
| 19 | Env var scope (app vs qa) | `global_environment.scope`, `repo_environment.scope` (`app`/`qa`); QA scope only injected when QA pipeline runs |
| 20 | Agent definitions MCP servers | `agent_definitions.mcp_servers` (JSONB) — lets the QA agent declare its Playwright MCP server in the seed |

---

## Job Lifecycle & Executor

The `IssueExecutor` (`embry0/services/issue_executor.py`) owns the lifecycle of every background coroutine spawned to run a workflow.

**Task lifecycle.** `IssueExecutor._track_task(coro, *, kind, job_id, issue_id)` is the single entry point for creating background coroutines. It registers the task in both `_background_tasks` (set) and `_tasks_by_job` (dict) and attaches a done-callback that logs non-`CancelledError` failures and publishes a `job_failed` WS event. `IssueExecutor.cancel_job(job_id)` is the centralised cancellation flow: cancel the live task (with a 5s grace), destroy the sandbox, update job+issue status, purge LangGraph checkpoints for the thread, and emit a `job.cancelled` audit event.

**Trace ID threading (Plan H).** Every job is assigned a `trace_id` (format `trc-<12hex>`) at creation; the id is bound to structlog contextvars for the duration of `_run_workflow` and persisted onto every `audit_log` row written in that span. Use it as a grep key to reconstruct an issue's full timeline across logs and audit events.

**Agent-initiated questions (Plan L — `ask_user`).** Agents can pause the pipeline mid-execution by calling `embry0.sandbox.ask_user(question, category, options)`. The sandbox runner emits `agent_ask_user` events; `developer_node` picks them up and the graph routes to `ask_user_interrupt`, which raises `interrupt(...)` with the pending questions. `_handle_needs_info` persists questions to `issue_inputs` and transitions status to `awaiting_input` (reusing the triage-question infrastructure). On resume via `Command(resume=answers)`, the developer re-runs with a Q&A block prepended to its prompt. Capped at 5 rounds per job; past that the job fails with `ERR_MAX_AGENT_QUESTIONS`.

### Failure Classification

Failures populate the `jobs.error_code` column (migration 8) with one of the canonical values in `embry0.safety.error_codes.ErrorCode`:

| Code | Meaning |
|------|---------|
| `ERR_AGENT_TIMEOUT` | Agent SDK call exceeded its configured timeout. |
| `ERR_NO_RESULT` | Sandbox produced no final `ResultMessage`. |
| `ERR_BUDGET_OVERRUN` | Hard budget cap hit. |
| `ERR_MAX_RETRIES` | Reviewer rejected past `max_feedback_loops`. |
| `ERR_TRIAGE_MALFORMED` | Triage LLM output failed schema validation. |
| `ERR_ORPHANED` | Orchestrator restarted with the job in-flight. |
| `ERR_WORKFLOW_UNKNOWN` | Referenced workflow is not registered. |
| `ERR_SANDBOX_INIT` | Sandbox container creation failed. |
| `ERR_DOCKER_TIMEOUT` | An underlying Docker command timed out. |
| `ERR_MAX_AGENT_QUESTIONS` | Agent exceeded the 5-round `ask_user` cap (see Plan L). |
| `ERR_MAX_TRIAGE_QUESTIONS` | Triage exceeded the 5-round needs_info cap (Plan C / B6). |
| `ERR_UNKNOWN` | Uncategorised — dashboards should alert on growth. |

---

## WebSocket Streaming

Live event fan-out to the dashboard is served by `WS /ws/jobs/{id}/events`. The handler lives in `embry0/api/ws/streaming.py`.

WebSocket fan-out is managed by `embry0.api.events.EventBus` — a concurrency-safe class that holds per-`job_id` `asyncio.Queue` subscribers under an `asyncio.Lock` and exposes `subscribe`, `unsubscribe`, and `publish`. Event producers (graph nodes, the executor, the sandbox manager) call `EventBus.publish(job_id, event)` and every currently-subscribed queue for that job receives a copy.

**Replay cursor (`since_seq`).** Every broadcast event carries an `event_seq` field — the monotonic `BIGSERIAL` `id` from the `job_logs` table stamped at persist time. When a WS client reconnects after a dropped connection, it passes `?since_seq=<last_seen>`; the handler replays only rows with `id > since_seq`, tracks the highest id as a watermark, and then drops live events with `event_seq <= watermark` to prevent duplicates during the subscribe→replay race.

**Authentication.** WS clients authenticate via the `Sec-WebSocket-Protocol` subprotocol header: `embry0.bearer.<api_key>`. The server validates with `hmac.compare_digest` and echoes the matched subprotocol back on accept (RFC 6455). Token-in-URL is no longer supported. Per-job event queues are bounded at 1000 events; on overflow, the publisher drops the event with a `ws_slow_consumer` warning.

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
        LPR["embry0_proxy.py"]
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

The public endpoint (e.g. `embry0.example.com`) is restricted to webhook paths only:

| Path | Service | Purpose |
|------|---------|---------|
| `/api/v1/webhook` | GitHub webhook | Issue events, PR events, comments |
| `/api/v1/telegram/callback` | Telegram Bot API | Reply-to-message answers |
| Everything else | **403 Forbidden** | Dashboard only accessible on LAN |

Signature verification is always on in production: `GITHUB_WEBHOOK_SECRET` is set and the handler enforces HMAC-SHA256 (`verify_webhook_signature` in `embry0/api/auth.py`).

### smee.io Relay (local development webhook ingress)

For developer laptops without a public URL, embry0 supports the [smee.io](https://smee.io) relay pattern. The GitHub webhook is pointed at a smee channel URL, and `smee-client` forwards received events to `http://localhost:8200/api/v1/webhook`. Because smee re-serializes the JSON payload before forwarding, GitHub's HMAC signature is invalidated — so this flow requires `WEBHOOK_DEV_MODE=true` and an empty `GITHUB_WEBHOOK_SECRET`. (`AUTH_DEV_MODE` is the unrelated separate flag for bypassing API key auth; do NOT enable it just for the webhook flow.)

The handler in `embry0/api/v1/webhooks.py` supports this flow in two ways:

1. `verify_webhook_signature(..., webhook_dev_mode=True)` skips HMAC verification when no secret is configured.
2. After JSON parsing, the handler detects and unwraps smee's `{"payload": "<json-string>"}` envelope so downstream code sees the real GitHub payload unchanged.

See [webhooks.md](webhooks.md#option-b--smeeio-relay-local-development) for the end-to-end setup. **Never enable this configuration in production** — any unsigned `POST` to `/api/v1/webhook` will be accepted and could trigger jobs.

### Telegram Bot

- Webhook registered on app startup via `setWebhook` API
- Secret token verification via `X-Telegram-Bot-Api-Secret-Token` header
- Per-question messages with inline "Answer in Dashboard" buttons
- Reply-to-message matching for free-text answers

---

## Future: Spring Boot SaaS Layer

Spring Boot manages a fleet of embry0 instances — one per tenant. embry0 remains single-tenant internally.

```mermaid
graph TB
    subgraph SaaS["Spring Boot SaaS Platform"]
        GW["API Gateway"]
        TM["Tenant Manager"]
        BL["Billing"]
    end

    subgraph Fleet["embry0 Fleet (K8s)"]
        L1["embry0<br/>Tenant A"]
        L2["embry0<br/>Tenant B"]
        L3["embry0<br/>Tenant C"]
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
