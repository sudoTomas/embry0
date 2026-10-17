# Spike: Grok Build as the grok executor (#42)

**Verdict: GREEN on all six restated criteria, on route B with `api_backend = "responses"`; the swap (#43) is gated on two open decisions.**

Green means Grok Build can run as a sandbox executor through the unchanged
xai-proxy, and its stream feeds the Claude path's parser. It does not mean
the swap is safe as it stands. Ring 3 would move from an in-process,
fail-closed check to a hook that Grok Build lets through on crash or
timeout. Which runtime Grok Build would replace is also undecided. Both
are product decisions (OD1, OD2 below), and neither is taken.

Plan: [`docs/superpowers/plans/2026-10-16-trace-fidelity-plan-d.md`](../superpowers/plans/2026-10-16-trace-fidelity-plan-d.md).
Criteria: [spec section 4](../superpowers/specs/2026-10-15-trace-fidelity-design.md#4-grok-build-spike),
as restated by the plan's Decisions 3 to 6.

## What was run

Grok Build **1.0.62** (`grok 1.0.62 (1474f9cbdd91)`), the version
`https://x.ai/cli/stable` reported on 2026-10-16. The Linux aarch64
artifact has sha256
`0b808a2209728788d2e37c34ea1846d460b41528a9fc7edc7698b9e7dec12fe1`. It is
a static ELF, so the base image's glibc does not matter. xAI publishes no
checksum and no signature; the transport md5 matched, and that is all.

The binary was installed into a scratch image built on the local
`embry0-sandbox` image. That image ran on a scratch `--internal` network
beside the stock `embry0-proxy` image running the xai-proxy, with this
tree's `embry0/` mounted over it unchanged. Telemetry, auto-update,
memory, web search and subagents were off.

Criterion 1 was proven offline: the proxy's upstream pointed at an echo
server that hashes every header value, and a dummy upstream token was
used. Criteria 2 to 6 come from one live session under a SuperGrok
session token pushed to the local proxy (`-m grok-4.5`). The run used
route B (a custom model entry in `$GROK_HOME/config.toml`) with
`api_backend = "responses"`. Three live attempts were made: the first
failed on the `messages` backend, the second exposed a hook bug, and the
third is the main run. Two resume runs followed. The captures were then
parsed offline through the SDK's own `parse_message` and an unmodified
copy of `consume()`.

## Criteria

Line numbers refer to the main run's 15-line `streaming-messages-json`
capture: L1 is `system/init`, L2 to L14 alternate `assistant` and
`user`, and L15 is `result`.

| # | Restated criterion | Verdict | Evidence | Caveat |
|---|---|---|---|---|
| 1 | The proxy injected the SuperGrok token and stripped the sandbox bearer; the sandbox never held the upstream token | GREEN, both routes | Echo: route A 14/14 and route B 3/3 requests carry the dummy token's hash; 0 carry the sandbox bearer's. Control without the proxy: 13/14 carry the sandbox bearer, so the check can fail. An in-sandbox sampler read every process's `environ` about every 20 ms: 0 lines with the dummy prefix (1,320 sampled lines on A, 940 on B); no file under `/home`, `/tmp` or `/workspace` holds it | Route B keeps the sandbox bearer in env; route A writes it to `$GROK_HOME/auth.json`. `x-xai-token-auth` passes the proxy unstripped |
| 2 | The stream parses with the Claude path's handling after a thin adapter and yields `tool_call`, `tool_result`, `turn_usage`, `cost_update` under the contract | GREEN | 0 `MessageParseError`s on the main run, resume run and attempt 2. 36 events, 36/36 contract-valid. 7 `tool_call` = 7 `tool_result` (paired by `tool_id`, including the parallel pair in L8/L9); 7 `turn_usage` for 7 assistant lines; 1 `cost_update`. `consume()` changed: 0 of 230 lines. Each assertion was also shown failing under a mutation | Validity holds even unadapted; the 43-line adapter buys fidelity, not validity. The `tool_result` normaliser is required for ask-user (below) |
| 3 | Hook deny and `--deny` each block their command, visible as an error tool result; the denied command did not run; the hook uses Grok Build's names | GREEN | 5 `tool_result`s with `is_error: true`: L3, L5, L13 (hook, `Hook denied: spike: curl denied by hook`), L7, L11 (policy, `deny rule on bash matching "rm *"`). `build/keep.txt` still exists afterwards; no curl output in the main run | Needed a word-match hook. The harness's prefix hook let a compound `...; curl ...` through in attempt 2, and curl ran (it failed only at DNS) |
| 4 | `--resume <id>` continues the conversation in a fresh container after copying the session directory and, separately, `updates.jsonl` alone; same `session_id` | GREEN for the directory | Session `01a147bf-66b9-70f2-afe6-511bb06fe0fb`. Directory copy: init `session_id` equals it, the answer quotes the README marker line from history with no tool available, `num_turns` 1 | `updates.jsonl` alone fails (rc 1, empty stream) and triggers a remote restore from xAI's session service, an egress path. So the directory is what suffices |
| 5 | Init `skills` lists a user-invocable skill from a plugin loaded via `[plugins] paths` + `enabled` | GREEN | L1 `skills: ['spike-skill']`; debug log: `plugin discovered name=spike-plugin ... skills=1`. Same on all three attempts | A resumed home without `plugins/` reports `skills: []`; plugins must be re-rendered on resume |
| 6 | Every `assistant` line carries `message.usage` with the four token fields, `price_usage("grok-4.5", ...)` prices each, `result.usage`/`modelUsage` present; the side-model calls are named | GREEN | 7/7 assistant lines carry `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`; all 7 priced. `result.usage` = their sum (4,487 in, 644 out, 23,296 cache read, 0 cache creation); `modelUsage` keyed by `grok-4.5` only | `total_cost_usd` is not 0, against the criterion's expectation. It is about 2.6 times below embry0's table. The `grok-4.6` title call and a post-turn summary call are unpriced and absent from usage |

## Credential routes and the paths each calls

**Route A** is the spec's original route. The session-service base
(`[endpoints] cli_chat_proxy_base_url`) points at the proxy, and
`GROK_AUTH_PROVIDER_COMMAND` prints the sandbox bearer. In this mode
Grok Build treats the base as its own account service:

| Path | Calls (offline run) | Purpose |
|---|---|---|
| `GET /` | 1 | connectivity probe, no credential; the proxy answers 404 itself |
| `GET /v1/user` | 3 | account |
| `GET /v1/models` | 2 | model catalog |
| `GET /v1/settings` | 4 | remote settings and managed config |
| `GET /v1/bundle/archive`, `/v1/subagents/bundle` | 1 each | bundle sync |
| `POST /v1/responses` | 2 + 1 | the `grok-4.6` title call, then the `grok-4.5` turn |

All startup fetches 404'd harmlessly. Only the Responses call is fatal
offline. Route A was not run live. That route B's `responses` backend
works live shows that `api.x.ai` serves `/v1/responses` to a SuperGrok
token. Whether it serves `/v1/user`, `/v1/settings` and the bundle paths
is unknown. Despite the `auth.token_header` setting (default
`xai-grok-cli`), the credential travels as `Authorization: Bearer`. The
setting changes only the value of a separate marker header,
`x-xai-token-auth`.

**Route B** is a custom model entry:
`[model."grok-4.5"] base_url = <proxy>/v1`, an `api_backend`, and an
`env_key` naming a variable that holds the sandbox bearer. It made no
session-service calls. Grok Build tried to fetch the
`cli-chat-proxy.grok.com` catalog and `/login-config`, but refused both
locally for lack of a credential, before opening a connection. Per run
it sends three kinds of model call: the turn, a title call naming
`grok-4.6` (sent through the `grok-4.5` endpoint, because the side model
has no credential of its own), and a post-turn `grok-4.5` summary call
with no turn index. The table key must be quoted. The user guide's
unquoted `[model.grok-4.5]` parses as `[model.grok-4]` plus a key `5`,
and Grok Build then fails closed with "Not signed in".

**Why `messages` failed live.** With `api_backend = "messages"`,
`api.x.ai` accepted the SuperGrok token on `/v1/messages` and started
streaming. Its `content_block_delta` events of type `thinking_delta`,
however, carry no `index` field. Grok Build's strict deserializer aborts
the turn on them (`missing field 'index'`), on the turn and on the title
call alike. Switching to `api_backend = "responses"` was the only config
change, and it reaches `/v1/responses` through the proxy, which forwards
any `/v1/{path}` unchanged. The amended spec section 4 still names
`messages` as the proven route. That held only offline against the echo,
and the live run supersedes it.

## What the Claude path's `consume()` needed

Nothing. 0 of the 230 lines (`embry0/agents/executor.py:475-494`, the
type predicates, and `:674-883`, `consume`) differ in the lifted copy;
a diff run alongside the parse measured this, and it reports 1 when a line is changed.
The SDK parser needed no defaults either: Grok Build sends a non-empty
`signature` on every thinking block and `duration_api_ms` on `result`.
A mutation that deletes those two fields fails on exactly those two, so
the zero is a measurement.

The adapter is 43 non-blank lines, plus 4 stubs for executor state (the
clock, a list-append writer, an invocation namespace feeding the real
`_ids()`, and `resumed_cost_usd = 0.0`). It has three parts:

- **Tool names** (15 lines). Only `run_terminal_command` occurred
  (7 calls), with inputs `command` and `description`, which are already
  Claude's Bash keys. The `--tools` id for the same tool is
  `run_terminal_cmd`, and both spellings must map to `Bash`. The other
  mappings (`read_file`, `list_dir`, `use_tool` to `mcp__server__tool`)
  were tested only on synthetic blocks. `use_tool`'s argument key is a
  guess.
- **`tool_result` normaliser** (18 lines). This part is required, not
  cosmetic. Grok Build delivers `tool_result.content` as a JSON string.
  A denial is `[{"type":"content","content":{"type":"text",...}}]`. A
  shell result is `{"type":"Bash","output":[...],"exit_code":N,...}`,
  where `output` arrived as a list of byte values in every captured
  shell result. Without the normaliser, `tool_result.content` is a list
  of integers. EMB-44's embedded ask-user recovery then never finds its
  `{"type": "agent_ask_user"` marker: a synthetic probe emits
  `[tool_result]` unadapted and `[agent_ask_user, tool_result]` adapted.
- **Per-line glue** (10 lines).

Two fidelity gaps remain after the adapter. Grok Build reports a
non-zero shell exit as `is_error: false`: the compound command whose
curl exited 6 is an example, whereas Claude's Bash marks it an error.
The adapter keeps Grok Build's flag and prefixes `Exit code N`. Whether
to flip the flag is a plan-E choice. Denial texts also name Grok Build's
tools ("Tool `run_terminal_command` was not executed"), so its
vocabulary leaks into result content. Turn numbering is one `turn_start`
per `assistant` line, which equals Grok Build's `num_turns`
(7, counting model calls).

## Sessions and resume

A session is the directory
`$GROK_HOME/sessions/<encoded-cwd>/<id>/`: 20 entries, among them
`updates.jsonl`, `chat_history.jsonl`, `events.jsonl`, `summary.json`,
`usage.json`, `tool_definitions.json`, `rewind_points.jsonl`, a
`terminal/` directory and lock files. Copying the whole directory into
a fresh container resumes correctly, and the debug log shows the
persisted model being restored.

Copying `updates.jsonl` alone does not work. Grok Build reports the
session "not found locally, restoring conversation from remote" and
starts an OIDC discovery against `auth.x.ai` to fetch it from xAI's
session service. The `--internal` network blocked all three attempts
before any model call. This is an egress path a deployment must expect:
an incomplete session copy reaches out silently instead of failing
locally. The minimum file set was not explored.

## Cost and usage

- Per-message usage is complete and prices with
  `price_usage("grok-4.5", ...)`. `result.usage` is the exact sum of the
  per-message figures, and `input_tokens` excludes cached tokens, as in
  Anthropic semantics. Caching held from the second message on: cache
  reads were 23,296 tokens against 4,487 uncached input tokens.
- Grok Build's own `total_cost_usd` is non-zero under the SuperGrok
  token on route B, against the user guide (`14-headless-mode.md`) and
  the criterion's expectation. embry0's table prices the same tokens
  about **2.6 times** higher on the main run and **2.9 times** on the
  resume run. On a Grok Build executor, the live `turn_usage` ticker and
  the `cost_update` total (which passes Grok Build's figure through)
  would disagree by that factor unless one source is chosen.
- Grok Build's total is per run, not cumulative across a resume, so
  the resumed-cost subtraction (#501) is not needed.
- Usage omits two side calls per run: the `grok-4.6` title call and a
  post-turn `grok-4.5` summary call (low effort). The main run made
  **9 proxy requests for 7 assistant messages**. `grok-4.6` was really
  served (the debug log records its model fingerprint), yet every stream
  `assistant` line says `grok-4.5`, and `grok-4.6` has no entry in
  `embry0/agents/providers.py`. The quota these calls spend is not
  reported anywhere in the stream.

## Egress observed

The sandbox network was `--internal`, so any attempt to reach an outside
host failed at DNS. The debug logs name no host other than the proxy,
the echo, and the three below.

| Attempt | Where seen | Outcome |
|---|---|---|
| `GET /` prewarm to the proxy root | both routes | proxy 404, not forwarded |
| Session-service account, catalog, settings and bundle GETs (`/v1/user`, `/v1/models`, `/v1/settings`, `/v1/bundle/archive`, `/v1/subagents/bundle`) | route A (offline) | forwarded by the proxy to the upstream; 404 from the echo |
| `cli-chat-proxy.grok.com` `/v1/models`, `/login-config` | route B | refused inside Grok Build for lack of a credential; no connection |
| `auth.x.ai` OIDC discovery (remote session restore) | resume with `updates.jsonl` only | 3 attempts, blocked by the internal network |
| `example.test` (the model's curl) | attempt 2 | ran, failed at DNS; denied in the main run |
| Telemetry, turn-delta feedback, OTel | all runs | telemetry suppressed by env. Feedback stays on by default and OTel logs "emission gate opened", but neither sent a request |
| Auto-update, memory sync, web search | all runs | none seen |

Route A also spawned `bash`, and route B spawned `hostnamectl` and
`awk`, to probe the environment. If a Grok Build executor ever uses
route A, the proxy forwards the session-service GETs to the model API
with the SuperGrok token; a path allowlist on the proxy would close that.

## Ring 3 under Grok Build

On the Claude path, Ring 3 is 41 case-insensitive regexes
(`embry0/safety/patterns.py`) over NFKC-normalised input, applied
in-process. It fails closed: the executor refuses to run without the
hook. Grok Build offers three layers instead:

- **`--deny` rules are globs.** For `Bash` rules, `*` matches anything,
  spaces included. Alternations, `\s+`, anchors and the negative
  lookahead in the regex set have no glob form, so only a coarse floor
  translates. `--deny "Bash(rm *)"` did match inside compound commands
  (`&&` and `;`), which is the useful part.
- **Hooks fail open.** "All hook failures (timeouts, crashes, malformed
  output, missing required env vars) are fail-open" (`10-hooks.md`).
  The default timeout is 5 s. The hook runs before the permission
  policy.
- **Names and keys differ.** A hook sees camelCase `toolName`/`toolInput`
  with Grok Build's names (`run_terminal_command`, `read_file`,
  `list_dir`, `use_tool`). A hook keyed on Claude's `Read{file_path}`
  lets `read_file` on `/etc/passwd` through, because it finds no path
  and allows. The harness self-test showed this failing with a
  Claude-only key list. The documentation disagrees on the `read_file`
  path key (`14-headless-mode.md` shows `path`; recorded tool
  definitions show `target_file`), and no `read_file` occurred live, so
  a hook must check both.
- **The prefix lesson.** The spike's own hook denied commands that
  *start with* `curl`. The model folded curl into
  `head ...; ls -la; curl ...`, the hook allowed it three times, and
  curl ran. Any translation of a Ring-3 pattern must match words inside
  compound commands, not prefixes, and the test corpus needs compound
  forms.

**Proposed mitigation**, if OD2 accepts the change:

1. An uncrashable `python -m` hook that maps Grok Build names and input
   keys to Claude's and calls `gate_tool_call` (Ring 3 and Ring 2). It
   wraps everything, so any exception, unknown tool or malformed payload
   denies with exit 2. The only fail-open left is a hook that cannot
   start or exceeds an explicit timeout.
2. A coarse `--deny` floor for the patterns that have glob equivalents.
3. `--sandbox` for the filesystem.
4. `--tools` with internal ids. The model-facing `run_terminal_command`
   given to `--tools` names no tool, and Grok Build then fails *open*,
   leaving all 21 tools available (it warns, and that is all).

Ring 2 needs its own renderer. embry0's `//`-anchored rules in
`.claude/settings.json` are literal glob text to Grok Build, so they
match nothing.

## Other findings for an executor

- **`x-grok-conv-id` is Grok Build's.** It sets its own session id on
  model calls and an empty value on the title call. The proxy fills
  only a missing header, so its sandbox-id stamp (EMB-54) no longer
  applies, and cache stickiness on the title call is unset.
- **Where the bearer lives.** Route B's `env_key` puts the sandbox bearer
  in the environment of every process Grok Build spawns. The tree keeps
  it in a 0600 file (`embry0/sandbox/xai_token.py`). Route A keeps it out
  of env but persists it to `$GROK_HOME/auth.json` (0600). Either file
  must stay out of any session copy-out. Neither route exposes the
  upstream token.
- **Compat sources and `--trust`.** Grok Build also loads Claude, Cursor
  and Codex hooks, skills, MCP and rules. The spike switched every
  `[compat.*]` source off and never passed `--trust`, which project
  skills and settings require headless and which also activates the
  repository's own hooks and MCP servers. Whether an executor passes it
  is open.
- **`--tools` vocabulary.** It takes internal ids (`read_file`,
  `run_terminal_cmd`), while the init line and the stream use
  model-facing names (`run_terminal_command`). The MCP meta-tools
  `search_tool` and `use_tool` always stay in the list.
- **`--no-auto-update`** is missing from `--help` but accepted; config
  and env switches exist as well.
- **Version.** The spec's 1.0.50 was a local install; stable was 1.0.62
  on the day, and this spike pins 1.0.62 with the sha256 above. xAI's
  source repository has no releases or tags, so a pin is the only
  reproducible reference.

## Open decisions

**OD1. Which runtime would Grok Build replace?** Grok's default today is
the Claude CLI over the xai-proxy (`SdkAgentExecutor`), and the opt-in
`DirectXaiExecutor` is insurance against a CLI release breaking on xAI's
compat endpoint.

- **(a) The opt-in fallback only.** Deletes `executor_xai.py`,
  `xai_tools.py`, `mcp_client.py` and their tests. Keeps the SDK path as
  the default, with QA MCP, resume and in-process Ring 3 unchanged; Grok
  Build becomes the insurance instead. Its own failure mode (an
  `api.x.ai` stream shape breaking its strict client, as `messages` did)
  is the kind of break the insurance exists for.
- **(b) The default runtime too.** Grok stops running the Claude CLI.
  QA's MCP servers move to `[mcp_servers.*]` behind `use_tool`, resume
  moves to directory copy-out, and Ring 3 moves to the hook (OD2). The
  SDK-over-proxy overlay stays as the fallback.
- **(c) Both.** Deletes the direct path and the SDK-over-proxy overlay
  (`embry0/agents/executor.py`). Grok then has one runtime and no
  insurance.

If OD1 keeps the direct path, the always-applicable conformance work
(per-turn `turn_usage` and cache-aware pricing in `DirectXaiExecutor`)
runs regardless.

**OD2. Is a fail-open Ring-3 layer acceptable on the grok path?** Under
Grok Build the hard boundary would be `--deny` globs (a coarse floor,
not the 41 regexes), `--tools` with internal ids, and `--sandbox`. The
regex policy would run in the mapping hook, which can be made to deny
on every failure it can see, but not on a crash before it starts or a
timeout. If the answer is no, #43 is red regardless of the six criteria,
and the direct path stays. If yes, ADR 0010 records it, amending ADR
0001's Ring-3 hook assumption.

## What plan E would contain if both are taken

The plan's "Plan E outline" covers this. In short: extract `consume()`
into a module unchanged. Add a Grok Build stream adapter (name and key
mapping, the `tool_result` normaliser), tested against the scrubbed main
capture as the first recorded input-stream fixture. Add the uncrashable
hook and a Ring-2 glob renderer, and switch session copy-out to the
whole directory, with `auth.json` excluded and plugins re-rendered on
resume. Then build the `GrokBuildExecutor` on route B with
`api_backend = "responses"` and quoted model keys, pin 1.0.62 and its
sha256 per architecture in both sandbox Dockerfiles, delete what OD1
names, and finish with live verification on a deployment, including
the QA flow. Plan E also has to settle three questions: whose cost
total is authoritative, a defensive strip of `x-xai-token-auth` at the
proxy, and whether to flip `is_error` on a non-zero exit.

## Not exercised

Subagents (`--no-subagents`; every line had `parent_tool_use_id: null`),
compaction (no `compact_boundary`), ask-user (synthetic probe only), MCP
tools (no `use_tool` captured), the QA flow (needs a deployment), route A
live, `read_file` live, `consume()`'s error-result branches, and the
minimum file set for resume.

## Reproducing

The harness lives outside the tree by design (captures can carry session
material). Run in order:

1. **S1.** Download the stable binary, check the sha256 above, build the
   scratch image (`Dockerfile`, `auth_provider.sh`). Helpers:
   `proxy_up.sh echo|live`, `proxy_down.sh`, `enroll.sh`, `push_token.sh`
   (token on stdin only), `proxy_health.sh`, `hook_selftest.sh`; homes
   `grok-home-A/`, `grok-home-B/` (`config.toml`, `hooks/spike.json`,
   `hooks/deny_curl.py`, `plugins/spike-plugin/`).
2. **S2.** `proxy_up.sh echo` with a dummy token; `run_route.sh` per
   route and `env_sampler.sh`; `echo_s2.py` hashes every header and
   `summarize.py` reads its log.
3. **S3.** `proxy_up.sh live` with a freshly signed-in session token
   piped to `push_token.sh`; `run_live.sh` for the main and resume runs.
4. **S4.** `parse_capture.py` (offline, `PYTHONPATH` on this tree) for
   the parse, adapter count and scrub; `controls.py` for the mutations
   that show each assertion can fail.
