# Trace fidelity: one event contract from the sandbox to the browser

**Status:** draft for review, 2026-10-15
**Workstream:** 1 of 3 (trace fidelity → agent identity and skills → game UI)
**Tracker:** GitHub milestone "Trace fidelity"

## 1. Problem

The dashboard cannot show what an agent did because the event stream feeding it is incomplete at the source and has no owner. An audit of the pipeline on 2026-10-15 (sandbox executor → runner → LangGraph writer → `_broadcast_event` → `job_logs` → WebSocket → React hooks) found, each verified against the code:

1. **Tool results never leave the sandbox on the Claude path.** `SdkAgentExecutor` reads `UserMessage` content only to recover embedded ask-user events and emits nothing else (`embry0/agents/executor.py:784-804`). The `ToolResultBlock` branch it does have fires only for `AssistantMessage`, which the Anthropic protocol never carries tool results on. The xAI executor does emit them. Consequence: no ✓/✗ per tool call, no "what did the tool return", QA browser snapshots invisible.
2. **Executor errors are invisible.** Policy denials, timeouts and provider errors are emitted as `{"type": "error", "error": ...}` with no `node` (`executor.py:361,868`; `executor_xai.py:176-365`). Every frontend reader expects `message` and a node, so they render as blank lines attached to no agent.
3. **No terminal event.** Nothing emits `complete`, `stream_end` or any job-finished signal. The socket stays open until the client leaves, "Live" stays lit on finished jobs, the client retries ten times against a finished job, and `isComplete` depends on a ten-second poll.
4. **Reconnects duplicate history.** `streaming.py` supports `?since_seq=`; `useJobEventStream.ts:97` never sends it, and the replay is appended to the existing buffer. A bus overflow drop (`bus.py:73`) is invisible to the client because it never compares `event_seq`.
5. **Cost is one number per run, at the end.** `cost_update` fires once at `ResultMessage` (`executor.py:744-770`); `AssistantMessage.usage` is never read. Frontend reducers also treat the delta as a total (`useJobLogs.ts:54`, `useLiveJobSummary.ts:106`), so the logs page shows the last agent's cost.
6. **No run correlation.** Events carry `node` (an agent type) and nothing else. A retried run's events are indistinguishable from the first run's; `traces.trace_id` is never written onto any event; `job_logs.agent_id` exists and is never written; subagent messages (`parent_tool_use_id`) are flattened into the parent's turn count.
7. **Vocabulary without an owner.** Two dead backend enums (`embry0/execution/events.py`, `embry0/sandbox/events.py`), ad hoc dict literals at every emit site, and a frontend union (`events-discriminated.ts`) with seventeen types the backend never sends and about ten it does send but the union lacks. `StructuredView` renders only the dead vocabulary, so it is empty for real jobs.
8. **Nothing on the event path is redacted.** `redact_secrets` is applied to stderr tails and diagnostics only. A `cat .env` in a tool result or a Bash command containing a token lands verbatim in `job_logs` and the browser.
9. **Two events bypass persistence.** `job_failed` is published to the bus only (`issue_executor.py:783-796`); QA `subtask_status`/`done` live on a second bus with a 64-deep oldest-drop queue and no replay.
10. **Silent truncation.** `text` 2000, `thinking` 3000, `tool_result` 1000, `tool_call.input` 200, `result_summary` 500 characters, with no marker.

Milestones 2 and 3 build the agent roster, skills and the game-themed live view on top of this stream. Rebuilding the UI first would mean rebuilding it twice.

## 2. Goals and non-goals

**Goals**

- One typed event vocabulary, defined once in Python, mirrored in TypeScript, pinned by a contract test on both sides and documented in `docs/api.md`.
- Every signal the runtime produces reaches the browser with its correlation keys intact: tool results paired to tool calls, subagent boundaries, per-turn usage, run ids, node ids, a terminal event.
- A client that reconnects or reloads reconstructs the same timeline as a client that never disconnected, with no duplicates and with detection of gaps.
- Secrets never enter `job_logs`.
- A spike that answers whether Grok Build can replace the hand-rolled xAI executor and join this contract.

**Non-goals**

- Any visual redesign. Frontend changes in this workstream are limited to adopting the contract and removing dead code paths, so the current pages become honest. Milestone 3 restyles them.
- A new trace store. `job_logs` stays the single write path; it gains columns and indexes.
- Sandbox resource metrics (CPU, memory, egress drop counters). Out of scope; filed separately if wanted.
- Replacing the QA SSE route for the QA dashboard. It keeps working; the job stream additionally carries what it needs.

## 3. Design

### 3.1 The event contract

**Home.** `embry0/execution/events.py`. It already exists, is dead, and is one of the four `execution/` modules baked into the sandbox image (`infra/Dockerfile.sandbox:67`), so the sandbox executor and the orchestrator import the same definitions. The duplicate enum in `embry0/sandbox/events.py` is deleted; its `emit_event` helper moves into the contract module.

**Shape.** A `StrEnum` of event types and one `TypedDict` per type, all extending a base:

```python
class BaseEvent(TypedDict):
    type: str           # EventType value
    timestamp: str      # UTC ISO 8601, stamped where the event is created
    job_id: NotRequired[str]        # stamped by _broadcast_event
    event_seq: NotRequired[int]     # stamped by _broadcast_event after persist
    prev_seq: NotRequired[int]  # stamped by _broadcast_event: the job's previous persisted event_seq
    run_id: NotRequired[str]        # every event of one agent run (3.4)
    node: NotRequired[str]          # graph node id, not the agent type (3.4)
    agent: NotRequired[str]         # agent type
    parent_tool_use_id: NotRequired[str]   # set on subagent events (3.3)
```

Constructor functions, one per type, are the only way production code builds an event: `events.tool_call(run_id=..., node=..., tool_name=..., ...)`. They are plain functions returning dicts; validation happens in tests, not at runtime, so the sandbox never pays for Pydantic.

**The vocabulary** (final; everything else is removed):

| Type | Emitted by | Payload beyond base |
|---|---|---|
| `agent_started` | executors | `model`, `max_turns` |
| `turn_start` | executors | `turn_number` (top-level turns only), `model` |
| `turn_usage` | executors, per `AssistantMessage` | `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `cost_usd` (delta, priced locally), `model` |
| `text` | executors | `text`, `truncated`, `original_length` |
| `thinking` | executors | `text`, `truncated`, `original_length` |
| `tool_call` | executors | `tool_id`, `tool_name`, `summary` (≤200 chars), `tool_input` (full, redacted) |
| `tool_result` | executors | `tool_id`, `content`, `is_error`, `truncated`, `original_length` |
| `subagent_start` | executors | `tool_id` (the Task call), `name` |
| `subagent_end` | executors | `tool_id`, `is_error` |
| `context_compacted` | executors | `pre_tokens` when known |
| `agent_ask_user` | executors (recovered from tool output) | unchanged payload |
| `cost_update` | executors, once per run | run totals: `cost_usd`, `duration_ms`, `duration_api_ms`, `num_turns`, four token counters |
| `agent_completed` | executors | `is_error`, `error_code`, `policy_denials`, `output` (tail, redacted), `tools_called` |
| `error` | executors and nodes | `message`, `code` (an `ErrorCode` when known), `tool_name` |
| `node_started` | nodes | `attempt` |
| `node_completed` | nodes | `action`, `attempt`, `ok`, `round`, `deliverables` (count), `error` |
| `pipeline_graph` | plan_route, onboard, QA orchestrator | `graph` |
| `progress` | nodes | `message`, `app` |
| `interrupt` | nodes | `reason`, `questions`, `summary` (a dict) |
| `agent_paused_for_input` | nodes | `count` |
| `review_decision` | review | `decision`, `summary` (both nullable), `duplicates_open_pr` (the duplicate PR's data) |
| `validation_result` | review | `validation` (a dict: the validator's result, nested like the other payloads) |
| `verification_result` | verify | unchanged |
| `pr_created` | developer | `pr_url`, `branch` |
| `qa_apps_resolved`, `qa_subtask_started`, `qa_subtask_finished`, `qa_subtask_status`, `qa_agent_transient_retry` | QA | `app` plus existing fields; `qa_subtask_status.boot_phase` is a dict (the structured boot outcome) |
| `system` | executor service | `message` |
| `job_failed` | executor service | `kind`, `message` |
| `job_finished` | executor service | `status`, `error_code`, `error_message`, `pr_url`, `total_cost_usd` |

Removed from the frontend union because nothing ever emitted them: `complete`, `stream_end`, `tool_start`, `tool_end`, `tool_use`, `node_state`, `feedback_triggered`, `feedback_resolved`, `pipeline_stage_started`, `pipeline_stage_completed`, `awaiting_input`, `auto_answered`, `input_resumed`, `conversation_message`, `finding_published`, `github_api`.

**The mirror.** A backend test writes `docs/contracts/events.json`: for each type, its required and optional field names. The test fails when the file on disk differs from what the code produces, so a vocabulary change is a visible diff in the PR. A vitest test loads the same file and asserts that the TypeScript union (`frontend/src/lib/types/events.ts`) declares exactly those types with those fields. The TS file is hand-written, not generated, so it stays readable; the test is what keeps it honest.

**Documentation.** `docs/api.md` gains an "Events" section rendered from the same JSON by a small script run in the backend test, so the doc cannot drift either. The nonexistent `/graphs/*` routes are removed from `docs/api.md` in the same change.

### 3.2 Tool results and errors on the Claude path

In `SdkAgentExecutor.consume()`:

- For every `UserMessage` whose content is a list, each `ToolResultBlock` (duck-typed on `tool_use_id`) emits `tool_result` with `content` rendered to text (strings as is; list blocks joined by their `text` fields, image blocks replaced by `[image]`), capped with a marker (3.6), and `is_error`. The ask-user recovery stays.
- Executor `error` events gain `message` (replacing the `error` key), `code`, and the run's `node`/`run_id`. Policy denials carry `code=ERR_TOOL_DENIED` (the existing code for a denied tool call) and `tool_name`.
- `agent_started`, `agent_completed` and the stream-failure `error` carry `node` and `run_id` like every other executor event. Today they carry neither.

The xAI executor makes the same field changes so both paths conform until the Grok Build decision (section 4) retires it.

### 3.3 Subagents, turns and per-turn usage

- Every SDK message's `parent_tool_use_id` is copied onto the emitted event. Events with a parent are subagent events.
- When the first event with a given `parent_tool_use_id` arrives, the executor emits `subagent_start` naming the parent `tool_call` (the `Task`/`Agent` tool's input gives the subagent type). When the parent's `tool_result` arrives, it emits `subagent_end`.
- `turn_number` counts only top-level `AssistantMessage`s. Today it counts subagent messages too, which is why a job shows turn 193 while the result says 70.
- Each `AssistantMessage` with `usage` emits `turn_usage` with the four token counters and a cost delta priced from `embry0/agents/providers.py` for that model (cost is per-model, so a subagent on a different model prices correctly). `cost_update` stays as the run's authoritative total from `ResultMessage`; the orchestrator keeps incrementing `jobs.total_cost_usd` on `cost_update` only, so the database total does not double count.
- `SystemMessage` with subtype `compact_boundary` emits `context_compacted`. Other system subtypes are ignored.

### 3.4 Run ids, node ids and the trace row

- `run_agent_node` mints `run_id` (UUID4) before the sandbox exec, writes the `traces` row at that moment with `result='running'`, and passes `run_id` into the invocation. The sandbox stamps it on every event. The row is updated at run end with the existing fields. The `traces.trace_id` column becomes the run id (same value), so the trace row and the events share one key.
- `node` means the LangGraph node id (`developer`, `generic_agent`, `review`, …) on every event, including the sandbox's. The invocation carries `node` so the executor stamps the same id the node events use; today a generic-agent step emits `node=generic_agent` while its agent's events say `node=<agent_type>`, and the UI attaches nothing to the card. `agent` carries the agent type separately.
- `node_started`/`node_completed` carry `attempt`, counted by the node itself (retry loops re-enter the node). The frontend stops inferring attempts from event order.
- `job_logs` gains `run_id TEXT`, `node TEXT`, `event_type TEXT`, populated by `append_log_event` from the payload, with indexes `(job_id, event_type)` and `(run_id)`. The unused `agent_id` column is dropped. Migration **95** (both deployments are at 94; the number is assigned at merge time, one PR at a time, per CLAUDE.md).
- `traces.result` gains `running`; the frontend filter and color map are corrected to the stored values (`success`, `error`, `running`).

### 3.5 Terminal event, reconnect and gaps

- `job_finished` is emitted through `_broadcast_event` by the executor's outcome path for every terminal transition (completed, failed, partial, cancelled, expired, skipped) and when a job is orphaned at startup. Paused and awaiting-input are not terminal and emit nothing new (the `interrupt` and `agent_paused_for_input` events already cover them). The paused-job reaper and the boot orphan sweep have no executor; they call the same job-events core directly (the boot sweep with no bus: nothing is subscribed before the server listens, so the event is persisted and replayed). The synthetic `node_completed(action="orphaned")` marker is gone; `job_finished` carries the orphan's error code.
- The WebSocket route closes with code 1000 after forwarding `job_finished`. A client connecting to a job whose latest persisted event is `job_finished` gets the replay and then the close. `job_failed` goes through `_broadcast_event` like everything else.
- The client (`useJobEventStream`) tracks the last `event_seq` it processed and reconnects with `?since_seq=<last>`. Replayed events ≤ last are dropped. `event_seq` is `job_logs.id`, shared by every job, so consecutive events of one job are not contiguous; the orchestrator stamps `prev_seq`, the job's previous persisted `event_seq`, and a live event whose `prev_seq` is greater than the client's last processed seq means a gap (bus overflow or a persist failure): the client fetches `GET /jobs/{id}/logs/events?since_seq=<last>` to fill it before applying the live event. On `job_finished` the client marks the stream complete and never reconnects. A server close with code 1000 means the same (the route closes that way straight after replay for a job that was already terminal).
- An event whose persist failed is still published, with no `event_seq`; clients apply it but it cannot be replayed. The `event_persist_failed` log line is kept; a counter `embry0_job_events_persist_failed_total` is added so it is visible in Prometheus. Rule: `or vector(0)` and a threshold test that shows it fires (CLAUDE.md guardrail).

### 3.6 Redaction and truncation

- `_broadcast_event` redacts before persisting and before publishing: `redact_secrets` with `known_secret_values()` is applied to every string value in the event, recursively through `tool_input`, `result`, and lists. Registry credential secrets are registered with `register_known_secrets` at startup and after create or rotate; the sandbox manager registers each per-sandbox bearer when it enrolls the sandbox (the bearer matches no pattern on its own), and the executor service registers the job's resolved LLM secrets at job start (which covers env-fallback credentials). Every scrub uses `secret_env_values()`, the process's secret env values plus the registered set.
- The sandbox executors scrub `text`, `thinking`, `tool_result.content` and `agent_completed.output` with the secret values they can see (their own env) before the constructor caps them, so a token is never cut before it is matched; the runner's writer scrubs every string value of the event once more before stdout, so `tool_input` is covered and no line depends on one side alone.
- Caps become markers: `text` 4000, `thinking` 4000, `tool_result` 4000, `agent_completed.output` 10000 (unchanged), each with `truncated: true` and `original_length` when cut. `tool_call.summary` keeps 200; `tool_input` is stored whole (it already is) but a single string value above 64 KiB is cut with a marker inside the value (the event shape is unchanged), so a `Write` of a large file does not store the file twice.
- `traces.result_summary` is redacted at write.

### 3.7 QA and executor-service events on the job stream

- The QA orchestrator emits `qa_subtask_status` through the parent writer at sub-task start and end (as well as to the QA bus), and the three QA graph nodes emit `node_started`/`node_completed`, the orchestrator's `node_completed` carrying `action=<overall_status>`, which is the run-level outcome on the job stream; the QA bus `done` keeps closing the SSE route and gains no contract type. The SSE route and `QAEventBus` are unchanged in this workstream; milestone 3 removes them once the merged job page reads the job stream.
- `job_failed` goes through `_broadcast_event`; the startup orphan path emits `job_finished` (3.5).

### 3.8 Frontend adoption (honesty fixes only)

- `frontend/src/lib/types/events.ts` becomes the single union; `events-discriminated.ts` and its phantom variants are deleted.
- `useJobEventStream`: `since_seq`, dedupe, gap fill, `job_finished` handling, no reconnect after terminal.
- `useJobLogs` and `useLiveJobSummary`: cost is the sum of `cost_update` deltas, with `turn_usage` adding the in-flight run's partial until its `cost_update` arrives; tokens likewise. `latestTurns` from top-level `turn_start`.
- `useAgentStates`: keyed by `run_id`, grouped by `node`; pending nodes come from `pipeline_graph` through `deriveNodeStates`, never from a hard-coded list; legacy events without `run_id` attach to the latest run of their node; `error.message` and `code`; `attempt` from the event; tool results paired by `tool_id`; subagent events nested under their parent tool call.
- `StructuredView`: renders `tool_call`/`tool_result`/`thinking`/`turn_start`/`subagent_*`; `progress` renders `message`. `RawView` prints every event one line with its type and seq. `ConversationView` reads `tool_call.summary` and `tool_input`.
- `JobLogsPage`: node filter matches `node`; the graph appears for every job kind that emits `pipeline_graph` (onboard and QA orchestrators emit one in this workstream so the onboarding job no longer shows an empty map).
- `TracesTable`: result filter values and colors match storage; agent filter options come from `GET /agents` (landed in #56; #41 adds its render test).
- Dead code removed: reducers for the deleted types, the `awaiting_input`/`pendingInputs` plumbing that no event feeds, the `paused_at`/`ttl_hours` interrupt plumbing: the `interrupt` event carries no TTL, so `InterruptData` reads `reason`, `questions` and `summary.*` and the never-fed `NeedsYouCard` countdown goes.

### 3.9 Error handling

- A malformed line from the sandbox (not JSON) is dropped by the agent runner with an `event_parse_failed` log line, as today; wrapping it as a `system` event is not part of this workstream.
- A constructor is never called with an unknown type; the contract test scans `embry0/` for a literal dict whose `"type"` value is an `EventType` member and fails on any hit outside the contract module and its tests, so every emit site goes through a constructor.
- Persist failure: event still published, counter incremented, client cannot replay it (3.5).
- Redaction failure (unexpected exception in the scrubber): the event is dropped, not published, and logged with the event type only. Leaking is worse than a missing line.

## 4. Grok Build spike

**Question.** Can Grok Build (xAI's open-source coding agent CLI, Apache-2.0, 1.0.50 as of 2026-10-15; stable was 1.0.62 on 2026-10-16, and the spike pins the version and sha256 it ran) replace `DirectXaiExecutor`, so that both providers share one stream parser and the hand-rolled loop, `xai_tools.py` and `mcp_client.py` can be deleted? (They already share the parser on the default SDK-over-proxy path, where grok runs on the Claude CLI; `DirectXaiExecutor` is the opt-in fallback. The spike therefore asks whether Grok Build can be the grok executor at all, through the proxy and under the safety rings, replacing the fallback, the default runtime, or both; which one is a product decision recorded with the findings.)

**What is already known** (read from the installed binary and its user guide):

- `grok -p <prompt> --output-format streaming-messages-json` emits the Anthropic Messages wire format: `system/init` (session id, tools, skills, MCP servers), `assistant` (text, thinking, tool_use blocks, `parent_tool_use_id`), `user` (tool_result blocks with `tool_use_id`, `is_error`), `result` (`total_cost_usd`, `usage`, `modelUsage`, `num_turns`, `duration_ms`). This is the shape `SdkAgentExecutor` already parses.
- Permission flags map onto the rings: `--permission-mode bypassPermissions` with `--deny` rules still enforced ("deny wins over always-approve"), `--tools` allowlist, and a `PreToolUse` hook that denies with `{"decision": "deny"}` on stdout or exit code 2. Hooks fail open on crash or timeout, so the hard boundary is `--deny` plus `--tools`; the hook is the policy's advisory layer, exactly the reverse of today's assumption. Ring 3 is 41 regexes that `--deny` globs cannot express, so under Grok Build the regex policy runs in a hook that fails open; that change is recorded as a decision (ADR 0010) before any swap, not assumed by the spike. Grok Build's tool names and input keys also differ (`run_terminal_command{command}`, `read_file{path}`), so a hook written against Claude's names measures nothing.
- Credentials: `GROK_AUTH_PROVIDER_COMMAND` runs a binary that returns the bearer. `GROK_CLI_CHAT_PROXY_BASE_URL` is the session-service base (Grok Build's own account, model-catalog and `/v1/responses` traffic), not the model API; API-key traffic uses `GROK_XAI_API_BASE_URL`, and a custom model entry in `config.toml` with `base_url` pointed at the proxy is the route that works; `api_backend = "messages"` reaches the proxy's `/v1/messages` offline but fails live because xAI's stream omits `index` on thinking deltas and Grok Build's parser aborts, so `api_backend = "responses"` (which the proxy forwards unchanged as `/v1/responses`) is the proven backend. Either way the sandbox-side credential is the per-sandbox proxy bearer and the xai-proxy swaps it for the SuperGrok token, so the credential stays out of the sandbox as today.
- Skills: reads `.claude/skills`, `.agents/skills`, `.grok/skills`, plugin skills, `[skills] paths` in config, and plugins listed in `[plugins] paths` + `enabled` (or under `$GROK_HOME/plugins/`); `--plugin-dir` exists only on `grok agent`, the ACP server, not on headless `grok -p`. Same SKILL.md frontmatter as Claude Code, so milestone 2's plugin sources apply unchanged.
- Sessions: a session is the directory `$GROK_HOME/sessions/<encoded-cwd>/<id>/` (`updates.jsonl` is authoritative, beside a dozen other files), `--resume <id>` is scoped to that cwd; the copy-out path moves one file today, so resume needs a directory (or `updates.jsonl` alone, if the spike shows it suffices).
- MCP servers from `[mcp_servers.<name>]` in config.

**Procedure** (throwaway; nothing lands in `main` from the spike except the findings doc):

1. Build a scratch image from `embry0-sandbox` with the pinned Grok Build binary and `--no-auto-update`.
2. Start it on `sandbox-restricted` with a per-sandbox bearer enrolled on the xai-proxy, `GROK_HOME=/home/agent/.grok`, an auth-provider command that prints the bearer, the endpoint override pointing at the proxy.
3. Run a two-tool prompt (read a file, run a shell command) headless with the streaming format, `--permission-mode bypassPermissions`, `--tools` limited to the developer set, `--deny "Bash(rm *)"`, and a `PreToolUse` hook that denies `Bash(curl *)`.
4. Capture stdout, the session directory, and the proxy's access log.

**Acceptance criteria** (all must hold to proceed to the swap):

- The proxy saw the request with the SuperGrok token injected and the sandbox bearer stripped; the sandbox environment never contained the SuperGrok token.
- The captured stream parses with the Claude path's message handling after a thin adapter (NDJSON → the SDK's message classes or duck-typed equivalents), producing `tool_call`, `tool_result`, `turn_usage` and `cost_update` events under this contract.
- The hook deny and the `--deny` rule each blocked their command, visible in the stream as an error tool result, and the denied command did not run (the `rm` target still exists, no `curl` egress was seen). The hook is written against Grok Build's own tool names and input keys.
- `--resume <session id>` continues the conversation in a fresh container after copying the session directory (and, separately, `updates.jsonl` alone), with the same `session_id` in the resumed init line.
- The init message's `skills` lists a user-invocable skill from a plugin loaded through `[plugins] paths` + `enabled` in `$GROK_HOME/config.toml`.
- Cost: under a SuperGrok session token `result.total_cost_usd` is 0 ("unknown, not free"), so the run pins `-m grok-4.5` and green means every `assistant` line carries `message.usage` with input, output, cache-read and cache-creation tokens that `price_usage` can price, plus `result.usage`/`modelUsage`; the findings say which side-model calls the usage leaves out.

**Decision rule.** Green on all six: open "GrokBuildExecutor replaces DirectXaiExecutor" in this milestone, pin the binary in both sandbox Dockerfiles (image rebuild recipe in the PR's deploy notes), delete the hand-rolled loop. Any red: keep `DirectXaiExecutor` conforming to the contract (3.2) and record the blocker in the spike's findings document.

## 5. Testing

- **Contract tests.** Backend: `docs/contracts/events.json` matches the module; no literal `{"type": ...}` dict outside the contract module; every `EventType` has a constructor and a doc row. Frontend: the union matches the JSON.
- **Executor tests** (`tests/unit/agents/test_sdk_executor.py`): a `UserMessage` with a `ToolResultBlock` emits `tool_result` paired to the preceding `tool_call` (today's test asserts the dead `AssistantMessage` branch; it is rewritten); `parent_tool_use_id` yields `subagent_start`/`subagent_end`; top-level turn counting; `turn_usage` per assistant message with the priced delta; error events carry `message`, `code`, `node`, `run_id`; truncation markers. Each new test is shown failing on the pre-fix tree with its own assertion message, per CLAUDE.md.
- **Node tests**: `run_agent_node` writes the `running` trace row before exec and finalizes it; `node` on sandbox events equals the node id for generic-agent steps; `attempt` increments on re-entry.
- **Service tests**: `job_finished` on every terminal transition and on the orphan path; `job_failed` persisted; redaction applied to nested values (a registered secret inside `tool_input` and inside a list block is scrubbed); redaction exception drops the event; the persist-failed counter.
- **WebSocket tests** (`tests/unit/api/ws/test_streaming.py`): close after `job_finished`; replay-then-close for a finished job; `since_seq` replay unchanged.
- **Frontend hook tests**: reconnect with `since_seq` and no duplicates (today's test only checks that the buffer survives); gap detection triggers the REST fill; cost summation; pending nodes from the graph; node filter by `node`.
- **Component render tests**, which do not exist today: `StructuredView`, `ConversationView`, `RawView`, `AgentCard`, `ToolCallStream`, `TracesTable` rendered from a recorded fixture (`tests/fixtures/job-events/`), one fixture per job kind, generated by `scripts/job_event_fixtures.py` from the event constructors and graph builders and pinned to `docs/contracts/events.json` by `tests/unit/execution/test_job_event_fixtures.py`; a capture from a real run may replace them after deploy.
- **Prometheus**: the new counter's rule shown firing with a relaxed threshold.
- **Full suite** against a scratch Postgres per CLAUDE.md, failure sets diffed against `origin/main`.

## 6. Migration and deploy notes

- Migration 95: `job_logs` columns and indexes, `agent_id` dropped, `traces.result` accepts `running`. Existing rows keep NULL `run_id`/`node`/`event_type`; the REST endpoint returns the payload only, so the UI recognises legacy rows by shape and `normalizeWireEvent` maps them to contract names once, at the wire.
- The sandbox image must be rebuilt: `embry0/agents/executor.py`, `executor_xai.py`, `embry0/execution/events.py` and `embry0/sandbox/` change. The PR's deploy notes name the recipe from `docs/ops/deploy.md`.
- Old events already in `job_logs` carry the old `error` key and no `run_id`. The frontend's reducers accept `message ?? error` for the `error` type only, so old jobs keep rendering; no backfill.
- The executors renamed `tool_call.input` to `summary` (plus the full `tool_input`) and `tool_result.tool_use_id` to `tool_id`. #41 moved those fallbacks into `normalizeWireEvent`; reducers read contract names only, and a live dashboard keeps pairing results with calls across the deploy because the normalizer runs on every row.
- No API route changes; the WebSocket gains a close and the REST events endpoint is unchanged.

## 7. Issue breakdown (milestone "Trace fidelity")

| Issue | Scope |
|---|---|
| #33 | Event contract module, JSON mirror, contract tests, `docs/api.md` events section and stale route removal |
| #34 | Claude path: tool results, error shape, node/run id on every executor event |
| #35 | Subagent boundaries, top-level turn counting, `turn_usage`, `context_compacted` |
| #36 | Run id minting, running trace row, node id on invocations, `attempt` on node events, migration 95 |
| #37 | `job_finished`, WebSocket close, `job_failed` persisted, orphan path |
| #38 | Client replay cursor, dedupe, gap fill, no reconnect after terminal |
| #39 | Redaction on the event path, truncation markers, drop-on-scrub-failure, persist-failed counter and rule |
| #40 | QA status and done on the job stream; `pipeline_graph` for onboarding and QA jobs |
| #41 | Frontend adoption: union, reducers, views, traces filter, dead code removal, render tests from fixtures |
| #42 | Grok Build spike (throwaway), findings document |
| #43 | Conditional on #42: GrokBuildExecutor replaces DirectXaiExecutor; image pin; delete the hand-rolled loop |

#33 and #34 are the first PR and carry this spec. #35, #36, #37, #39 depend on #33. #38 depends on #37. #40 depends on #33. #41 depends on #33 through #40. #42 is independent; #43 depends on #42 and #34.
