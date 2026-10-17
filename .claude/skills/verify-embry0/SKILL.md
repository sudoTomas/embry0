---
name: verify-embry0
description: Drive the embry0 dashboard and its REST API the way an operator does, from any checkout or pull request worktree. Builds the head's dashboard beside a running embry0 stack, proves which revision it serves, then probes pages, takes screenshots and reads API responses, read-only. Use when proving a dashboard or API change, reproducing a dashboard bug, or checking which build a deployment serves.
---

# Verify embry0

The user surface is the dashboard (React SPA served by the frontend's nginx) and the REST API behind it at `/api/v1`, plus the MCP server. A running embry0 stack (`infra/docker-compose.yml`) owns the orchestrator, Postgres and the agent runtime. This skill never starts or restarts that stack. It builds a second dashboard container from the checkout under review and attaches it to the stack's frontend network, so the branch's UI runs against the real API without a deploy.

What this proves and what it does not:

- A frontend change is proved here, on the launched container, before it merges.
- A backend change needs the orchestrator rebuilt (`infra/deploy.sh orchestrator` on the host that runs the stack). After that deploy, drive the deployment's own dashboard URL with `EMBRY0_VERIFY_URL`.

Helper (executable): `.claude/skills/verify-embry0/bin/control-embry0`

```bash
B=.claude/skills/verify-embry0/bin/control-embry0
A=.claude/skills/verify-embry0/artifacts
$B setup                                   # once per machine: installs the browser driver
$B launch --checkout "$PWD"                # this checkout's dashboard on 127.0.0.1:8201
$B doctor                                  # worth driving? which revision does it serve?
$B probe --route /jobs --width 390
$B shot  --route /jobs --width 390 --path $A/jobs-390.png
$B read  --route / --selector main
$B api   /api/v1/orgs                      # GET only
$B stop
```

JSON on stdout. `artifacts/` and `.state/` are gitignored: screenshots show real repository names, jobs and costs from the deployment, so they stay on the machine that took them.

## Launch

- Run on the host that runs the stack: `launch` needs its Docker daemon and the compose network the dashboard's nginx resolves `orchestrator` and `mcp` on (`embry0_frontend` by default; `EMBRY0_VERIFY_NETWORK` for another project name).
- `launch --checkout DIR` builds `DIR/frontend` with the repo's own `frontend/Dockerfile`, labels the image with the checkout's short HEAD, and runs it on `127.0.0.1:8201` (`--port N` for another). Ready when `launch` prints `"ok": true`; the port answered.
- `--dry-run` prints the build and run commands and starts nothing.
- One launched dashboard per port. `launch` refuses a taken port.
- The API key: `EMBRY0_API_KEY`, else the `API_KEY=` line of `EMBRY0_ENV_FILE` (default: the checkout's root `.env`; a worktree has none, so point it at the deployment's root `.env`). The key never reaches stdout.

## Doctor

```bash
$B doctor
```

- `health`: `GET /health` through the launched nginx.
- `ready`: `GET /api/v1/health/ready` with the key, which also proves the container reaches the orchestrator.
- `api_key_accepted`: `GET /api/v1/queue` with the key answered 200. The dashboard's gate (`frontend/src/components/auth/AuthGate.tsx`) uses a stored key before any SSO redirect, so the driver seeds the key in `localStorage` (`embry0.api-key`) and never meets the identity provider.
- `bundle`: the module script `index.html` loads. Two dashboards built from the same frontend tree serve the same bundle name.
- `served.head`: the revision label of the launched image. It must equal the pull request head with `dirty_frontend_files: 0` from `launch`.
- `worth_driving: false` means stop. Do not drive a dashboard that never answered.

## Drive

Routes are react-router paths: `/`, `/jobs`, `/jobs/:id`, `/issues`, `/repos`, `/repos/<owner>/<name>`, `/qa`, `/watcher`, `/logs`, `/agents`, `/environments`, `/credentials`, `/pipelines`, `/settings`, `/providers`, `/orgs` (when the branch has it). Every page renders inside `main`; the sidebar carries `data-testid="sidebar"` with `data-mode` (`column` at desktop width, `drawer` below 768 px).

```bash
$B probe --route /credentials              # breadcrumbs, h1, horizontal_scroll, sidebar mode, table_rows, errors
$B probe --route / --width 390             # phone width: sidebar is a drawer behind "Open menu"
$B read  --route /agents --selector "main table"
$B api   /api/v1/jobs?brief=true&limit=5
```

Read-only. The launched dashboard and the deployment share one API, one database and one agent runtime. Never press a control that writes (New job, Cancel, Resume, Accept, Approve, Register, Discover, Save, Delete), and `api` refuses anything but GET. A change to a write flow is proved by reading the form, the button state and the request it would send up to the last click, and by an operator making that click on the deployment.

Feature recipes: `features/README.md`. Match the change to a feature file and drive every entry point it lists.

## Evidence

- Directory: `.claude/skills/verify-embry0/artifacts/`. It survives `stop`.
- A proof shows: `doctor` output with `served.head` equal to the head under review, the `probe` JSON of each touched route at 1440 and at 390, a screenshot of each, and for an API change the `api` response that carries the new field.
- `probe.errors` lists page errors, console errors and every response at 400 or above. A proof with errors on the touched route is not a pass.

## Cleanup

```bash
$B stop
```

Removes only the container and image this helper launched (ids in `.state/`). It never touches the stack's own containers. Artifacts stay.

## Proved

From a worktree at main: `setup`, `launch --checkout`, `doctor` (health 200, ready ok, key accepted, `served.head` equal to the worktree head), `probe` of `/` at 390 (no horizontal scroll, `Open menu` present, breadcrumbs `embry0 / Overview`), `shot` of `/pipelines`, `api /api/v1/orgs`, the navigation feature driven end to end, `stop`; the screenshots survived and the deployment's own dashboard kept serving.
