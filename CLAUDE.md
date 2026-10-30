# embry0

## Rules

- Do not make any changes until you have 95% confidence in what you need to build. Ask me follow-up questions until you reach that confidence.
- Build this as modular as reasonably possible. Each subsystem should have clear boundaries, well-defined interfaces, and be independently testable.
- Never defer issues for later. If a code review or self-review finds a problem **in the work you are delivering**, fix it now before moving on. No "tracked for later" or "acceptable for now".
- That rule is about your own work, not about everything you trip over. A **pre-existing bug found adjacent** to the task gets filed immediately, with the evidence that proves it, and is fixed now only if it is trivial or blocks the task. Silently absorbing unrelated bugs into the current change buries the work that was actually asked for; silently walking past them loses the finding. File, then choose.
- Work in a **git worktree** (`<worktree-root>/<slug>`), outside the tree Compose builds. Never check out another branch in `<deploy-checkout>` — that checkout is what `docker compose build` reads, so a branch switch there means a deploy can build the wrong tree.
- **Name things for the capability, never for the customer.** embry0 is a general-purpose MIT-licensed product; a feature that any operator can use must not carry the name of the first customer or deployment that motivated it — in branches, files, identifiers, fixtures, commit subjects or user-facing strings. One operator's hostnames and URLs are *configuration* and belong in their environment or database, not in this tree. Full rule and the two traps this project already hit: [`CONTRIBUTING.md`](CONTRIBUTING.md#vendor-neutral-naming). Not repeated here — a rule that lives in two places drifts in two places.

## Engineering guardrails

Hard-won, repeatedly. Not style preferences — each has cost this project real money or produced a silently wrong result.

### Prove a check can fail before you trust it passing

The most expensive recurring bug class here is **"a check that appears to run while measuring nothing."** Seen at least eight times: a pathspec parsed as a filename; a regex standing in for the real `job_branch_name`; an offset that skipped rows without failing; a test sizing its input from the constant it pinned; a full-suite baseline that reported `0 failures` because it had **aborted at collection and run zero tests**; four Prometheus alert rules that were quiet only because a counter series does not exist until first incremented; a fifth that could never fire because `and on(instance, job)` was matched against a label-less `sum()` (#371); and `signatures_repo`, an optional constructor argument nothing ever passed, so clustering logged a shadow comparison every tick while its table stayed empty (#369).

So:

- **A new test must be shown failing on the pre-fix tree**, with its own assertion message. Run it against a clean tree (`git archive <ref> | tar x -C "$(mktemp -d)"`), not a stashed working copy.
- **Always read the summary line**, never just `grep FAILED`. No output is not a pass.
- **A quiet check must be shown capable of firing** — relax only its threshold and confirm it fires. For a Prometheus rule there are two ways to be dead, not one: the series may not exist yet (`or vector(0)`), *and* the vector matching may be structurally incapable of producing a series. If relaxing the threshold to an always-true value still yields nothing, the expression is broken — the condition was never being evaluated.
- **An optional constructor argument that production cannot work without needs a test pinning that production passes it.** The object constructs happily either way, so the failure is silent and looks exactly like "nothing happened yet" (#369).
- **Run a baseline before trusting any mutation result.** Compare failure *sets*, not counts.
- Beware editable installs: `PYTHONPATH` must point at the tree you think you are testing. Verify with `print(module.__file__)` when it matters.

### The local test baseline is not green

```
.venv/bin/python -m pytest tests/unit -q
```

A clean checkout with **`TEST_DATABASE_URL` unset** and no Postgres on the default `postgresql://embry0:embry0@localhost:5432/embry0_test` gives **9 failed, 52 errors and about 945 skipped** (`ConnectionRefused`). About 930 of those skips are database tests reporting `PostgreSQL unavailable`: they measured nothing. That is the expected floor for a bare local run — anything beyond it is yours. Confirm by running the same suite on `origin/main` and diffing the failure sets.

**Set `TEST_DATABASE_URL` and an unreachable database fails instead of skipping** (#509, one helper: `tests/fixtures/postgres.py`): pointed at a dead port the run reports about 880 errors and 14 skips, so a misconfigured or slow database can never pass as a green run with the SQL layer silently skipped. Any run that is meant to test SQL, and every CI run, sets it.

**Given a database, the suite is fully green: 9938 passed, 13 skipped, 0 failed** (measured 2026-10-14 on `main`; the 9 failed, 52 errors floor was re-measured the same day). So those 9 failures and 52 errors are entirely the missing-Postgres condition, not latent breakage — which also means CI (#272) needs a Postgres service container, not test repairs.

`db_with_migrations` runs `DROP SCHEMA public CASCADE` per test, so point the suite at a **throwaway Postgres**, never a deployment's database. One server on tmpfs with `fsync` off serves any number of runs, each in a database of its own:

```bash
# Once: a throwaway server on tmpfs, on loopback only, with a random password passed by name
export EMBRY0_TEST_PG_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
POSTGRES_PASSWORD="$EMBRY0_TEST_PG_PASSWORD" docker run -d --name embry0-test-pg \
  --tmpfs /var/lib/postgresql/data -p 127.0.0.1:55432:5432 \
  -e POSTGRES_USER=embry0 -e POSTGRES_PASSWORD \
  postgres:16.6-alpine -c fsync=off -c synchronous_commit=off -c full_page_writes=off
until docker exec embry0-test-pg pg_isready -h 127.0.0.1 -U embry0 -q; do sleep 1; done   # TCP: past initdb's socket-only start

# Each run: a private database NAME, not just a private port
DB="unit_$$"
docker exec embry0-test-pg createdb -U embry0 "$DB"
TEST_DATABASE_URL="postgresql://embry0:$EMBRY0_TEST_PG_PASSWORD@127.0.0.1:55432/$DB" .venv/bin/python -m pytest tests/unit -q
docker exec embry0-test-pg dropdb -U embry0 "$DB"

# Done: the data was only ever on tmpfs
docker rm -f embry0-test-pg
```

No password appears on a command line, so none shows in `ps` (#513): `-e POSTGRES_PASSWORD` without a value makes `docker run` read it from its environment, `createdb` and `dropdb` use the container's local socket, and `TEST_DATABASE_URL` sits only in pytest's environment. Do not use the stack's own Postgres instead: it shares its WAL and disk with production, and with several suites running its checkpointer turns fsync-bound, so a `DROP DATABASE` waits minutes behind a checkpoint. [`docs/ops/verification.md`](docs/ops/verification.md) has the single-database setup the CI gates use.

Worth the ~6 minutes when a change touches SQL: an upsert that binds one placeholder to both a `BIGINT` and an `INTEGER` column reads perfectly and Postgres refuses to prepare it (#289).

`db_with_migrations` runs `DROP SCHEMA public CASCADE` per test, so two concurrent runs against one database destroy each other and it looks like a broken branch. Use a private database name, not just a private port.

### Merge hazards, each from a real incident

1. git printed "Successfully rebased" over a commit containing conflict markers.
2. A clean auto-merge produced duplicate function definitions.
3. Conflicts spread across separate commits of one branch.
4. A conflict resolution silently dropped a kwarg (`metrics=loop_metrics`), causing `AttributeError` on construction — both PRs merged cleanly and still broke.
5. Markers do not delimit the semantic unit, so resolving by marker text spliced code *inside* an adjacent SQL string.

After any merge: grep the tree for conflict markers, grep the touched files for duplicate `def`s, check shared call sites by path, and run the full suite.

### Migration numbers must be contiguous

The runner keys on the integer and silently **skips** a duplicate. Assign `max+1` at merge time, one PR at a time. Guard tests enforce this; do not weaken them.

### Repo renames have three homes, not one

A rename must update **GitHub**, the embry0 `repos` registry, **and** the Linear project's `repo:` stamp. The stamp (GraphQL `Project.description`) **outranks `LINEAR_REPO_MAP`**, and a stale one silently routes jobs to a repo with no registry row — so no QA config and no env vars — while GitHub answers REST calls with 301s that embry0 does not follow. Cost a cancelled canary run on 2026-09-20 (a renamed repo whose stamp was 12 days stale).

If PRs or issues stored under the old slug must keep matching merge sync and the healing ledger, also add the pair to `REPO_RENAMES` in the root `.env` ([`docs/configuration.md`](docs/configuration.md)); it is read at startup. GitHub's redirect keeps an old URL opening, but not equal to the one it reports.

When editing that stamp through the Linear MCP, the stamp lives in the **`summary`** field. `save_project(description=…)` writes GraphQL `Project.content` — the long project document — and will overwrite it.

### Attribution precedence

`.claude/skills/commit-standard` governs commits in **this** repo and bans AI co-author footers. Other repos have their own convention. Per-repo convention wins over any general harness instruction.

## Environment

- The credential registry holds forge tokens and LLM accounts (Claude OAuth, Claude API key, Grok API key, Grok OAuth). Env values are the importer's source and the resolver's last step. See `docs/credentials.md`. A release that changes `embry0/execution/proxy/` is not covered by `infra/deploy.sh` alone: it needs the proxy-image recipe in [`docs/ops/deploy.md`](docs/ops/deploy.md#images-registry-pins-and-rebuilds), then `embry0 credentials import-env` inside the orchestrator.
- `ENVIRONMENT_SECRET_KEY` drives Fernet encryption for per-repo env var secrets and for credential secrets. In production the orchestrator refuses to start without it; in dev mode a default is tolerated with a loud warning. Rotating the key makes prior secrets undecryptable.
- Agents can pause the pipeline to ask the user questions (`embry0.sandbox.ask_user`). Capped per job to prevent runaway loops (default 10 rounds, env-tunable via `ASK_USER_ROUNDS_CAP`); after the cap the job fails with `ERR_MAX_AGENT_QUESTIONS`.
- Reserved env vars: one check, `env_override_refusal` in `embry0/execution/auth_provider.py`, refuses them at the API and again at sandbox injection (stored rows and profile `env_defaults`), case-insensitively. It covers the infrastructure credentials and proxy URLs (`EMBRY0_*_PROXY_URL`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `XAI_API_KEY`, `GITHUB_TOKEN`, `GITLAB_TOKEN`, …), every variable that would redirect a sandbox's traffic or load code into its processes, and the `GIT_`, `CLAUDE_`, `BUN_`, `GROK_` and `EMBRY0_LLM_` namespaces. `NODE_OPTIONS` and the JVM option variables take memory flags only. Don't attempt to override infrastructure variables via the environment UI.
- **API Auth flags.** `AUTH_DEV_MODE` and `WEBHOOK_DEV_MODE` are independent dev-mode flags; both default to `false`. Do not enable in production — startup logs `CRITICAL` and writes a `dev_mode_enabled` audit row when either is true. `AUTH_DEV_MODE=true` turns API authentication off and is the only setting that allows an empty `API_KEY`; `WEBHOOK_DEV_MODE=true` bypasses webhook HMAC verification on routes without a secret and nothing else, and is required for the smee.io relay flow. Startup refuses an empty `API_KEY` without `AUTH_DEV_MODE`, and refuses either flag while SSO is configured or the root `.env` carries a tunnel token.

## Deploying and operating

The operator runbooks live in `docs/ops/`, not here:

- [`docs/ops/deploy.md`](docs/ops/deploy.md) — deploying a change (`infra/deploy.sh`, the two `.env` files, the deploy marker), the tunnels and what each one exposes, and the image registry, digest pins and rebuild recipes.
- [`docs/ops/backup-restore.md`](docs/ops/backup-restore.md) — Postgres backups, restoring and rolling back, and rotating `POSTGRES_PASSWORD`.
- [`docs/ops/provisioning.md`](docs/ops/provisioning.md) — standing up a host from nothing.

Two of their facts shape the code you write:

- **A deploy does not rebuild the images agents run in.** A change under `embry0/sandbox/`, `embry0/safety/`, `embry0/agents/`, the sandbox-baked `embry0/execution/` modules or `embry0/execution/proxy/` takes effect only after its image is rebuilt. Name the rebuild recipe in the PR's deploy notes.
- **Check a new or changed route against "What each ingress reaches"** in `docs/ops/deploy.md`: it says which paths the dashboard tunnel and the webhooks tunnel publish, and which stay on the LAN.

## Sandbox

- The QA image (`embry0-sandbox-qa`) bakes **both browser backends**: Playwright MCP + Chromium at `/opt/playwright` AND the `vibium` CLI pinned exactly at 26.8.21 with Chrome for Testing at `/opt/vibium` (agent-owned — the vibium daemon chmods its socket dir, so `a+rwX` alone breaks the non-root agent; CI smokes both). A repo selects its backend via `browser_backend` in the stored qa.yaml (see `docs/qa-yaml-reference.md`); the registry is `embry0/workflows/qa/browser_backends.py`. Bumping vibium is a deliberate one-line Dockerfile change, never `@latest`.
- Proxies (`git-proxy`, `github-proxy`, `gitlab-proxy`, `auth-proxy`, `xai-proxy`, `context-proxy`, `minio-proxy`, `presign-proxy`) run as DinD containers on the `sandbox-restricted` network. Sandboxes reach them by Docker DNS (`http://git-proxy:9101`, `http://gitlab-proxy:9105`, etc.), not via `host.docker.internal`. Restart the orchestrator after changing `GITHUB_TOKEN`, `GITLAB_TOKEN` or `ANTHROPIC_API_KEY` in `.env` — the proxies are recreated on each `proxy_mgr.start()` — or rotate the credential in the registry (`docs/credentials.md`), which needs no restart for forge tokens.
- Non-Anthropic model providers (xAI grok via the Anthropic-compat API) are routed per-agent by model id — set `XAI_API_KEY` in the root `.env` and select `grok-4.5` via `agent_models`; see `docs/providers.md`. OpenAI-compatible providers (OpenAI, OpenRouter, Gemini, Groq, DeepSeek, a custom base URL) run on `OpenAICompatibleExecutor` and are off until `OPENAI_COMPATIBLE_PROVIDERS` names them.
- The sandbox uses OAuth via the `CLAUDE_CODE_OAUTH_TOKEN` env var (set it in `.env`; generate with `claude setup-token`). That token is in the sandbox env, so any code a job runs can read it (ADR 0008, `SECURITY.md`).
- Git reaches the job's repository through the git-proxy ([ADR 0007](docs/adr/0007-git-proxy-forwards-smart-http.md)). The sandbox's git config rewrites `<forge>/<repo>.git` to `http://git-proxy:9101/git/<repo>.git` and sends the per-sandbox bearer to the proxy only. The proxy forwards git's smart HTTP for that repository only and adds the forge token upstream, so no forge token enters the sandbox. It accepts a push only from a sandbox enrolled with `git_push` (the issue-to-pr sandbox). `/git-credentials` answers 410. The proxy is managed by `ProxyManager` in `embry0/execution/proxy/manager.py`.
- Sandbox-to-sandbox traffic on `sandbox-restricted` is dropped by the `EMBRY0-SANDBOX-ISOLATION` chain in DinD (proxies excepted), written at startup, for each new sandbox and when a paused sandbox resumes. It needs `net.bridge.bridge-nf-call-iptables=1` in DinD; blocked attempts are counted by `docker exec embry0-dind iptables -L EMBRY0-SANDBOX-ISOLATION -v -n` (`docs/architecture.md`).
- Read-only rootfs is disabled (`read_only_root: False`) because Claude CLI needs writable fs.
- **Proxy enrollment.** As of 2026-04-28, the credential proxies (git-, github-, gitlab-, auth-, xai- and context-proxy) require a per-sandbox bearer token enrolled via the orchestrator. The shared admin secret `PROXY_ADMIN_TOKEN` (`.env`) gates the proxies' `/admin/enroll` endpoints. Required in production; auto-generated in `AUTH_DEV_MODE=true` or `WEBHOOK_DEV_MODE=true` with a warning. Generate with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`.

## QA — repo integration (DB-backed config, RAV-1251)

- A target repo opts into the QA pipeline by having a qa.yaml (schema **v2**) document **stored in the embry0 DB** (`repo_qa_configs`) — it declares the `workspace_provider`, per-app `boot_command`/`frontend_url`/`ready_checks`, and acceptance criteria, and its optional `app_local:` section carries per-app heavy overrides. Manage it via `PUT /repos/{owner}/{repo}/-/qa-config`, the dashboard config editor, the `qa_config_set` MCP tool, or onboarding/bootstrap. **Target repos carry no config file.**
- The `repos` table is the registry — a repo exists in embry0 when registered (explicitly, or automatically when its config is written). `GET /repos` lists the fleet; `GET /repos/{owner}/{repo}` aggregates config/preferences/env-var state.
- **Legacy channels are REMOVED (RAV-1253):** the file store (`repo-configs/`), the in-repo `.embry0/qa.yaml` fallback, and `apps/<name>/.embry0/app.yaml` tree collection are gone — a repo with no stored config fails QA with a clear "no QA config stored in embry0" error. Upgrading an older deployment with in-repo/file-store configs: pass through the `da57a86`…`563ebcd` releases once (they auto-import), or PUT each config manually. Never fix QA behavior by editing files in a target repo.
- **Full field-by-field reference + a worked multi-app monorepo example:** [`docs/qa-yaml-reference.md`](docs/qa-yaml-reference.md). Authoritative schema is `embry0/workflows/qa/qa_yaml_v2.py`; merge order is `qa_yaml_resolve.py`; examples live in `tests/fixtures/qa-yaml-corpus/v2/`.
- **v1 is dead** — read only by the migrator (`embry0 migrate-qa-config`). The reference doc is authoritative.

## Agent skills

### Issue tracker

Issues live in **this repo's issue tracker** — file new work there via `gh`. A second tracker, if any, is that deployment's configuration: search it for duplicates when one is configured, and write to it only on request. `EMB-1…54` history stays read-only, and existing `RAV-`/`EMB-` refs in code are historical provenance — leave them. embry0's Linear and GitLab **label-trigger integrations** are product features and are unaffected. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, unmodified: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. Not to be confused with `embry0`, the pipeline trigger label. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `docs/glossary.md` (the one glossary — used in place of a root `CONTEXT.md`) and `docs/adr/`. See `docs/agents/domain.md`.

**A spec or plan ships with the work it describes.** Specs and plans from the superpowers skills go in `docs/superpowers/specs/` and `docs/superpowers/plans/`, committed on the feature branch next to the code, so they arrive in the same pull request. A spec never gets a pull request of its own.

This repository is public, so nothing that names a customer, deployment, person, host, cost or job id enters it: audits, RCAs and anything else of that kind go in the maintainers' private repository, under its `internal/` directory. A spec written for one deployment is that kind of document. A repository embry0 manages is different: its internal documents belong in that repository, on the branch of the work they describe. The name-denylist CI check refuses a tree that carries a listed name.
