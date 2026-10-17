# Trace fidelity plan D: the Grok Build spike and the direct executor's conformance

> **For agentic workers:** this plan is executed in waves, one git worktree per repo task (`tfd-<task>`, branch `tfd/<task>` from `feat/trace-fidelity-d`); the spike's harness and captures live in the controller's scratchpad and never enter the tree. The controller integrates each wave and runs the gates. Steps use checkbox (`- [ ]`) syntax. An implementer reads only its own task plus the header sections above the tasks.

**Goal:** answer issue #42 with evidence (can Grok Build run as the grok executor inside a sandbox through the xai-proxy, with the six acceptance criteria green or red), land the findings document, and bring `DirectXaiExecutor` up to the event contract where the spike leaves it in place. Issue #43 (the `GrokBuildExecutor` swap) is executed only if the spike is green on all six criteria and two product decisions are taken; its shape is outlined at the end for a plan E, not executed here.

**Architecture:** the spike is throwaway: a scratch image built on the local `embry0-sandbox` image with the pinned Grok Build (GB) binary, a scratch internal Docker network, the `embry0-proxy` image running the xai-proxy with the worktree's `embry0/` mounted over it, and an echo upstream that records which headers and paths the proxy forwards. Criterion 1 (token injected, sandbox bearer stripped, sandbox never holds the upstream token) is proven offline against the echo. Criteria 2 to 6 need one live SuperGrok session token pushed to the local proxy; the captured NDJSON stream is then parsed offline through the SDK's message parser and a copy of the Claude path's `consume()`. The red path (always-applicable conformance work) adds `turn_usage` per turn to `DirectXaiExecutor` and prices its cost like the SDK path.

**Tech Stack:** Python 3.12 (shared venv `/Users/tomas/repos/embry0-worktrees/trace-fidelity/.venv`, `PYTHONPATH=<worktree>`), Docker (local daemon, arm64 images), Grok Build 1.0.x Linux aarch64 binary from `https://x.ai/cli/`, `claude_agent_sdk` 0.2.164 (`_internal.message_parser.parse_message`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-15-trace-fidelity-design.md`, section 4 (the spike), sections 3.1 to 3.3 (the events an executor must emit), 5 (executor tests), 7 (issue table). Research dossier (not committed): scratchpad `research-plan-d.md`, discrepancies D1 to D17.

## Global Constraints

- Nothing from the spike enters the tree except `docs/spikes/2026-10-<DD>-grok-build.md` (and, on green, an ADR draft). Harness, images, captures and logs stay under the scratchpad `grok-spike/`. Captures may contain the session token in headers: they are never copied into the repo, never pasted into a report, and the scratchpad directory is removed when the spike closes.
- No token is echoed, logged or committed. The local GB session token is read from `~/.grok/auth.json` only by a pipe into `docker exec -i` (never on a command line, never into a file in the tree), and only after Tomas has refreshed it himself (Open decision 3). No programmatic refresh: embry0's refresher would rotate the refresh-token lineage and leave the local GB with a dead one.
- The local `~/.grok` (Tomas's own GB install, config, sessions) is read-only for this plan; the spike uses its own `GROK_HOME` inside the containers.
- Egress: the scratch sandbox network is `--internal`; the proxy alone gets a second network with egress. GB telemetry, auto-update, memory sync and web search are off in the scratch image (`GROK_DISABLE_AUTOUPDATER=1`, `DISABLE_TELEMETRY=1`, `GROK_TELEMETRY_ENABLED=false`, `GROK_MEMORY=0`, `--disable-web-search`, `--no-subagents`); every egress attempt the echo or the proxy sees that is not a model call is recorded in the findings.
- Public repo (CLAUDE.md): the findings document names no customer, deployment, person, host, cost or job id; cost appears as relative or as token counts only; the name-denylist CI check must pass.
- No new Python dependency. No change to `docs/contracts/events.json`, `embry0/execution/events.py` or `EVENT_FIELDS`.
- Backend gates after every repo task: `ruff check`, `ruff format --check`, `mypy embry0` clean; the touched test files green; full unit suite against a throwaway Postgres before the PR, failure set diffed against `origin/main`.
- Every new test is shown failing first (RED) with its own assertion message on the pre-fix tree, then passing.
- Commits: Conventional Commits, subject at most 72 characters, no AI co-author footer, last line `Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ`.

## Decisions

Where the spec, issues #42/#43 and the tree at cb42471c disagree, these rulings apply. Dossier discrepancy numbers in brackets. Spec sentences they amend are listed in Task S5.

1. **The spike answers a narrower question than the spec poses** [D1]. Spec 4 and #42 say Grok Build would let "both providers share one stream parser". They already do: grok runs by default on `SdkAgentExecutor` (the Claude CLI pointed at the xai-proxy, `embry0/agents/executor_factory.py:33-42`, `docs/providers.md:173-187`); `DirectXaiExecutor` runs only with `XAI_DIRECT_EXECUTOR=true`, kept as insurance against a Claude CLI release breaking on xAI's compat endpoint. The spike's question becomes: *can Grok Build run as a sandbox executor through the xai-proxy under embry0's safety rings, well enough to replace the opt-in fallback, or the default runtime, or both?* The six criteria stand as the evidence; which runtime it would replace is Open decision 1 and is not needed to run the spike.
2. **Two credential routes are tested, not one** [D3]. The spec names `GROK_CLI_CHAT_PROXY_BASE_URL`, which is GB's *session-service* base (default `cli-chat-proxy.grok.com`), not the model API base; the xai-proxy forwards only `/v1/{path}` to `api.x.ai`. Route A is the spec's (auth-provider command printing the sandbox bearer + session-service URL = proxy). Route B is a custom model entry in `$GROK_HOME/config.toml` with `api_backend = "messages"`, `base_url` = the proxy and `env_key` naming an env var that holds the sandbox bearer, which is the only protocol proven to work through the proxy with a SuperGrok token (`/v1/messages`). The offline echo run (S2) shows which paths each route calls before any live run.
3. **Criterion 5 is restated** [D2]. `--plugin-dir` exists only on `grok agent` (ACP server), not on headless `grok -p`. Criterion 5 becomes: *the `system/init` line's `skills` lists a user-invocable skill from a plugin loaded through `[plugins] paths` + `[plugins] enabled` in `$GROK_HOME/config.toml` (or `$GROK_HOME/plugins/`)*.
4. **Criterion 4 is a fresh-container resume** [D6]. GB sessions are a directory `$GROK_HOME/sessions/<encoded-cwd>/<id>/` (many files), while embry0's copy-out path moves one file. The criterion becomes: `--resume <id>` continues the conversation in a *fresh* container after copying (a) the whole session directory and, separately, (b) `updates.jsonl` alone; the findings say which suffices.
5. **Criterion 6 names the model and the fields** [D7]. Under a SuperGrok session token GB reports `total_cost_usd: 0` ("unknown, not free"). The run pins `-m grok-4.5` (priced in `embry0/agents/providers.py`); green means every `assistant` line carries `message.usage` with `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, and `price_usage("grok-4.5", ...)` yields a number for each, plus `result.usage`/`modelUsage` present.
6. **Criterion 3 reads the stream, not the hook** [D4, D5]. The `--deny` rule and the hook deny must each appear in `stream.ndjson` as a `user` line with a `tool_result` whose `is_error` is true (or a documented equivalent GB emits), and the denied command must not have run (the `rm` target still exists; the echo never saw a `curl`). The hook is written against GB's own tool names and input keys (`run_terminal_command{command}`), because a hook that matches Claude names measures nothing under GB.
7. **Ring 3 under GB is a security finding, not a spike criterion** [D4]. Ring 3 is 41 regexes applied in-process and fail-closed on the Claude path; GB `--deny` rules are globs and GB hooks fail open on crash or timeout. The findings document states what a GB swap would do to Ring 3 and proposes the mitigation (an uncrashable hook that maps GB names to Claude names and calls `gate_tool_call`, plus a coarse `--deny` floor and `--sandbox`). Accepting a fail-open layer is Open decision 2 and gates #43, not the spike.
8. **Version and checksum are the spike's to pin** [D10]. The spec's "1.0.50" is the local install; stable is 1.0.62 on 2026-10-16. S1 downloads the version `https://x.ai/cli/stable` reports on the day, records its sha256 (none is published), and the findings carry version and hash; a later #43 pins those.
9. **The red path is worth doing in every outcome except "green and the direct path is deleted"** [Section 6 of the dossier]. `DirectXaiExecutor` emits no `turn_usage` (spec table 3.1 says executors emit one per assistant message) and prices cost without cache tokens while `price_usage` prices them. R1 fixes both. R1 is dispatched only after the spike verdict and Open decision 1, so it is not thrown away.
10. **Findings go to `docs/spikes/`** [Section 5]. The two existing spike documents live there with a verdict at the top. An ADR (next free number 0010) is written only if a decision lands (green plus Open decisions 1 and 2 taken); it would amend ADR 0001's Ring-3 hook assumption. The plan ships in the same PR as the findings (red) or the #43 code (green), never alone.
11. **`GROK_` becomes a reserved env prefix regardless of outcome** [D13]. GB reads `GROK_*` variables that redirect traffic or load code (`GROK_AUTH_PROVIDER_COMMAND`, `GROK_CLI_CHAT_PROXY_BASE_URL`, `GROK_XAI_API_BASE_URL`, `GROK_HOME`, `GROK_CONFIG`, `GROK_MANAGED_CONFIG_URL`, ...). A sandbox that ever carries the binary must refuse them from repo env vars, and the binary's presence is the spike's own hypothesis. Task R2 adds `GROK_` to `HOOK_ENV_PREFIXES` in `embry0/execution/auth_provider.py` with a test; it is cheap, defensive and independent of the verdict.
12. **`embry0/execution/auth_provider.py` is the reserved-env registry, not an auth-provider command** [D13]; the dossier's brief assumed otherwise. No auth-provider command exists in the tree; route A's command is a two-line shell script in the scratch image (`cat $EMBRY0_XAI_TOKEN_FILE`).
13. **No recorded input-stream fixture exists yet** [D14]. `tests/fixtures/job-events/` are plan C's synthesized output-side events. The spike's `stream.ndjson` is the first candidate input-stream fixture; it is scrubbed (session id, cwd, hostnames, any token) before it could ever enter the tree in plan E, and it does not enter the tree in this plan.
14. **Compat sources are switched off in the spike** [D12]. GB also loads `~/.claude/settings.json` hooks, project `.claude/`, `.cursor/`, `.mcp.json` and `.agents/skills`. The scratch `GROK_HOME/config.toml` turns the `[compat.*]` switches off and the run does not pass `--trust`; what GB still loads is recorded. Whether a GB executor would ever pass `--trust` is a plan-E question.

## Open decisions for Tomas

The plan runs the spike (S1 to S5) and the reserved-prefix task (R2) without these. R1 and anything after need them.

- **OD1. Which runtime would Grok Build replace?** (a) the opt-in `DirectXaiExecutor` only (smallest change, keeps the SDK path as grok's default and keeps the Claude CLI as insurance); (b) the default grok runtime too (grok stops running the Claude CLI; QA MCP, resume and Ring 3 all move to GB); (c) both, deleting the direct path and the SDK-over-proxy overlay. The spike's findings are written so each option can be judged.
- **OD2. Is a fail-open Ring-3 layer acceptable on the grok path?** Today's Ring 3 is in-process and fail-closed. Under GB the hard boundary is `--deny` globs plus `--tools` plus `--sandbox`; the regex policy runs in a hook that GB lets through on crash or timeout. If no: #43 is red regardless of the six criteria, and the direct path stays (R1 runs). If yes: ADR 0010 records it.
- **OD3. Refresh the local Grok Build session once.** Criteria 2 to 6 need a live SuperGrok session token pushed to the local proxy. `~/.grok/auth.json` holds one that expired on 2026-10-09. Tomas runs the local `grok` interactively once (any prompt, then quit) so the file holds a fresh token; S3 then pipes it into the proxy. Alternative: an `XAI_API_KEY` from the console, which spends real money and changes what criterion 1 proves (API-key traffic goes to a different base URL inside GB). If neither is wanted, the spike ends after S2 with criteria 2 to 6 "not run" and the findings say so.

## Review Focus

Inputs the spec implies but names no test for; each line's check is added to the owning task.

1. **A hook that measures nothing.** A GB `read_file` with `target_file: /etc/passwd` must be denied by the spike hook; a hook written for Claude's `Read{file_path}` lets it through. S1 includes a hook self-test that feeds that exact stdin and asserts the deny JSON (offline, no GB needed).
2. **The sandbox never held the upstream token.** Beyond "the echo saw the injected bearer": `docker exec <sandbox> sh -c 'cat /proc/*/environ 2>/dev/null | tr "\0" "\n"' | grep -c <first 8 chars of the dummy token>` must be 0, and the bearer file must hold only the sandbox token. S2.
3. **A deny that ran anyway.** After the live run, the `rm` target directory must still exist in the workspace and the echo/proxy logs must show no `curl`-shaped egress from the sandbox. S3.
4. **Resume that silently starts a new session.** A `--resume` that falls back to a fresh session looks like success. S3 asserts the resumed `system/init` carries the *same* `session_id` and that the answer references the file read in the first run.
5. **Unpriced side-model calls.** GB uses a secondary model for titles and compaction; S3 records every distinct `model` seen in the proxy's forwarded requests (echo path count per model), so the findings say what the usage figures leave out.

## File structure

New in the tree:
- `docs/spikes/2026-10-<DD>-grok-build.md` (S5): verdict table, routes, version + sha256, egress record, Ring-3 finding, OD1/OD2 inputs.
- `docs/adr/0010-grok-build-executor.md` only on green with OD1 and OD2 taken (S5, otherwise skipped).
- `tests/unit/execution/test_auth_provider_reserved_grok.py` (R2) or a test added to the existing reserved-env test module (R2 picks whichever file holds the `HOOK_ENV_PREFIXES` tests).

Modified in the tree:
- `embry0/execution/auth_provider.py` (R2: `GROK_` in `HOOK_ENV_PREFIXES`).
- `embry0/agents/executor_xai.py`, `tests/unit/agents/test_executor_xai.py`, `tests/unit/agents/test_providers.py`, `tests/unit/agents/test_unpriced_models.py` (R1).
- `docs/superpowers/specs/2026-10-15-trace-fidelity-design.md` section 4 (S5 amendments).
- GitHub issues #42 and #43 bodies (S5: corrected flags, restated criteria, OD1/OD2 pointers).

Scratchpad only (`<scratchpad>/grok-spike/`, never committed): `Dockerfile`, `echo_upstream.py`, `proxy_up.sh`, `enroll.sh`, `push_token.sh`, `grok-home-A/`, `grok-home-B/` (`config.toml`, `hooks/spike.json`, `hooks/deny_curl.py`, `plugins/spike-plugin/`), `run_live.sh`, `resume_fresh.sh`, `parse_capture.py`, `captures/`.

## Execution waves

| Wave | Tasks | Needs |
|---|---|---|
| 1 | S1 harness (scratchpad), R2 reserved prefix (worktree `tfd-r2`) | binary download; no key |
| 2 | S2 offline header proof + which paths each route calls | Docker; no key |
| 3 | S3 live runs (criteria 2 to 6 raw data) | OD3: a fresh local GB session token |
| 4 | S4 offline parse of the capture (criterion 2) | none |
| 5 | S5 findings, spec amendments, issue bodies, PR | OD1/OD2 for the ADR only |
| 6 | R1 direct-path conformance | verdict + OD1 (skipped only if green and the direct path is deleted) |

S1 to S4 are run by the controller with one Opus agent per wave in the scratchpad (no worktree). R1 and R2 are ordinary worktree tasks. On red (or OD2 = no), the PR is `docs(spikes): Grok Build as the grok executor, findings` plus R1/R2, `Fixes: #42`, and #43 is closed with a comment pointing at the findings. On green with OD1/OD2 taken, the PR carries the findings, R2 and the ADR, `Fixes: #42`, and plan E (#43) is written next.

Collision table: `embry0/execution/auth_provider.py` is R2 only; `executor_xai.py` and its three test files are R1 only; the spec and the issues are S5 only. No two tasks share a file.

---

### Task S1: Spike harness

**Files (scratchpad `grok-spike/`):** `Dockerfile`, `echo_upstream.py`, `proxy_up.sh`, `enroll.sh`, `push_token.sh`, `grok-home-A/config.toml`, `grok-home-B/config.toml`, `grok-home-*/hooks/spike.json`, `grok-home-*/hooks/deny_curl.py`, `grok-home-*/plugins/spike-plugin/.grok-plugin/plugin.json` (or the manifest name GB's user guide `09-plugins.md` prescribes) and `plugins/spike-plugin/skills/spike-skill/SKILL.md`, `auth_provider.sh`, `hook_selftest.sh`.

**Interfaces:**
- Consumes: local images `embry0-sandbox:trace-fidelity-b` (arm64) and `embry0-proxy:latest`; GB user guide at `/Users/tomas/.grok/docs/user-guide/` (read-only reference for flag and config names: `02-authentication.md`, `05-configuration.md`, `07-mcp.md`, `09-plugins.md`, `10-hooks.md`, `11-models.md`, `14-headless-mode.md`, `17-sessions.md`, `22-permissions.md`, `26-reference.md`).
- Produces: image `grok-spike:<version>`; scripts S2/S3 call; `VERSION` and `SHA256` files.

- [ ] **Step 1: Pin and fetch the binary**

```bash
cd "$SCRATCH/grok-spike"
VER=$(curl -fsSL https://x.ai/cli/stable); echo "$VER" > VERSION
curl -fsSL "https://x.ai/cli/grok-${VER}-linux-aarch64" -o grok-linux-aarch64
shasum -a 256 grok-linux-aarch64 | tee SHA256
```
Expected: a ~190 MB file and a 64-hex sha256. If the URL 404s for the stable version, fall back to `1.0.50` and record why.

- [ ] **Step 2: Scratch image**

```dockerfile
FROM embry0-sandbox:trace-fidelity-b
USER root
COPY grok-linux-aarch64 /usr/local/bin/grok
RUN chmod 0755 /usr/local/bin/grok && mkdir -p /home/agent/.grok /home/agent/.embry0 && chown -R agent:agent /home/agent/.grok /home/agent/.embry0
ENV GROK_DISABLE_AUTOUPDATER=1 DISABLE_TELEMETRY=1 GROK_TELEMETRY_ENABLED=false GROK_MEMORY=0 GROK_HOME=/home/agent/.grok
COPY --chown=agent:agent auth_provider.sh /usr/local/bin/embry0-grok-auth
RUN chmod 0755 /usr/local/bin/embry0-grok-auth
USER agent
WORKDIR /workspace
RUN grok --no-auto-update --version
```
`auth_provider.sh`:
```sh
#!/bin/sh
# Route A auth provider: print the per-sandbox proxy bearer. Exit 1 fast when
# the file is missing so GB does not fall into its 300 s interactive sign-in.
f="${EMBRY0_XAI_TOKEN_FILE:-/home/agent/.embry0/xai_proxy_token}"
[ -r "$f" ] || exit 1
cat "$f"
```
Build: `docker build -t grok-spike:$VER .`. Expected: the last `RUN` prints the version (glibc compatibility of the binary on bookworm is one of the unverified items; a failure here is a finding, not a blocker: record it and try the `.gz` artifact or the x86_64 build under emulation).

- [ ] **Step 3: Echo upstream**

`echo_upstream.py` (stdlib only, runs inside a `python:3.12-slim` container): an HTTP server on 8080 that, for every request, appends one JSON line to `/log/echo.jsonl` with `method`, `path`, `sha256(Authorization header)[:12]` (never the header), the presence of `x-api-key`, `x-grok-conv-id`, the request body's `model` field if the body is JSON and has one, and returns `200 {"type":"message","id":"msg_echo","role":"assistant","model":"grok-4.5","content":[{"type":"text","text":"echo"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}` for `/v1/messages` and `404 {}` for anything else. The 404s are the point: they show which paths a route tries.

- [ ] **Step 4: Networks and the proxy**

`proxy_up.sh MODE` where `MODE` is `echo` or `live`:
```bash
set -e
docker network create --internal grok-spike-restricted 2>/dev/null || true
docker network create grok-spike-egress 2>/dev/null || true
export PROXY_ADMIN_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))'); echo -n "$PROXY_ADMIN_TOKEN" > admin.token; chmod 600 admin.token
if [ "$1" = echo ]; then
  docker run -d --rm --name grok-echo --network grok-spike-restricted -v "$PWD/captures:/log" -v "$PWD/echo_upstream.py:/echo.py:ro" python:3.12-slim python /echo.py
  UPSTREAM='m.XAI_API_BASE="http://grok-echo:8080"; '
else
  UPSTREAM=''
fi
docker run -d --rm --name grok-xai-proxy --network grok-spike-restricted -e PROXY_TYPE=xai -e LISTEN_PORT=9106 -e PROXY_ADMIN_TOKEN \
  -v "$WT/embry0:/app/embry0:ro" embry0-proxy:latest \
  python -c "import embry0.execution.proxy.xai_proxy as m; ${UPSTREAM}from embry0.execution.proxy.server import main; main()"
[ "$1" = live ] && docker network connect grok-spike-egress grok-xai-proxy
```
Check `embry0/execution/proxy/server.py` for the real entrypoint and env names before relying on this (the dossier: `PROXY_TYPE`, `LISTEN_PORT`, port 9106; upstream constant `XAI_API_BASE` in `xai_proxy.py:34`, read at call time at `:237`; precedent `tests/integration/test_proxy_enrollment_e2e.py:21-55`). Expected: `docker logs grok-xai-proxy` shows the server listening; `GET /admin/health` with `X-Admin-Token` answers.

- [ ] **Step 5: Enroll and push**

`enroll.sh SANDBOX_ID` generates a sandbox bearer (`secrets.token_urlsafe(32)`), POSTs `/admin/enroll {"sandbox_id":..., "sandbox_token":...}` from inside the proxy container (`docker exec grok-xai-proxy python -c '<urllib POST>'`, body on stdin), and writes the bearer to `sandbox.token` (0600). `push_token.sh` reads a token from **stdin** and POSTs `/admin/token {"access_token": ...}` the same way (`docker exec -i`), so the token is never an argument. Body shapes: `embry0/execution/proxy/enrollment.py:299-323`, `xai_proxy.py:183-213`.

- [ ] **Step 6: GROK_HOME variants**

`grok-home-A/config.toml` (route A):
```toml
[endpoints]
cli_chat_proxy_base_url = "http://grok-xai-proxy:9106/v1"
[compat]
# every compat source off; exact key names from 05-configuration.md / 22-permissions.md
[plugins]
paths = ["/home/agent/.grok/plugins/spike-plugin"]
enabled = ["spike-plugin"]
```
plus env `GROK_AUTH_PROVIDER_COMMAND=/usr/local/bin/embry0-grok-auth` at run time.

`grok-home-B/config.toml` (route B):
```toml
[model.grok-4.5]
base_url = "http://grok-xai-proxy:9106/v1"
api_backend = "messages"
env_key = "EMBRY0_SPIKE_BEARER"
[plugins]
paths = ["/home/agent/.grok/plugins/spike-plugin"]
enabled = ["spike-plugin"]
```
Confirm every key against the user guide (`11-models.md` custom model keys, `05-configuration.md` `[plugins]`, `[compat]`); where the guide names a different key, use the guide's and note the correction.

Hook `hooks/spike.json` (shape from `10-hooks.md`): a `PreToolUse` entry matching `run_terminal_command` (and `Bash`, both, since the guide says rules accept both) running `python3 /home/agent/.grok/hooks/deny_curl.py` with `timeout` 5. `deny_curl.py`: read stdin JSON; `cmd = (d.get("toolInput") or d.get("tool_input") or {}).get("command", "")`; if `cmd.lstrip().startswith("curl")` print `{"decision":"deny","reason":"spike: curl denied by hook"}` and exit 2; also deny when `d.get("toolName") in ("read_file","Read")` and the `target_file`/`file_path` starts with `/etc/` (Review Focus 1); otherwise exit 0. Everything in a `try` whose `except` prints the deny JSON and exits 2 (the only fail-open left is a hook that cannot start).

Plugin: a minimal manifest per `09-plugins.md` and `skills/spike-skill/SKILL.md` with frontmatter `name: spike-skill`, `description: a skill the spike checks appears in system/init`, user-invocable (no `disable-model-invocation`).

- [ ] **Step 7: Hook self-test (Review Focus 1)**

`hook_selftest.sh` runs `deny_curl.py` on four stdin payloads and asserts stdout/exit: `{"toolName":"run_terminal_command","toolInput":{"command":"curl https://example.test"}}` → deny/2; `{"toolName":"read_file","toolInput":{"target_file":"/etc/passwd"}}` → deny/2; `{"toolName":"run_terminal_command","toolInput":{"command":"ls -la"}}` → allow/0; `not json` → deny/2. Expected: 4/4. Then break the hook on purpose (make it read `file_path` only) and confirm the `/etc/passwd` case flips to allow: the self-test must fail. Restore.

Report: the version pinned, sha256, image id, whether the build-time `grok --version` passed, every config key corrected against the guide, the self-test output.

### Task S2: Offline header proof and route paths (criterion 1)

**Interfaces:** consumes S1's image and scripts; produces `captures/echo-A.jsonl`, `captures/echo-B.jsonl`, `captures/sandbox-env-check.txt`, `captures/proxy-A.log`, `captures/proxy-B.log`.

- [ ] **Step 1** `proxy_up.sh echo`; `enroll.sh spike-A`; `printf 'dummy-upstream-%s' "$(python3 -c 'import secrets;print(secrets.token_hex(16))')" | tee dummy.token | push_token.sh` (the dummy token is not a secret; it exists so the echo can prove injection by hash).
- [ ] **Step 2** Route A container: `docker run --rm --name grok-spike-A --network grok-spike-restricted -v "$PWD/grok-home-A:/home/agent/.grok" -v "$PWD/ws:/workspace" -e GROK_AUTH_PROVIDER_COMMAND=/usr/local/bin/embry0-grok-auth grok-spike:$VER sh -c 'install -m 0600 /dev/stdin /home/agent/.embry0/xai_proxy_token && grok -p "say hi" -m grok-4.5 --output-format streaming-messages-json --permission-mode bypassPermissions --no-auto-update --disable-web-search --no-subagents --max-turns 1' < sandbox.token > captures/stream-echo-A.ndjson 2> captures/stderr-echo-A.log || true`. The echo answers only `/v1/messages`, so GB will fail or produce one echo turn; both are fine. Record in `echo-A.jsonl` which paths were requested and whether the `Authorization` hash equals `sha256("Bearer " + dummy)[:12]` and never `sha256("Bearer " + sandbox)[:12]`.
- [ ] **Step 3** Route B container: same, with `grok-home-B`, `-e EMBRY0_SPIKE_BEARER="$(cat sandbox.token)"` (route B puts the bearer in env by design; note it in the findings as a difference from the tree's 0600-file convention) and no auth-provider env.
- [ ] **Step 4** Review Focus 2: while a container is running a long `sleep` variant, `docker exec grok-spike-B sh -c 'cat /proc/*/environ 2>/dev/null | tr "\0" "\n"' | grep -c "$(cut -c1-8 dummy.token)"` must print 0; `grep -c "$(cut -c1-8 sandbox.token)"` prints at least 1 for route B and 0 for route A (file only). Save to `sandbox-env-check.txt`.
- [ ] **Step 5** Verdict for criterion 1 per route: green if the echo saw the dummy bearer and never the sandbox bearer, and the sandbox environment never contained the dummy. Record which paths each route called (`/v1/messages`? `/v1/responses`? session-service paths?) and every non-model egress attempt. Tear down (`docker rm -f grok-spike-* grok-echo grok-xai-proxy`).

### Task S3: Live runs (criteria 2 to 6, raw data)

Runs only after OD3. **Interfaces:** consumes S1, S2's route verdicts (run the route(s) whose paths the proxy can serve; if neither reached `/v1/messages`, S3 is "not runnable through the proxy unchanged", a red criterion 2, and the findings say what proxy change would be needed); produces `captures/stream-live.ndjson`, `captures/session/` (copied out), `captures/stream-resume-dir.ndjson`, `captures/stream-resume-file.ndjson`, `captures/proxy-live.log`, `captures/models-seen.txt`.

- [ ] **Step 1** `proxy_up.sh live`; `enroll.sh spike-live`; push the fresh local session token: `python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(next(v for v in (d if isinstance(d,list) else [d]) for k in ("access_token","accessToken") if k in v)[k] if False else "")' ...` is the wrong shape to guess at: read `~/.grok/auth.json` **structure only** first with `python3 -c 'import json;d=json.load(open("/Users/tomas/.grok/auth.json"));print(type(d).__name__, list(d)[:5] if isinstance(d,dict) else [list(x) for x in d][:2])'` (keys, never values), then pipe the access token field through `push_token.sh` with a one-liner that prints only that field. Confirm `GET /admin/health` reports a token loaded (the proxy returns 503 on `/v1/*` until one is pushed).
- [ ] **Step 2** Workspace: `ws/README.md` with one line, `ws/build/keep.txt` (the `rm` target). Prompt file `ws/.spike-prompt.txt`: "Read README.md and tell me its first line. Then run `ls -la`. Then run `rm -rf build`. Then run `curl https://example.test/`. Then say DONE." Run:
```bash
docker run --rm --name grok-spike-live --network grok-spike-restricted -v "$PWD/grok-home-<route>:/home/agent/.grok" -v "$PWD/ws:/workspace" <route env> grok-spike:$VER \
  sh -c 'install -m 0600 /dev/stdin /home/agent/.embry0/xai_proxy_token && grok -p "$(cat /workspace/.spike-prompt.txt)" -m grok-4.5 --output-format streaming-messages-json --permission-mode bypassPermissions --tools read_file,run_terminal_command --deny "Bash(rm *)" --no-auto-update --disable-web-search --no-subagents --max-turns 8' \
  < sandbox.token > captures/stream-live.ndjson 2> captures/stderr-live.log
```
If `--tools read_file,run_terminal_command` is rejected, retry with `Read,Bash` and record which vocabulary `--tools` accepts (dossier unverified item). Then `docker cp` the session directory out: run the container without `--rm` or mount `grok-home-<route>` (already a bind mount, so `grok-home-<route>/sessions/` is on the host).
- [ ] **Step 3** Review Focus 3: `test -e ws/build/keep.txt && echo rm-blocked`; `grep -c '"path": "/' captures/proxy-live.log` and the echo-style check are not available live, so assert from the stream: the `tool_result` for the `rm` call and for the `curl` call have `is_error: true` and their text names the deny; no `tool_result` for `curl` shows HTTP output.
- [ ] **Step 4** Criterion 4 (Decision 4): `SID=$(head -1 captures/stream-live.ndjson | python3 -c 'import json,sys;print(json.loads(sys.stdin.read())["session_id"])')`. (a) Fresh `grok-home-resume-dir/` = `config.toml` + the whole `sessions/<enc>/<SID>/` directory; run `grok -p "Which file did you read, and what was its first line?" --resume $SID ...` in a new container → `captures/stream-resume-dir.ndjson`. (b) Fresh `grok-home-resume-file/` with only `sessions/<enc>/<SID>/updates.jsonl` → `captures/stream-resume-file.ndjson`. Review Focus 4: both resumed `system/init` lines must carry `session_id == SID`, and the answer must name `README.md` and its first line.
- [ ] **Step 5** Criterion 5 (Decision 3): the first line of `stream-live.ndjson` is `system/init`; `skills` must contain `spike-skill`. If it does not, try the `$GROK_HOME/plugins/` auto-trusted location and record which worked.
- [ ] **Step 6** Criterion 6 (Decision 5): list every `assistant` line's `message.usage` keys and values; `result` line's `total_cost_usd`, `usage`, `modelUsage`, `num_turns`, `duration_ms`. Review Focus 5: from `proxy-live.log` (the proxy logs `xai_credentials_used sandbox_id=` per request, not the model), count requests; from the stream count assistant messages; the difference is side-model traffic the usage leaves out.
- [ ] **Step 7** Tear down; push nothing else; the live token stays only in the proxy container's memory, which is removed.

### Task S4: Offline parse of the capture (criterion 2)

**Interfaces:** consumes `captures/stream-live.ndjson`; produces `captures/parsed-events.jsonl` and `captures/parse-report.md`. Runs on the host venv with `PYTHONPATH=<worktree>`, no network, no Docker.

- [ ] **Step 1** `parse_capture.py`: for each NDJSON line, `claude_agent_sdk._internal.message_parser.parse_message(line_dict)` inside a `try`; count `MessageParseError`s by cause (expected: missing `thinking.signature`, missing `result` keys such as `duration_api_ms`, `permission_denials`). For each failure, apply the minimal default (`signature: ""`, `duration_api_ms: 0`, `permission_denials: []`) and re-parse; record every default needed.
- [ ] **Step 2** Tool-name mapping before parsing: `run_terminal_command`→`Bash` (`command` kept), `read_file{target_file}`→`Read{file_path}`, `search_replace`→`Edit`, `write`→`Write`, `list_dir{target_directory}`→`Glob{path}`, `grep`→`Grep`, `spawn_subagent`→`Task`, `use_tool{tool_name:"s__t"}`→`mcp__s__t` with `tool_input` unwrapped. Record which GB tool names the capture actually contains.
- [ ] **Step 3** Feed the parsed messages to a copy of `consume()` lifted verbatim from `embry0/agents/executor.py:676-890` into the script with the `nonlocal`s turned into a small state object and `writer` = append to a list (this is the "thin adapter" the spec asks about, measured honestly: count the lines the copy needed changed). Assert the emitted list contains `tool_call` (with `tool_id`, `tool_name`, `summary`, `tool_input`), `tool_result` paired by `tool_id`, `turn_usage` per assistant line with `cost_usd` from `price_usage("grok-4.5", ...)`, and one `cost_update`; validate every event against `docs/contracts/events.json` (required keys present, no unknown keys) using the same checks `tests/unit/execution/test_job_event_fixtures.py` applies to fixtures. Expected: green = all four types present and contract-valid; otherwise the report names the first failing event.
- [ ] **Step 4** Scrub a copy of the capture for a possible plan-E fixture: replace the session id, the cwd, any hostname and any `Authorization`-like string; run `python scripts/check_denylist.py` against it if the denylist file is available (it is a CI secret, so likely "not runnable locally"; say so). Keep the scrubbed copy in the scratchpad only.

### Task S5: Findings document, spec amendments, issue bodies, pull request

- [ ] **Step 1: Findings** `docs/spikes/2026-10-<DD>-grok-build.md`, in the format of `docs/spikes/2026-07-23-emb54-xai-native-sdk.md`: a bold verdict line (GREEN on all six / RED on criteria N, M), then a table with one row per criterion (restated per Decisions 3 to 6), its evidence (commands, counts, hashes, line numbers in the capture; no tokens, no hosts, no cost), then sections: version and sha256 pinned; route A vs route B results and which paths each called; what the SDK parser needed changed (S4 line count and defaults); the Ring-3 finding (Decision 7) with the proposed mitigation; egress observed; what the usage figures leave out; inputs for OD1 and OD2; what plan E would contain if green. Relative cost or token counts only.
- [ ] **Step 2: Spec amendments** (`docs/superpowers/specs/2026-10-15-trace-fidelity-design.md`, section 4):
  - "Can Grok Build ... replace `DirectXaiExecutor`, so that both providers share one stream parser": append "(they already share it on the default SDK-over-proxy path; the spike asks whether Grok Build can be the grok executor at all, replacing the opt-in fallback, the default runtime, or both)".
  - "`GROK_CLI_CHAT_PROXY_BASE_URL` overrides the endpoint": replace with "`GROK_CLI_CHAT_PROXY_BASE_URL` is the session-service base; API-key traffic uses `GROK_XAI_API_BASE_URL`, and a custom model entry with `api_backend = \"messages\"` and `base_url` pointed at the proxy is the route proven to speak the protocol the proxy forwards".
  - "`--plugin-dir` on `grok agent`" and the criterion "lists skills from a `--plugin-dir`": replace with the `[plugins] paths`/`enabled` form (Decision 3).
  - "`updates.jsonl` under `$GROK_HOME/sessions/`, `--resume <id>`, so the existing copy-out-and-resume path applies": replace with "a session is the directory `$GROK_HOME/sessions/<encoded-cwd>/<id>/`; the copy-out path moves one file today, so resume needs a directory (or `updates.jsonl` alone, if the spike shows it suffices)".
  - "the hard boundary is `--deny` plus `--tools`; the hook is the policy's advisory layer": append "; Ring 3 is 41 regexes that `--deny` globs cannot express, so under Grok Build the regex policy runs in a hook that fails open, a change recorded as a decision (ADR 0010) before any swap".
  - Acceptance criterion on cost: replace with Decision 5's wording.
  - "1.0.50 as of 2026-10-15": append "; stable was 1.0.62 on 2026-10-16; the spike pins the version and sha256 it ran".
- [ ] **Step 3: Issue bodies.** `gh issue edit 42` so the body states the restated criteria and the two routes; `gh issue edit 43` so it says "conditional on #42 green and on two decisions: which runtime Grok Build replaces, and acceptance of a fail-open Ring-3 hook layer (ADR 0010)". On red, close #43 with a comment pointing at the findings.
- [ ] **Step 4: Verification before the PR.** `ruff check . && ruff format --check . && mypy embry0`; full unit suite against a throwaway Postgres (CLAUDE.md recipe), failure set diffed against `origin/main`; `grep -rn '<<<<<<<\|>>>>>>>'` clean; `gitleaks detect --config .gitleaks.toml --source . --log-opts=origin/main..HEAD` clean; `grep -rniE 'bearer [a-z0-9_-]{20,}|access_token' docs/spikes/2026-10-*-grok-build.md` empty.
- [ ] **Step 5: Pull request.** Branch `feat/trace-fidelity-d` against `main`. Red: title `docs(spikes): Grok Build as the grok executor, red on <criteria>` (or `green on all six` if OD1/OD2 are pending), body per the repository template (Summary, Motivation, How it works = what was run and how each criterion was judged, Verification with the gate lines, Deploy notes: "No deployable change beyond `GROK_` joining the reserved env prefixes (orchestrator and sandbox images rebuilt by `infra/deploy.sh`; the reserved-env check runs at the API and at sandbox injection, so the sandbox image rebuild recipe in `docs/ops/deploy.md` applies)" plus R1's note if R1 landed ("sandbox image rebuild: `embry0/agents/executor_xai.py` is baked in"), Out of scope: plan E / #43), `Fixes: #42`, last line `https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ`. The plan, the findings and the spec amendments ship in it.

### Task R1: `DirectXaiExecutor` emits `turn_usage` and prices cache tokens

Dispatched after the verdict and OD1 (Decision 9). **Files:** Modify `embry0/agents/executor_xai.py`, `tests/unit/agents/test_executor_xai.py`, `tests/unit/agents/test_providers.py:41-44`, `tests/unit/agents/test_unpriced_models.py:36-40`.

**Interfaces:** consumes `events.turn_usage(*, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, cost_usd, model, run_id, node, agent, parent_tool_use_id=None)` and `price_usage(model, *, input_tokens, output_tokens, cache_read_tokens=0, cache_creation_tokens=0) -> float | None` from `embry0/agents/providers.py:105`.

- [ ] **Step 1: RED test, one `turn_usage` per turn, priced**

Add to `tests/unit/agents/test_executor_xai.py` after `test_cost_update_num_turns_counts_turns_not_distinct_tools`:

```python
def test_each_turn_emits_turn_usage_priced_with_cache_tokens(env, monkeypatch):
    # Spec 3.1: executors emit turn_usage per assistant message, priced by the
    # table including cache reads and writes (the SDK path already does).
    (env / "a.txt").write_text("a\n")
    responses = [
        _Resp([_Block("tool_use", id="t1", name="Read", input={"file_path": "a.txt"})], "tool_use", _Usage(10, 2, 1000, 100)),
        _Resp([_Block("text", text="done")], "end_turn", _Usage(5, 2)),
    ]
    _install(monkeypatch, responses)
    events = []
    _run(_invocation(), events)
    usages = [e for e in events if e["type"] == "turn_usage"]
    assert len(usages) == 2, f"expected one turn_usage per turn, got {[e['type'] for e in events]}"
    first = usages[0]
    assert (first["input_tokens"], first["output_tokens"], first["cache_read_tokens"], first["cache_creation_tokens"]) == (10, 2, 1000, 100)
    assert first["model"] == "grok-4.5" and first["run_id"] and first["node"] and first["agent"]
    from embry0.agents.providers import price_usage

    assert first["cost_usd"] == pytest.approx(
        price_usage("grok-4.5", input_tokens=10, output_tokens=2, cache_read_tokens=1000, cache_creation_tokens=100)
    )
    # turn_usage precedes the turn's tool_call, as on the SDK path (usage is known when the message arrives)
    types = [e["type"] for e in events]
    assert types.index("turn_usage") < types.index("tool_call")
```

- [ ] **Step 2: Run it, expect RED** — `PYTHONPATH=$WT $PY -m pytest tests/unit/agents/test_executor_xai.py -k turn_usage -q`. Expected: `AssertionError: expected one turn_usage per turn, got [...]` with no `turn_usage` in the list.

- [ ] **Step 3: Emit it.** In `executor_xai.py`, right after `writer(events.turn_start(turn_number=turn, model=invocation.model, **ids))` (around line 239), add:

```python
                if usage is not None:
                    turn_in = int(getattr(usage, "input_tokens", 0) or 0)
                    turn_out = int(getattr(usage, "output_tokens", 0) or 0)
                    turn_cr = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
                    turn_cc = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
                    writer(
                        events.turn_usage(
                            input_tokens=turn_in,
                            output_tokens=turn_out,
                            cache_read_tokens=turn_cr,
                            cache_creation_tokens=turn_cc,
                            cost_usd=price_usage(
                                invocation.model,
                                input_tokens=turn_in,
                                output_tokens=turn_out,
                                cache_read_tokens=turn_cr,
                                cache_creation_tokens=turn_cc,
                            ),
                            model=invocation.model,
                            **ids,
                        )
                    )
```
Import `price_usage` from `embry0.agents.providers` next to the existing `provider_for_model` import.

- [ ] **Step 4: GREEN** — same command. Expected: passes. Run the whole file: the existing 29 tests stay green (`test_simple_text_turn` asserts `types[0] == "agent_started"` and `types[-1] == "agent_completed"`, unaffected).

- [ ] **Step 5: RED test, run cost prices cache tokens**

```python
def test_run_cost_prices_cache_tokens_like_the_sdk_path(env, monkeypatch):
    # _compute_cost ignored cache reads/writes while price_usage prices them, so
    # cost_update and the summed turn_usage disagreed on every cached run.
    _install(monkeypatch, [_Resp([_Block("text", text="ok")], "end_turn", _Usage(100, 20, 10_000, 1_000))])
    events = []
    out = _run(_invocation(), events)
    from embry0.agents.providers import price_usage

    expected = price_usage("grok-4.5", input_tokens=100, output_tokens=20, cache_read_tokens=10_000, cache_creation_tokens=1_000)
    assert out.cost_usd == pytest.approx(expected), f"cost {out.cost_usd} ignores cache tokens; expected {expected}"
    cost_update = [e for e in events if e["type"] == "cost_update"][-1]
    assert cost_update["cost_usd"] == pytest.approx(expected)
```
Expected RED: `cost 0.00032 ignores cache tokens; expected ...` (the exact figures depend on `CACHE_READ_FACTOR`/`CACHE_WRITE_FACTOR` in `providers.py`).

- [ ] **Step 6: Switch `_compute_cost` to `price_usage`.** Replace the body of `_compute_cost` (executor_xai.py:118-131) with a call that keeps its contract (None for an unpriced model):

```python
def _compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float | None:
    """Cost from provider pricing (xAI compat layer returns no total_cost_usd).

    None when the model has no price entry (#219): the price table is the only
    accounting this executor has, so an unpriced model is an error to record,
    never a silent $0. Prices cache reads and writes with the same factors as
    the SDK path, so cost_update agrees with the summed turn_usage.
    """
    return price_usage(
        model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
    )
```
and pass `totals["cache_read"]`, `totals["cache_creation"]` at its call site. `tests/unit/agents/test_providers.py:41-44` and `test_unpriced_models.py:36-40` call `_compute_cost(model, in, out)` with two positional token counts: still valid (defaults 0); `test_providers.py:43` expects `grok-4.7` at 1 M in + 1 M out to equal `rate_in + rate_out`, unchanged with zero cache tokens. Remove the now-unused `provider_for_model` import if nothing else in the file uses it.

- [ ] **Step 7: GREEN**, then the module's gates: `ruff check embry0/agents/executor_xai.py tests/unit/agents/`, `ruff format --check`, `mypy embry0/agents/executor_xai.py`; `pytest tests/unit/agents/test_executor_xai.py tests/unit/agents/test_providers.py tests/unit/agents/test_unpriced_models.py tests/unit/execution/test_event_contract.py -q` all green.

- [ ] **Step 8: Commit** (two commits, one per concept):
`feat(agents): the direct xAI executor emits turn_usage per turn` and `fix(agents): the direct xAI executor prices cache tokens`, each ending with the `Claude-Session:` line. Deploy note for the PR: `embry0/agents/executor_xai.py` is baked into the sandbox images.

### Task R2: `GROK_` joins the reserved env prefixes

**Files:** Modify `embry0/execution/auth_provider.py` (`HOOK_ENV_PREFIXES`, around line 177, and the module docstring list of reserved namespaces if it enumerates them); Test: the module that already tests `HOOK_ENV_PREFIXES`/`env_override_refusal` (find it with `grep -rln "HOOK_ENV_PREFIXES\|env_override_refusal" tests/unit`), one new test; `docs/configuration.md` or wherever the reserved namespaces are documented for operators (`grep -rn "BUN_" docs CLAUDE.md`), one line.

**Interfaces:** consumes `env_override_refusal(name: str) -> str | None` (or the actual signature at `auth_provider.py`; read it first).

- [ ] **Step 1: RED test**

```python
def test_grok_namespace_is_reserved():
    # Grok Build reads GROK_* variables that redirect its traffic or load code
    # (GROK_AUTH_PROVIDER_COMMAND, GROK_CLI_CHAT_PROXY_BASE_URL, GROK_HOME, GROK_CONFIG,
    # GROK_MANAGED_CONFIG_URL); a sandbox that carries the binary must refuse them
    # from repo env vars, like CLAUDE_, GIT_ and BUN_.
    for name in ("GROK_AUTH_PROVIDER_COMMAND", "grok_home", "Grok_Config"):
        assert env_override_refusal(name) is not None, f"{name} was accepted"
```
Adapt the call and the expected return to the real function (it may return a message string, a bool, or raise); keep the assertion message. Expected RED: `GROK_AUTH_PROVIDER_COMMAND was accepted`.

- [ ] **Step 2: Add the prefix.** `HOOK_ENV_PREFIXES = (..., "GROK_")` with a one-line comment: `# Grok Build: auth provider command, endpoint overrides, home/config paths`. Update the docstring list and the operator doc line.
- [ ] **Step 3: GREEN**, then `pytest tests/unit/execution/ -q -k "auth_provider or env_override or reserved"`, `ruff`, `mypy`.
- [ ] **Step 4: Commit** `feat(execution): reserve the GROK_ env namespace in sandboxes`.

---

## Plan E outline (only if green, OD1 and OD2 taken; not executed here)

From the dossier's section 7, for the record so the findings can point at it: extract `consume()` from `executor.py:676-890` into `embry0/agents/message_stream.py` (pure refactor, T1); `embry0/agents/grok_stream.py` NDJSON-to-SDK-message adapter with the tool-name/input-key mapping and the defaults S4 measured, tested against the scrubbed capture as `tests/fixtures/grok-build-streams/*.ndjson` (T5); `embry0/sandbox/grok_hook.py` (`python -m`, maps GB names to Claude names, `gate_tool_call`, deny JSON + exit 2 on any failure) plus a Ring-2 renderer turning `//path` rules into absolute globs (T2); session directory copy-out and resume (T6); `GrokBuildExecutor` + routing + sandbox env + `config_lint` (T7); binary pin with sha256 per arch in both sandbox Dockerfiles with a pin test (T4); deletions per OD1 and doc updates (T8); live verification on a deployment (T9). Collision table and wave order are in the dossier, section 8.
