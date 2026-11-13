<div align="center">

<img src="frontend/public/apple-touch-icon.png" width="72" alt="embry0 logo" />

# embry0

**Software that is alive.** An issue goes in. A reviewed, browser-tested pull request comes out.

[![CI](https://github.com/sudoTomas/embry0/actions/workflows/ci.yml/badge.svg)](https://github.com/sudoTomas/embry0/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)](https://python.org)
[![React 19](https://img.shields.io/badge/react-19-61DAFB?logo=react&logoColor=white)](https://react.dev)
[![LangGraph](https://img.shields.io/badge/langgraph-orchestration-06b6d4)](https://github.com/langchain-ai/langgraph)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

[Quick start](#quick-start) · [How it works](#how-it-works) · [Features](#features) · [Security model](#security-model) · [Docs](#documentation)

<img src="docs/assets/demo.gif" width="100%" alt="embry0 in use: the guild hall, the map, a job running live, a finished job with its checked finish line, and the pipeline it ran" />

</div>

embry0 is a self-hosted engine that resolves issues from GitHub, GitLab or Linear by sending AI agents through a pipeline. A **triage agent** plans the work. A **developer agent** writes the code and opens the pull request. A **reviewer agent** sends it back until it is right. An optional **QA agent** boots your app and checks the acceptance criteria in a real browser. Every agent runs inside its own locked-down Docker sandbox, and you stay in charge: agents ask when they are unsure, and nothing merges without you.

You run it from a dashboard that can look like a pixel-art guild or a plain operations console. It is the same data either way.

<table>
  <tr>
    <td width="50%"><img src="docs/assets/guild-hall.png" alt="The guild hall: agents at their desks, quests on offer and finished work waiting for review" /><br /><sub><b>The guild hall.</b> Your agents at their desks, the work embry0 found, and what is waiting for your review.</sub></td>
    <td width="50%"><img src="docs/assets/guild-map.png" alt="The map: each repository drawn as a village with open work around it" /><br /><sub><b>The map.</b> Each repository is a village. Open work appears around it as quests.</sub></td>
  </tr>
  <tr>
    <td><img src="docs/assets/job-brief.png" alt="A finished job with its brief, a checked finish line and the pull request" /><br /><sub><b>A finished job.</b> The brief, a finish line checked off with evidence, and the pull request.</sub></td>
    <td><img src="docs/assets/job-live.png" alt="A running job: the pipeline graph beside the agent's live tool calls" /><br /><sub><b>A job, live.</b> Reasoning, tool calls, turns and cost as they happen.</sub></td>
  </tr>
  <tr>
    <td><img src="docs/assets/ask-human.png" alt="A job paused while the developer agent asks a question" /><br /><sub><b>It asks first.</b> An unsure agent pauses the job and sends you the question.</sub></td>
    <td><img src="docs/assets/pipelines.png" alt="The pipeline editor showing triage, developer, reviewer and QA as a graph" /><br /><sub><b>Pipelines.</b> Each workflow is the graph it runs, and you can edit it.</sub></td>
  </tr>
  <tr>
    <td><img src="docs/assets/agents.png" alt="The agent roster with levels, models and providers" /><br /><sub><b>The roster.</b> Core agents plus read-only specialists, each with its own model and limits.</sub></td>
    <td><img src="docs/assets/achievements.png" alt="The achievements diary with tiered badges" /><br /><sub><b>Achievements.</b> Badges agents earn from what actually happened: merges that landed, errors that stayed dead.</sub></td>
  </tr>
</table>

<sub>The screenshots come from a running instance. Repository names, issue text and people are demo data.</sub>

## How it works

```mermaid
graph LR
    subgraph In["Work comes in"]
        GH["GitHub / GitLab<br/>webhook"]
        LN["Linear<br/>webhook"]
        FOUND["Work embry0 finds<br/><small>leads, log watcher</small>"]
    end

    subgraph Orch["Orchestrator"]
        TR["Triage agent<br/><small>plans and routes</small>"]
        LG["LangGraph engine<br/><small>checkpointed in PostgreSQL</small>"]
    end

    subgraph Sandbox["One sandbox per job"]
        DEV["Developer"]
        REV["Reviewer"]
        QA["QA<br/><small>real browser</small>"]
    end

    PX["Credential proxies<br/><small>forge tokens stay here</small>"]
    YOU(["You<br/><small>dashboard, Telegram,<br/>GitHub comment</small>"])
    PR["Pull request"]

    GH --> TR
    LN --> TR
    FOUND --> TR
    TR --> LG --> DEV
    DEV --> REV
    REV -->|changes requested| DEV
    REV -->|approved| QA
    QA -->|failed| TR
    QA -->|passed| PR
    DEV -.->|git push| PX -.-> PR
    DEV -.->|questions| YOU
    YOU -.->|answers| DEV
    YOU -.->|merge approval| PR
```

**Where your code lives.** Agents work on a repository only inside its job's sandbox container, which is deleted when the job ends. Copies outlive it on the host: the job's event log and saved agent session in PostgreSQL (files the agent read and wrote, its shell commands) and pre-built QA images and per-repo cache volumes (a checkout). By default they are kept forever; `JOB_LOG_RETENTION_DAYS`, `AGENT_SESSION_RETENTION_DAYS` and `QA_IMAGE_RETENTION_DAYS` bound them, though not the registry's copies of QA images or database backups. [Data retention](SECURITY.md#data-retention) lists every copy and how long it stays.

## Features

- **Issue-to-PR pipeline** — triage scores confidence, splits oversized issues, and configures model tiers, validator modes, and sandbox profiles per job; the developer agent owns code, git, and PR creation; the reviewer loops it until approved.
- **Browser-verified QA gate** — boots your app's compose stack inside DinD and drives headless Chromium via Playwright against your acceptance criteria, with screenshots, console, network, and container logs as evidence.
- **The guild** — the home screen: your agents as characters in a guild hall, the work embry0 found as a quest board, every job a quest you send a party on, watch live and judge when it comes back (merge, send back with notes, reject, rate). On by default; one setting [turns the game layer off](docs/world.md#turning-the-game-off) for a plain operations dashboard. See [docs/world.md](docs/world.md).
- **Specialist agents** — ten read-only built-ins that each return one report on a repository: a ledger of its cracks (kintsugi), one provable weed (gardener), a four-angle review (interrogator), why and how answers, a proven blast radius, a comment pass, a design audit, an audit of the instruction files that steer coding agents (context-auditor) and a map of a monorepo's areas (area-mapper). See [docs/specialist-agents.md](docs/specialist-agents.md).
- **Human-in-the-loop** — agents pause to ask questions, dispatched to the dashboard, Telegram, or a GitHub comment; the pipeline resumes when answered from any channel.
- **Issue tracker with GitHub two-way sync** — board and list views, webhook-triggered or manual dispatch.
- **Live dashboard** — React SPA streaming agent thinking, tool calls, costs, and QA evidence over WebSocket.
- **Budget controls** — per-job, daily, and monthly caps with soft/hard overrun modes.
- **Your choice of model** — Claude by subscription token or API key, xAI Grok, any OpenAI-compatible provider (OpenAI, OpenRouter, Gemini, Groq, DeepSeek or your own endpoint), or a local model behind vLLM, Ollama or LM Studio, chosen per agent. See [docs/providers.md](docs/providers.md).
- **Work it finds by itself** — leads from Linear and GitHub, and a log watcher that drafts issues from production logs and can watch embry0 itself. See [docs/leads.md](docs/leads.md) and [docs/self-watching.md](docs/self-watching.md).
- **Pull request review** — advisory reviews posted on pull and merge requests. See [docs/pr-review.md](docs/pr-review.md).
- **Organizations, areas and maps** — per-organization standards, monorepo areas and a map of each repository. See [docs/standards.md](docs/standards.md), [docs/areas.md](docs/areas.md) and [docs/maps.md](docs/maps.md).
- **Built for a team** — Auth0 single sign-on, API keys with scopes, an audit log, Prometheus metrics and an MCP server. See [docs/sso.md](docs/sso.md).

## Security model

embry0 runs code an LLM agent writes, against repositories and issues you may not control. [SECURITY.md](SECURITY.md) is the threat model; the short version:

- **What holds.** Every job runs in its own container inside Docker-in-Docker, as a non-root user, with `--cap-drop=ALL` and `no-new-privileges`, which a sandbox profile cannot change. Forge tokens stay in credential proxies, and each sandbox has its own enrollment with them. Git reaches only the job's repository and pushes only from the issue-to-PR sandbox ([ADR 0007](docs/adr/0007-git-proxy-forwards-smart-http.md)); the forge API proxies forward only read calls on that repository. A sandbox cannot open a connection to another sandbox on the proxies' network, and its traffic to private, carrier-grade NAT and link-local addresses is dropped. Environment variables that would hook a sandbox's processes are reserved. The shared Docker daemon needs an admin's grant per repository, and embry0's own images run by digest.
- **What does not.** The agent's LLM credential (a Claude subscription token or an API key) is in the sandbox's environment, and public egress is open, so any code a job runs can read it and send it out ([ADR 0008](docs/adr/0008-llm-credential-in-sandbox-env.md)). The bash denylist (Ring 3) and the CLI's permission rules (Ring 2) are defence in depth, not boundaries. A repository granted Docker access is trusted code, root-equivalent on the host. An operator-tier key can run commands in a sandbox. By default the job log, saved agent sessions and pre-built QA images keep the repository's code with no end date ([Data retention](SECURITY.md#data-retention)). Details and the rest of the list: [What does not hold](SECURITY.md#what-does-not-hold).

Found a vulnerability? Report it privately, as [SECURITY.md](SECURITY.md#reporting-a-vulnerability) describes.

## Quick start

Requires Docker with the Compose plugin (`docker compose`) and Python 3.12+, on an x86_64 Linux host. The stack runs Docker-in-Docker in a privileged container. Its containers' memory limits total about 24 GB, each job's sandbox can take up to 8 GB more, and a first install, with its images and build cache, takes about 20 GB of disk.

```bash
git clone https://github.com/sudoTomas/embry0.git
cd embry0
cp .env.example .env
# Set in .env. Generate each secret with: openssl rand -hex 32
#   API_KEY, PROXY_ADMIN_TOKEN, ENVIRONMENT_SECRET_KEY, POSTGRES_PASSWORD, MINIO_ROOT_PASSWORD
#   GITHUB_TOKEN=ghp_...
# and one Claude credential:
#   CLAUDE_CODE_OAUTH_TOKEN=...   (the default auth mode, oauth; get one with `claude setup-token`)
#   or, for an API key: DEFAULT_AUTH_MODE=api_key and ANTHROPIC_API_KEY=sk-ant-...

python3 -m venv .venv && . .venv/bin/activate
pip install -e .          # installs the embry0 CLI
embry0 start              # builds every image, starts the stack, waits for health
```

Open the dashboard at **http://localhost:8200** and sign in with your `API_KEY`. Then create an issue and click **Send to Triage**. To trigger jobs straight from GitHub, set up [webhooks](docs/webhooks.md); the Cloudflare tunnel for them is an optional compose profile.

`embry0 start` reads this root `.env`, and `infra/.env` only for a `COMPOSE_FILE` overlay such as the [rootless sandbox runtime](docs/ops/provisioning.md#10-a-rootless-sandbox-daemon). Running compose yourself from `infra/` (as `infra/deploy.sh` does) reads `infra/.env` instead: start it from [`infra/.env.example`](infra/.env.example).

## Documentation

| Guide | What's inside |
|---|---|
| [Architecture](docs/architecture.md) | Full system reference — topology, pipelines, sandbox and proxy design, storage, job lifecycle |
| [Configuration](docs/configuration.md) | Environment variables, secrets at rest, execution/auth modes, CLI reference |
| [API reference](docs/api.md) | REST endpoints with curl examples, WebSocket event stream |
| [Onboarding a repo](docs/onboarding.md) | `embry0 init` (new projects) and `embry0 onboard` (existing repos) — zero-to-pipeline in one command |
| [Running QA](docs/running-qa.md) | Integrating a target repo, `qa.yaml`, gotchas, triggering and watching runs |
| [`qa.yaml` v2 reference](docs/qa-yaml-reference.md) | Field-by-field schema reference with monorepo examples |
| [Webhook setup](docs/webhooks.md) | Cloudflare Tunnel (production) and smee.io relay (local dev) |
| [Pull request review](docs/pr-review.md) | Advisory reviews posted on pull and merge requests: triggers, depth, forks, re-reviews |
| [Glossary](docs/glossary.md) | Domain vocabulary |
| [Decision records](docs/adr/) | ADRs — the decisions the codebase embodies and the evidence behind them |

## Development

```bash
uv sync --extra dev              # or: pip install -e ".[dev]"
uv run pytest tests/unit -q      # fast unit suite
uv run ruff check embry0 tests
cd frontend && npm install && npm test
```

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and commit conventions.

## Roadmap

Kubernetes deployment (Helm, DinD → pod launching) · user-defined LangGraph workflows via API · pipeline template marketplace · configurable per-agent Claude Code skills.

## License

[MIT](LICENSE) — use, modify, and distribute freely, including in proprietary and commercial software.

The project publishes no container images: you build them from this repository. They contain third-party software under its own terms, including the proprietary Claude Code CLI that the `claude-agent-sdk` wheel bundles. [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) lists it.

---

<div align="center">
  <sub>Built with LangGraph, Claude Agent SDK, FastAPI, React, and PostgreSQL</sub>
</div>
