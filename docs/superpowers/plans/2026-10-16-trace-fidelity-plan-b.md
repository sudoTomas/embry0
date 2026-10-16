# Trace fidelity, plan B: terminal event, reconnect, redaction, QA on the job stream

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every job ends with a `job_finished` event the WebSocket closes on, a reconnecting client rebuilds the same timeline with no duplicates and with gap detection, no secret reaches `job_logs`, and QA status plus the onboarding and QA pipeline graphs ride the job stream. Covers GitHub issues #37, #38, #39 and #40 of milestone "Trace fidelity".

**Architecture:** One module, `embry0/services/job_events.py`, owns the path from a producer to `job_logs` and the WebSocket bus (stamp, redact, persist, count, publish) and the `job_finished` emission that reads the job row after a terminal write; `IssueExecutor._broadcast_event` delegates to it, and the two producers that have no executor (the paused-job reaper and the boot orphan sweep) call it directly. Redaction runs twice: the sandbox executors scrub text before the constructors cap it, and the orchestrator scrubs every string value again before persisting. The WebSocket route closes with 1000 after `job_finished`, or straight after replay when the job is already terminal; the client keeps the last `event_seq`, reconnects with `?since_seq=`, drops replays it has seen, fills gaps from the REST log and never reconnects after the terminal event. QA state changes and the onboarding and QA graphs go through the parent stream writer like every other node event.

**Tech Stack:** Python 3.12, FastAPI WebSockets, asyncpg, `prometheus_client` behind `LoopMetrics`, promtool rule tests; frontend TypeScript 5.9, vitest 5, `tsc -b`.

**Spec:** `docs/superpowers/specs/2026-10-15-trace-fidelity-design.md` (sections 3.5, 3.6, 3.7, the `useJobEventStream` bullet of 3.8, 3.9, 5, 6). Plan A (`docs/superpowers/plans/2026-10-15-trace-fidelity-plan-a.md`, merged as #56) is the base: the contract module `embry0/execution/events.py`, its JSON mirror, the AST scan test, run ids, migration 95.

## Global Constraints

- Work in the worktree `<worktree-root>/trace-fidelity-b` on branch `feat/trace-fidelity-b`; never check out another branch in `<deploy-checkout>`.
- Commits follow `.claude/skills/commit-standard` (Conventional Commits, imperative subject ≤ 72 chars, body wrapped at 72, no AI co-author footer). Every commit message ends with `Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ`.
- A new test is shown failing on the pre-fix tree with its own assertion message before the fix is written. "Run: … Expected: FAIL" steps are not optional.
- Read the pytest summary line; `grep FAILED` is not a result.
- Unit tests that need Postgres use the `db_with_migrations` fixture (or `api_client`) and are marked `@pytest.mark.requires_postgres`; run them with `TEST_DATABASE_URL` set per CLAUDE.md against a throwaway database.
- **No migration.** `event_seq` is `job_logs.id` and `job_logs.event_type` (migration 95) already exists; the last migration stays 95 and `tests/unit/storage/test_migration_95.py::test_migration_95_is_the_last_entry` stays as it is.
- Every event is built through a constructor in `embry0/execution/events.py`; `tests/unit/execution/test_event_contract.py` refuses literal event dicts, `dict(type=...)` calls and `EventType.X` literals under `embry0/`. A contract change (one TypedDict field in Task 9) regenerates `docs/contracts/events.json` (`python -m embry0.execution.events --write-contract docs/contracts/events.json`), the `docs/api.md` table (`--render-markdown` between the markers) and `frontend/src/lib/types/events.ts` (`EVENT_FIELDS` row and interface), all pinned by tests.
- `embry0/execution/events.py` imports nothing beyond the standard library and structlog (it is baked into the sandbox image); redaction lives in `embry0/safety/redaction.py`, which the sandbox images also carry.
- Metrics: every metric is a method on `LoopMetrics` (`embry0/metrics/loop.py`), named `embry0_*`, counters end in `_total`, no label carries an identifier; a producer takes an optional `LoopMetrics` and `tests/unit/metrics/test_lifespan_wiring.py` pins that production passes it. A Prometheus rule on a counter uses `or vector(0)` and its promtool test shows it firing; a label-less `sum()` is never joined with `and on(...)` (#371).
- The AST scan in `tests/unit/execution/test_event_contract.py` and the terminal-write guards in `tests/unit/services/` must stay green at the end of every task.
- Nothing in this tree names a customer, deployment, person, host, cost or job id. No time estimates anywhere.
- The sandbox images must be rebuilt after this plan (`embry0/agents/`, `embry0/sandbox/runner.py`, `embry0/execution/events.py`, `embry0/safety/redaction.py` change); the PR's deploy notes name the recipe in `docs/ops/deploy.md`, "Images: registry, pins and rebuilds".

## Decisions where the spec and the code disagree

The research for this plan (verbatim code, every terminal write site, every reader of the wire) found twelve places where the spec assumes something the tree does not do. Each decision below is binding for the tasks; the task that touches the area also amends the spec sentence so the spec stays true.

1. **Two terminal transitions have no executor in scope.** `ContainerReaper._expire_paused_jobs` (`embry0/execution/image_manager.py`) and the boot orphan sweep (`embry0/api/app.py`, before `_init_app_state`) write terminal statuses with no bus and no `IssueExecutor`. Decision: the stamp/redact/persist/count/publish core of `_broadcast_event` moves to `embry0/services/job_events.py::broadcast_job_event(jobs, bus, metrics, job_id, event)` and `emit_job_finished(jobs, bus, metrics, job_id)`; the executor delegates; the reaper gets an `on_job_finished` callback wired after `_init_app_state`; the boot sweep calls `emit_job_finished` with `bus=None` (no client can be connected before the server listens; the replay serves it). The synthetic `node_completed(action="orphaned")` marker is replaced by `job_finished`.
2. **`job_finished` fields are not in scope at every site.** `total_cost_usd` is never written on onboard paths, `pr_url` is deliberately absent on the duplicate-skip path (#474), `_handle_workflow_result` computes the cost inline. Decision: `emit_job_finished` reads the job row after the terminal write (`jobs.get(job_id)`) and builds the event from `status`, `error_code`, `error_message`, `pr_url`, `total_cost_usd`. One query per job end. A row whose status is not terminal is logged and skipped, never emitted.
3. **Every terminal write is followed by `_announce_finished`.** The existing AST guard (`tests/unit/services/test_outcomes_credential_uses.py`) proves every `self._jobs.update(..., status=<terminal>)` is followed by the credential-ledger stamp; a second guard with all six terminal statuses requires `await self._announce_finished(job_id)` in the same block after the write, and after every `self._jobs.fail_jobs(...)` call.
4. **Per-sandbox bearers are not registered with the scrubber** (spec 3.6 says they already are), and `known_secret_values()` excludes the orchestrator's own env secrets. Decision: `SandboxManager` registers the bearer right after `enroll_sandbox` returns it (create and resume), `_precheck_llm_accounts` registers the job's resolved LLM secrets (covers env-fallback credentials), and every scrub uses `secret_env_values()` (env plus registered), as every existing caller does.
5. **Redact before cap, with `events.py` kept pure.** The constructors cap `text`, `thinking`, `tool_result.content` and `agent_completed.output` at construction, so a scrub at the sandbox writer would see a cut token. Decision: both executors scrub those four values with `redact_secrets(value, secret_env_values())` before calling the constructor (one scrubber per run, built once), and the sandbox writer `embry0/sandbox/runner.py::_emit` scrubs every string value of the event again (second pass, catches `tool_input` and anything a constructor did not cap). `events.py` imports nothing new.
6. **`agent_completed.output` keeps the tail**, as the spec table says; `_capped` gains `keep="head" | "tail"` and only `output` uses `tail`. `tool_input` string values above 64 KiB are cut with an in-string marker so the contract shape does not change.
7. **QA `done` has no contract type.** Decision: no new type. The QA orchestrator wrapper emits `node_started(node="orchestrate_qa")` before the implementation and `node_completed(node="orchestrate_qa", action=<overall_status>)` after; `init_orchestrator` and `qa_report` emit their own `node_started`/`node_completed`; `job_finished` is the run-level terminal. `qa_subtask_status` goes through the parent writer at both bus sites; its `boot_phase` becomes a dict in the contract (the payload is a dict today). The QA bus and SSE route are unchanged.
8. **WebSocket close.** The route checks `type == "job_finished"` before the `?event_types=` filter, so a filtering client still gets the close. After replay, the route closes with 1000 when the replay carried `job_finished` or the job row's status is terminal, which also covers jobs finished before this plan and a `job_finished` whose persist failed. The client treats close code 1000 as "the server finished", marks the stream complete and does not reconnect.
9. **Frontend types.** `LogEvent` (the legacy union) gains `event_seq?`/`job_id?` on its base and a `JobFinishedEvent` variant; `fetchJobLogEvents(jobId, sinceSeq = 0)` gains the cursor. The union swap stays #41.
10. **`job_failed` is persisted** by scheduling `self._broadcast_event(...)` as a tracked task from the done-callback. It does not imply `job_finished`: the runner already wrote the terminal status in the common path, and a job whose row still says `running` after a crash is the boot sweep's job, as today.
11. **Metrics wiring.** `IssueExecutor` gains `metrics: LoopMetrics | None = None`; the lifespan passes `app.state.loop_metrics` and the wiring test pins it. The counter `embry0_job_events_persist_failed_total` has no labels. The rule file is added to the promtool command in `.github/workflows/ci.yml` and `docs/ops/verification.md`, and a test asserts every `*.rules.test.yml` under `infra/prometheus/` is named there.
12. **Spec 3.9's "a malformed line is wrapped as `system`, as today"** is not today's behaviour (`AgentRunner` drops it and logs `event_parse_failed`). Wrapping is not in #37 to #40; Task 7 corrects the spec sentence to describe the drop. The "redaction exception drops the event" rule of 3.9 is implemented in Task 7.

## Review Focus

Input classes the spec implies but no existing test exercises, most likely to bite first. Each is pinned in the task named.

1. **A `job_finished` whose persist fails**: live clients still receive it and the socket closes; a client reconnecting later gets the close after replay because the job row is terminal. Pinned in Task 4.
2. **A client with `?event_types=progress`** (a filter that excludes `job_finished`) must still have its socket closed when the job ends. Pinned in Task 4.
3. **A registered secret nested two levels down** (`tool_input.env[0].value`) and one inside a list of content blocks must be scrubbed before persist. Pinned in Task 7.
4. **A gap fill whose REST page also contains the live event that revealed the gap** must deliver each event once, in sequence order, and events arriving while the fill is in flight must wait behind it. Pinned in Task 5.
5. **A boot orphan with no bus and no metrics** (the lifespan before `_init_app_state`) must still persist `job_finished` with the row's error code. Pinned in Task 3.

---

## File structure

| File | Responsibility |
|---|---|
| `embry0/services/job_events.py` (new) | `broadcast_job_event` (stamp, redact, persist, count, publish, persist-failed counter) and `emit_job_finished` (job row to `job_finished`) |
| `embry0/services/_executor_outcomes.py` (edit) | `_broadcast_event` delegates; `_announce_finished` after each terminal write |
| `embry0/services/_executor_run.py`, `embry0/services/issue_executor.py` (edit) | `_announce_finished` after each terminal write; `metrics` kwarg; `job_failed` through `_broadcast_event`; LLM secrets registered at job start |
| `embry0/storage/repositories/jobs.py` (edit) | `TERMINAL_JOB_STATUSES` exported |
| `embry0/execution/image_manager.py` (edit) | `ContainerReaper.on_job_finished` |
| `embry0/api/app.py` (edit) | boot sweep emits `job_finished`; executor gets `metrics`; reaper callback wired |
| `embry0/api/ws/streaming.py` (edit) | close 1000 after `job_finished` or for a terminal job after replay |
| `embry0/metrics/loop.py` (edit) | `job_event_persist_failed()` |
| `infra/prometheus/job-events.rules.yml`, `job-events.rules.test.yml` (new) | the persist-failed alert and its promtool test |
| `.github/workflows/ci.yml`, `docs/ops/verification.md`, `docs/ops/metrics.md` (edit) | the rule file in the promtool list; the counter documented |
| `embry0/safety/redaction.py` (edit) | `redact_event` (recursive string scrub of one event) |
| `embry0/agents/_common.py` (edit) | `make_scrubber()` |
| `embry0/agents/executor.py`, `embry0/agents/executor_xai.py` (edit) | scrub the four text fields before the constructors |
| `embry0/sandbox/runner.py` (edit) | `_emit` scrubs every string value |
| `embry0/execution/events.py` (edit) | `_capped(keep=)`, output tail, `tool_input` string cap, `boot_phase` dict |
| `embry0/execution/sandbox_manager.py` (edit) | register the per-sandbox bearer |
| `embry0/orchestration/nodes/agent.py` (edit) | `result_summary` redacted before its cap |
| `embry0/workflows/qa/orchestrator_helpers.py`, `subtask_nodes.py`, `orchestrator.py`, `orchestrator_report.py` (edit) | `qa_subtask_status` on the job stream; `node_started`/`node_completed` for the three QA nodes |
| `embry0/workflows/pipeline_graphs.py` (new) | static onboarding and QA pipeline graphs in the frontend shape |
| `embry0/workflows/onboard/nodes.py`, `embry0/workflows/qa/orchestrator.py` (edit) | emit `pipeline_graph` from the first node |
| `frontend/src/hooks/useJobEventStream.ts` (rewrite) | cursor, dedupe, gap fill, `job_finished`, close 1000 |
| `frontend/src/api/logs.ts`, `frontend/src/lib/types/events-discriminated.ts`, `frontend/src/lib/types/events.ts` (edit) | `since_seq` parameter; `event_seq`, `job_id`, `JobFinishedEvent` on the legacy union |
| `frontend/src/hooks/__tests__/useJobEventStream.test.ts` (new) | the reconnect, dedupe, gap and terminal tests |
| `docs/contracts/events.json`, `docs/api.md`, `frontend/src/lib/types/events.ts` (regenerate) | the `boot_phase` widening |
| `docs/superpowers/specs/2026-10-15-trace-fidelity-design.md` (edit) | sentences in 3.5, 3.6, 3.7, 3.9 corrected by the decisions above |

## Execution waves (for a controller running tasks in parallel)

Tasks whose files are disjoint and whose interfaces do not depend on each other can run in one wave, each in its own worktree:

- Wave 1: Task 1 (core module, metrics), Task 5 (frontend hook), Task 6 (sandbox-side redaction and truncation), Task 9 (QA status on the job stream).
- Wave 2: Task 2 (terminal sites, needs Task 1), Task 3 (reaper and boot sweep, needs Task 1), Task 4 (WebSocket close and `job_failed`, needs Task 1's `TERMINAL_JOB_STATUSES`), Task 7 (orchestrator-side redaction, needs Tasks 1 and 6), Task 8 (rule and docs, needs Task 1's counter name), Task 10 (pipeline graphs, needs Task 9's `node_started` in `init_orchestrator_node`).
- Wave 3: Task 11 (verification and the PR).

Tasks 6 and 9 both touch `embry0/execution/events.py` in different regions and both regenerate the three mirrors; the controller regenerates the mirrors once after the wave. Tasks 2, 3, 4 and 7 all touch the executor service or its tests in disjoint functions; Task 7 and Task 2 both edit `embry0/services/_executor_run.py` (different functions).

---

### Task 1: The job-events core and the persist-failed counter

**Files:**
- Create: `embry0/services/job_events.py`
- Modify: `embry0/services/_executor_outcomes.py:957-993` (`_broadcast_event` delegates)
- Modify: `embry0/services/issue_executor.py` (constructor: `metrics` kwarg; `_announce_finished` method bound next to `_broadcast_event`)
- Modify: `embry0/storage/repositories/jobs.py:91-103` (export `TERMINAL_JOB_STATUSES`)
- Modify: `embry0/metrics/loop.py` (counter + method)
- Modify: `embry0/api/app.py:810-820` (pass `metrics=app.state.loop_metrics`)
- Create: `tests/unit/services/test_job_events.py`
- Modify: `tests/unit/metrics/test_loop.py`, `tests/unit/metrics/test_lifespan_wiring.py:114-125`
- Modify: `tests/unit/services/test_issue_executor.py:1-120` (the four `_broadcast_event` tests keep passing; one gains a metrics assertion)

**Interfaces:**
- Produces: `embry0.services.job_events.broadcast_job_event(jobs, bus, metrics, job_id, event) -> dict[str, Any] | None` (returns the event as published, `None` when dropped); `embry0.services.job_events.emit_job_finished(jobs, bus, metrics, job_id) -> None`; `IssueExecutor._announce_finished(job_id) -> None`; `IssueExecutor(metrics=...)` stored as `self._metrics`; `LoopMetrics.job_event_persist_failed()`; `embry0.storage.repositories.jobs.TERMINAL_JOB_STATUSES: frozenset[str]`.
- Consumes: `events.job_finished(...)` from the contract module; `JobsRepository.append_log_event`, `increment_cost`, `get`; `EventBus.publish`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/services/test_job_events.py`:

```python
"""The one path from a producer to job_logs and the bus, and the job_finished emission (spec 3.5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from prometheus_client import CollectorRegistry

from embry0.api.events.bus import EventBus
from embry0.metrics import LoopMetrics
from embry0.services.job_events import broadcast_job_event, emit_job_finished


def _jobs(*, seq: int | None = 7, row: dict | None = None, persist_error: Exception | None = None) -> MagicMock:
    jobs = MagicMock()
    if persist_error is not None:
        jobs.append_log_event = AsyncMock(side_effect=persist_error)
    else:
        jobs.append_log_event = AsyncMock(return_value=seq)
    jobs.increment_cost = AsyncMock()
    jobs.get = AsyncMock(return_value=row)
    return jobs


@pytest.mark.asyncio
async def test_broadcast_stamps_job_id_timestamp_and_seq_and_publishes() -> None:
    jobs = _jobs(seq=42)
    bus = EventBus()
    queue = await bus.subscribe("job-1")
    out = await broadcast_job_event(jobs, bus, None, "job-1", {"type": "progress", "message": "hi"})
    assert out is not None and out["event_seq"] == 42 and out["job_id"] == "job-1" and out["timestamp"]
    assert queue.get_nowait() == out
    persisted = jobs.append_log_event.await_args.args[1]
    assert persisted["job_id"] == "job-1" and "event_seq" not in persisted, "the seq comes from the row id, not the payload"


@pytest.mark.asyncio
async def test_broadcast_without_a_bus_only_persists() -> None:
    jobs = _jobs(seq=1)
    out = await broadcast_job_event(jobs, None, None, "job-1", {"type": "system", "message": "boot"})
    assert out is not None and out["event_seq"] == 1
    jobs.append_log_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_failure_counts_and_still_publishes_without_a_seq() -> None:
    registry = CollectorRegistry()
    metrics = LoopMetrics(registry)
    jobs = _jobs(persist_error=RuntimeError("db down"))
    bus = EventBus()
    queue = await bus.subscribe("job-1")
    out = await broadcast_job_event(jobs, bus, metrics, "job-1", {"type": "progress", "message": "hi"})
    assert out is not None and "event_seq" not in out
    assert queue.get_nowait()["type"] == "progress"
    assert registry.get_sample_value("embry0_job_events_persist_failed_total") == 1, (
        "a failed persist must be visible in Prometheus (spec 3.5)"
    )


@pytest.mark.asyncio
async def test_cost_update_increments_the_job_total_once() -> None:
    jobs = _jobs()
    await broadcast_job_event(jobs, None, None, "job-1", {"type": "cost_update", "cost_usd": 0.25})
    await broadcast_job_event(jobs, None, None, "job-1", {"type": "turn_usage", "cost_usd": 0.10})
    assert [c.args for c in jobs.increment_cost.await_args_list] == [("job-1", 0.25)], (
        "only cost_update adds to jobs.total_cost_usd (spec 3.3)"
    )


@pytest.mark.asyncio
async def test_emit_job_finished_reads_the_row_and_publishes_the_terminal_event() -> None:
    row = {
        "job_id": "job-1",
        "status": "failed",
        "error_code": "ERR_AGENT_TIMEOUT",
        "error_message": "the agent timed out",
        "pr_url": None,
        "total_cost_usd": 1.25,
    }
    jobs = _jobs(seq=9, row=row)
    bus = EventBus()
    queue = await bus.subscribe("job-1")
    await emit_job_finished(jobs, bus, None, "job-1")
    event = queue.get_nowait()
    assert event["type"] == "job_finished", "a terminal transition must emit job_finished (spec 3.5)"
    assert event["status"] == "failed" and event["error_code"] == "ERR_AGENT_TIMEOUT"
    assert event["error_message"] == "the agent timed out" and event["pr_url"] is None
    assert event["total_cost_usd"] == 1.25 and event["event_seq"] == 9


@pytest.mark.asyncio
async def test_emit_job_finished_skips_a_row_that_is_not_terminal() -> None:
    jobs = _jobs(row={"job_id": "job-1", "status": "running"})
    bus = EventBus()
    queue = await bus.subscribe("job-1")
    await emit_job_finished(jobs, bus, None, "job-1")
    assert queue.qsize() == 0, "job_finished must never describe a job that is still running"
    jobs.append_log_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_emit_job_finished_never_raises() -> None:
    jobs = _jobs()
    jobs.get = AsyncMock(side_effect=RuntimeError("db down"))
    await emit_job_finished(jobs, None, None, "job-1")  # must not raise
    jobs.append_log_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_executor_broadcast_delegates_and_announce_finished_uses_its_repos() -> None:
    from embry0.services.issue_executor import IssueExecutor

    jobs = _jobs(seq=3, row={"job_id": "job-1", "status": "completed", "error_code": None, "error_message": None, "pr_url": "https://example.invalid/pr/1", "total_cost_usd": 0.5})
    bus = EventBus()
    queue = await bus.subscribe("job-1")
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = jobs
    ex._event_bus = bus
    await ex._announce_finished("job-1")
    event = queue.get_nowait()
    assert event["type"] == "job_finished" and event["pr_url"] == "https://example.invalid/pr/1"
    assert event["event_seq"] == 3


def test_executor_constructor_stores_metrics() -> None:
    from embry0.services.issue_executor import IssueExecutor

    metrics = LoopMetrics(CollectorRegistry())
    ex = IssueExecutor(
        issues_repo=MagicMock(),
        jobs_repo=MagicMock(),
        traces_repo=MagicMock(),
        workflow_registry=MagicMock(),
        database_url="postgres://stub",
        metrics=metrics,
    )
    assert ex._metrics is metrics, "IssueExecutor(metrics=...) must store the facade as _metrics"
```

Append to `tests/unit/metrics/test_loop.py`:

```python
def test_job_event_persist_failed_counts() -> None:
    m, registry = _metrics()
    m.job_event_persist_failed()
    m.job_event_persist_failed()
    assert registry.get_sample_value("embry0_job_events_persist_failed_total") == 2
```

In `tests/unit/metrics/test_lifespan_wiring.py::test_the_lifespan_hands_the_facade_to_every_producer`, add after the `traces_repo` line:

```python
    assert booted.state.issue_executor._metrics is metrics, "the executor counts persist failures (plan B Task 1)"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/services/test_job_events.py tests/unit/metrics/test_loop.py -q 2>&1 | tail -5`
Expected: `test_job_events.py` errors at collection (`ModuleNotFoundError: embry0.services.job_events`); `test_job_event_persist_failed_counts` FAILS with `AttributeError: 'LoopMetrics' object has no attribute 'job_event_persist_failed'`. The wiring test needs Postgres; run it in Step 5.

- [ ] **Step 3: Write the module, the counter and the export**

In `embry0/storage/repositories/jobs.py`, right after the `_TERMINAL_STATUSES` frozenset (line ~103), add:

```python
#: The statuses a live job ends in. Public so the services layer can ask
#: "is this row terminal?" with the repository's own answer (plan B).
TERMINAL_JOB_STATUSES: frozenset[str] = _TERMINAL_STATUSES
```

`embry0/services/job_events.py`:

```python
"""Job events: the one path from a producer to job_logs and the WebSocket bus.

``broadcast_job_event`` stamps ``job_id`` and ``timestamp``, persists the
event (the row id becomes ``event_seq``), adds a ``cost_update`` delta to
``jobs.total_cost_usd`` and publishes to the bus. ``IssueExecutor``
delegates its ``_broadcast_event`` here; the paused-job reaper and the boot
orphan sweep, which have no executor, call it directly (they pass
``bus=None`` when nothing can be subscribed yet, so the event is persisted
and replayed, not lost).

``emit_job_finished`` is spec 3.5's terminal event: after a terminal status
write, read the job row and broadcast ``job_finished`` built from it. The
row is the one source every site has in scope; a row that is not terminal is
never announced.

Both functions are best-effort: a failure here is logged and must not fail
the job transition that called them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from embry0.execution import events
from embry0.storage.repositories.jobs import TERMINAL_JOB_STATUSES

logger = structlog.get_logger(__name__)


class EventPublisher(Protocol):
    async def publish(self, job_id: str, event: dict[str, Any]) -> None: ...


class JobEventStore(Protocol):
    async def append_log_event(self, job_id: str, event: dict[str, Any]) -> int: ...
    async def increment_cost(self, job_id: str, cost_delta: float) -> None: ...
    async def get(self, job_id: str) -> dict[str, Any] | None: ...


async def broadcast_job_event(
    jobs: JobEventStore,
    bus: EventPublisher | None,
    metrics: Any,
    job_id: str,
    event: dict[str, Any],
) -> dict[str, Any] | None:
    """Stamp, persist, count and publish one event. Returns the event as
    published (with ``event_seq`` when the persist succeeded)."""
    event = {**event, "job_id": job_id}
    if "timestamp" not in event:
        event = {**event, "timestamp": datetime.now(UTC).isoformat()}

    seq: int | None = None
    try:
        seq = await jobs.append_log_event(job_id, event)
    except Exception:
        # Published anyway (a live client still sees it) but no client can
        # replay it; the counter is what makes that visible (spec 3.5).
        logger.warning("event_persist_failed", job_id=job_id, event_type=event.get("type"), exc_info=True)
        if metrics is not None:
            metrics.job_event_persist_failed()
    if seq is not None:
        event = {**event, "event_seq": seq}

    # cost_update carries the run's delta; it is the only event that moves
    # jobs.total_cost_usd (spec 3.3: turn_usage is an estimate, never added).
    if event.get("type") == "cost_update" and event.get("cost_usd"):
        try:
            await jobs.increment_cost(job_id, event["cost_usd"])
        except Exception:
            logger.warning("cost_update_failed", job_id=job_id, exc_info=True)

    if bus is not None:
        await bus.publish(job_id, event)
    return event


async def emit_job_finished(jobs: JobEventStore, bus: EventPublisher | None, metrics: Any, job_id: str) -> None:
    """Broadcast ``job_finished`` for a job whose terminal status was just written.

    Reads the row rather than taking fields, because no two terminal sites
    have the same fields in scope (cost is resolved inline in one, pr_url is
    deliberately absent in another). Never raises.
    """
    try:
        row = await jobs.get(job_id)
    except Exception:
        logger.warning("job_finished_event_row_fetch_failed", job_id=job_id, exc_info=True)
        return
    if not row:
        logger.warning("job_finished_event_no_row", job_id=job_id)
        return
    status = str(row.get("status") or "")
    if status not in TERMINAL_JOB_STATUSES:
        logger.warning("job_finished_event_skipped_non_terminal", job_id=job_id, status=status)
        return
    try:
        await broadcast_job_event(
            jobs,
            bus,
            metrics,
            job_id,
            events.job_finished(
                status=status,
                error_code=row.get("error_code"),
                error_message=row.get("error_message"),
                pr_url=row.get("pr_url"),
                total_cost_usd=float(row.get("total_cost_usd") or 0.0),
            ),
        )
    except Exception:
        logger.warning("job_finished_event_failed", job_id=job_id, exc_info=True)
```

Replace the body of `_broadcast_event` in `embry0/services/_executor_outcomes.py` (keep the function and its class binding):

```python
async def _broadcast_event(self: IssueExecutor, job_id: str, event: dict[str, Any]) -> None:
    """Forward event to WebSocket subscribers and persist to job_logs (embry0.services.job_events)."""
    await broadcast_job_event(
        self._jobs, getattr(self, "_event_bus", None), getattr(self, "_metrics", None), job_id, event
    )


async def _announce_finished(self: IssueExecutor, job_id: str) -> None:
    """spec 3.5: every terminal transition ends with job_finished. Called right
    after the terminal status write at every site (a guard test enforces it)."""
    await emit_job_finished(self._jobs, getattr(self, "_event_bus", None), getattr(self, "_metrics", None), job_id)
```

with `from embry0.services.job_events import broadcast_job_event, emit_job_finished` at the module top. In `embry0/services/issue_executor.py`, next to `_broadcast_event = _executor_outcomes._broadcast_event` (line ~1311) add `_announce_finished = _executor_outcomes._announce_finished`; in `__init__` add the keyword-only parameter `metrics: LoopMetrics | None = None` (import `LoopMetrics` under `TYPE_CHECKING` from `embry0.metrics`) and `self._metrics = metrics`. In `embry0/api/app.py` where `IssueExecutor(...)` is built (~line 810) pass `metrics=app.state.loop_metrics`.

In `embry0/metrics/loop.py`, in the jobs block of `__init__` after `self._agent_spend`:

```python
        self._job_event_persist_failures = Counter(
            "embry0_job_events_persist_failed_total",
            "Job events that could not be written to job_logs. Each was still published to live "
            "WebSocket clients but no reconnecting client can replay it (spec 3.5).",
            registry=r,
        )
```

and after `agent_spend`:

```python
    @_never_raises
    def job_event_persist_failed(self) -> None:
        self._job_event_persist_failures.inc()
```

Document it in `docs/ops/metrics.md`, Jobs table: `| \`embry0_job_events_persist_failed_total\` | counter | none |` with one sentence below the table: "A persist failure means a live client saw an event no reconnecting client can replay; `infra/prometheus/job-events.rules.yml` alerts on it (Task 8 adds the rule)."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/services/test_job_events.py tests/unit/services/test_issue_executor.py tests/unit/metrics/test_loop.py -q 2>&1 | tail -3`
Expected: all passed, 0 failed. Then `.venv/bin/ruff check embry0 tests && .venv/bin/ruff format --check embry0/services embry0/metrics tests/unit/services tests/unit/metrics && .venv/bin/mypy embry0/services/job_events.py embry0/services/_executor_outcomes.py embry0/services/issue_executor.py embry0/metrics/loop.py` clean.

- [ ] **Step 5: Run the Postgres-backed wiring test**

Run: `TEST_DATABASE_URL=... .venv/bin/python -m pytest tests/unit/metrics/test_lifespan_wiring.py -q 2>&1 | tail -3`
Expected: all passed (the executor carries the facade).

- [ ] **Step 6: Commit**

```bash
git add embry0/services/job_events.py embry0/services/_executor_outcomes.py embry0/services/issue_executor.py embry0/storage/repositories/jobs.py embry0/metrics/loop.py embry0/api/app.py docs/ops/metrics.md tests/unit/services/test_job_events.py tests/unit/metrics/test_loop.py tests/unit/metrics/test_lifespan_wiring.py
git commit -q -F - <<'EOF'
feat(services): one job-events path and the job_finished emitter

Two producers that end jobs have no executor (the paused-job reaper and
the boot orphan sweep), so the stamp/persist/count/publish core of
_broadcast_event moves to embry0.services.job_events where anything with
a jobs repository can call it. emit_job_finished reads the job row after
a terminal write and broadcasts the terminal event spec 3.5 requires; no
two terminal sites have the same fields in scope, the row does. A
persist failure now increments embry0_job_events_persist_failed_total,
and the executor carries the metrics facade like every other producer.

Refs: #37

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 2: `job_finished` on every executor-service terminal transition

**Files:**
- Modify: every terminal write site in `embry0/services/_executor_run.py`, `embry0/services/_executor_outcomes.py`, `embry0/services/issue_executor.py` (inventory below)
- Create: `tests/unit/services/test_job_finished_sites.py`

**Interfaces:**
- Consumes: `IssueExecutor._announce_finished(job_id)` from Task 1.
- Produces: a `job_finished` event after every terminal status write the executor service performs.

The sites (each one: after the `await self._finish_credential_uses(...)` that follows the status write, add `await self._announce_finished(job_id)`; where a site writes several branches, each branch gets one):

| Site | File:lines (pre-change) | Status written |
|---|---|---|
| `_refuse_if_over_budget` | `_executor_run.py:182-193` | `failed` (budget refusal) |
| `_run_onboard_workflow` | `_executor_run.py:284-324`, four branches | `failed` ×3, `completed` |
| `_run_workflow` | `_executor_run.py:979-1001`, two branches | `completed` (no state), `failed` (exception) |
| `_skip_as_duplicate` | `_executor_run.py:1172-1179` | `skipped` |
| `_run_qa_workflow` | `_executor_run.py:1344-1473`, three branches | `failed`, `completed`/`failed`/`partial`, `failed` |
| `_handle_workflow_result` | `_executor_outcomes.py:741-760` | `completed` or `failed` |
| `_handle_split` | `_executor_outcomes.py:923` | `completed` |
| `_handle_needs_info` | `_executor_outcomes.py:1037-1047`, `:1203` | `failed`, `completed` |
| `cancel_job` | `issue_executor.py:997-1007` | `cancelled` |
| `_fail_resume` | `issue_executor.py:1141-1178` | `failed` |
| `_relaunch` (`fail_jobs`, bulk) | `issue_executor.py:1231-1236` | `failed` ORPHANED, several ids |

For `_relaunch`: `fail_jobs(...)` returns the failed ids; add `for failed_id in failed_ids: await self._announce_finished(failed_id)` right after the call (check `fail_jobs`'s return in `embry0/storage/repositories/jobs.py`; if it returns nothing, use the ids you passed in).

- [ ] **Step 1: Write the failing tests**

```python
"""Every terminal status write the executor service performs is followed by job_finished (spec 3.5)."""

from __future__ import annotations

import ast
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import embry0.services._executor_outcomes as _executor_outcomes
import embry0.services._executor_run as _executor_run
import embry0.services.issue_executor as issue_executor
from embry0.services.issue_executor import IssueExecutor

_TERMINAL = {"completed", "failed", "cancelled", "partial", "expired", "skipped"}


def _order_recording_executor():
    order: list[tuple] = []
    ex = IssueExecutor.__new__(IssueExecutor)

    async def _update(job_id, **kw):
        order.append(("update", kw.get("status")))

    async def _finish(job_id, *, error_code):
        order.append(("finish", error_code))

    async def _announce(job_id):
        order.append(("announce", job_id))

    ex._jobs = MagicMock(update=AsyncMock(side_effect=_update), get=AsyncMock(return_value=None))
    ex._finish_credential_uses = AsyncMock(side_effect=_finish)
    ex._announce_finished = AsyncMock(side_effect=_announce)
    return ex, order


async def test_split_completed_announces_after_the_stamp():
    ex, order = _order_recording_executor()
    ex._issues = MagicMock(
        get=AsyncMock(return_value={"priority": "low", "labels": [], "repo": "o/r"}),
        create=AsyncMock(return_value="child-1"),
        update=AsyncMock(),
        update_parent_status=AsyncMock(),
    )
    ex._emit_audit = AsyncMock()
    ex._db = None
    ex._audit_log_path = None
    await ex._handle_split("iss-1", "job-1", {"sub_tasks": [{"task": "a"}]})
    assert order == [("update", "completed"), ("finish", None), ("announce", "job-1")], order


async def test_needs_info_zero_questions_announces_the_failure():
    ex, order = _order_recording_executor()
    ex._issues = MagicMock(get=AsyncMock(return_value={"id": "iss-1"}), update=AsyncMock())
    await ex._handle_needs_info("iss-1", "job-1", {"questions": [], "asking_node": "triage"})
    assert order == [("update", "failed"), ("finish", "ERR_EMPTY_INTERRUPT"), ("announce", "job-1")], order


async def test_cancel_job_announces_the_cancellation(monkeypatch):
    ex, order = _order_recording_executor()
    ex._background_tasks = set()
    ex._tasks_by_job = {}
    ex._event_bus = None
    ex._database_url = "postgres://stub"
    ex._audit_log_path = None
    ex._db = MagicMock()
    ex._issues = MagicMock(update=AsyncMock())
    ex._sandbox = None
    ex._cleanup_job_volumes = AsyncMock()
    import embry0.orchestration.checkpoint as ckpt_mod

    monkeypatch.setattr(ckpt_mod, "purge_thread", AsyncMock())
    with patch.object(issue_executor, "emit_audit", new=AsyncMock()):
        await ex.cancel_job("job-1")
    assert order == [("update", "cancelled"), ("finish", "cancelled"), ("announce", "job-1")], order


# --- structural guard: no terminal write escapes the announcement ---------------


def _is_terminal_job_update(node: ast.AST) -> bool:
    """`await self._jobs.update(..., status=<terminal literal | variable>)`."""
    if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Await)):
        return False
    call = node.value.value
    if not (isinstance(call, ast.Call) and ast.unparse(call.func) == "self._jobs.update"):
        return False
    for kw in call.keywords:
        if kw.arg == "status":
            if isinstance(kw.value, ast.Constant):
                return kw.value.value in _TERMINAL
            return isinstance(kw.value, ast.Name)
    return False


def _is_bulk_fail(node: ast.AST) -> bool:
    """`... = await self._jobs.fail_jobs(...)` or `await self._jobs.fail_jobs(...)`."""
    value = node.value if isinstance(node, (ast.Expr, ast.Assign)) else None
    call = value.value if isinstance(value, ast.Await) else None
    return isinstance(call, ast.Call) and ast.unparse(call.func) == "self._jobs.fail_jobs"


def _announces(block: list[ast.stmt]) -> bool:
    return any("self._announce_finished(" in ast.unparse(stmt) for stmt in block)


def test_every_terminal_status_write_is_followed_by_job_finished():
    missing = []
    found = 0
    for module in (_executor_outcomes, _executor_run, issue_executor):
        tree = ast.parse(inspect.getsource(module))
        for parent in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(parent, field, None)
                if not isinstance(block, list):
                    continue
                for i, stmt in enumerate(block):
                    if _is_terminal_job_update(stmt) or _is_bulk_fail(stmt):
                        found += 1
                        if not _announces(block[i + 1 :]):
                            missing.append(f"{module.__name__}:{stmt.lineno}")
    assert found >= 16, f"guard matched only {found} terminal writes; the matcher is broken"
    assert not missing, f"terminal job status writes not followed by _announce_finished: {missing}"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/services/test_job_finished_sites.py -q 2>&1 | tail -8`
Expected: the three order tests FAIL (no `("announce", ...)` entry) and the guard FAILS listing every site in the table above.

- [ ] **Step 3: Add the announcement at every site**

At each site in the table, immediately after the `await self._finish_credential_uses(...)` statement that follows the terminal write, add:

```python
        await self._announce_finished(job_id)
```

(`cancel_job` names the id `job_id` too; `_relaunch` loops over the failed ids as described above.) Where a site's `job_id` variable has another name, use that name; do not restructure the surrounding code.

- [ ] **Step 4: Run the suites**

Run: `.venv/bin/python -m pytest tests/unit/services -q 2>&1 | tail -3` (with Postgres for the few `requires_postgres` tests in that directory).
Expected: all passed. Existing order-recording tests in `test_outcomes_credential_uses.py` still pass because `_announce_finished` is a separate statement after the stamp. Existing tests that build an executor with `__new__` and reach a terminal write now call the real `_announce_finished`, which reads `self._jobs.get` (a `MagicMock` returns a non-awaitable `MagicMock` → the `await` raises `TypeError` → caught and logged by `emit_job_finished`'s `try`); if a test asserts on log output or on `get` call counts, set `ex._announce_finished = AsyncMock()` in that test. Then `.venv/bin/ruff check embry0/services tests/unit/services && .venv/bin/ruff format --check embry0/services tests/unit/services && .venv/bin/mypy embry0/services`.

- [ ] **Step 5: Commit**

```bash
git add embry0/services tests/unit/services/test_job_finished_sites.py tests/unit/services
git commit -q -F - <<'EOF'
feat(services): announce job_finished after every terminal write

No event ever marked a job finished, so the socket stayed open, Live
stayed lit and the client retried against a finished job. Every
terminal status write in the executor service is now followed by
_announce_finished, which reads the row and broadcasts job_finished; a
guard test walks the three modules and refuses a terminal write that is
not followed by it, with all six terminal statuses and the bulk
fail_jobs path in scope.

Refs: #37

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---
### Task 3: The reaper and the boot orphan sweep emit `job_finished`

**Files:**
- Modify: `embry0/execution/image_manager.py:433-509` (`ContainerReaper._expire_paused_jobs`; constructor)
- Modify: `embry0/api/app.py:1019-1050` (boot sweep), `~1211-1219` (reaper construction), after `_init_app_state` (reaper callback wiring)
- Modify: `tests/unit/execution/test_image_manager.py`, `tests/unit/api/test_credential_wiring.py`, `tests/unit/metrics/test_lifespan_wiring.py`
- Modify: `docs/superpowers/specs/2026-10-15-trace-fidelity-design.md` section 3.5 (the orphan sentence)

**Interfaces:**
- Consumes: `emit_job_finished(jobs, bus, metrics, job_id)` from Task 1; `IssueExecutor._announce_finished`.
- Produces: `ContainerReaper(..., on_job_finished: Callable[[str], Awaitable[None]] | None = None)` stored as a public attribute `on_job_finished` so the lifespan can set it after the executor exists.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/execution/test_image_manager.py` (same fakes as `test_expire_paused_jobs_uses_jobs_repo` at line 44: an `AsyncMock` db whose `fetch` returns the paused rows, an `AsyncMock` jobs repo, a `MagicMock` docker whose `run_cmd` raises):

```python
def _reaper_with_paused_jobs(job_ids: list[str]) -> tuple[ContainerReaper, AsyncMock]:
    mock_db = AsyncMock()
    mock_db.fetch = AsyncMock(return_value=[{"job_id": j} for j in job_ids])
    mock_db.fetchrow = AsyncMock(return_value=None)
    mock_jobs_repo = AsyncMock()
    mock_jobs_repo.update = AsyncMock()
    mock_docker = MagicMock()
    mock_docker.build_stop_cmd = MagicMock(return_value=["docker", "stop"])
    mock_docker.build_rm_cmd = MagicMock(return_value=["docker", "rm"])
    mock_docker.run_cmd = AsyncMock(side_effect=RuntimeError("no container"))
    reaper = ContainerReaper(DockerRuntime(mock_docker), db=mock_db, jobs_repo=mock_jobs_repo, paused_ttl_hours=0)
    return reaper, mock_jobs_repo


@pytest.mark.asyncio
async def test_expire_paused_jobs_announces_job_finished_for_each_expired_job() -> None:
    """spec 3.5: expiry is a terminal transition, so each expired job ends with job_finished."""
    announced: list[str] = []

    async def _on_finished(job_id: str) -> None:
        announced.append(job_id)

    reaper, jobs_repo = _reaper_with_paused_jobs(["job-a", "job-b"])
    reaper.on_job_finished = _on_finished
    await reaper._expire_paused_jobs()
    assert jobs_repo.update.await_count == 2
    assert sorted(announced) == ["job-a", "job-b"], "every expired job must be announced once"


@pytest.mark.asyncio
async def test_expire_paused_jobs_announcement_failure_does_not_stop_the_sweep() -> None:
    async def _boom(job_id: str) -> None:
        raise RuntimeError("bus down")

    reaper, jobs_repo = _reaper_with_paused_jobs(["job-a", "job-b"])
    reaper.on_job_finished = _boom
    await reaper._expire_paused_jobs()  # must not raise
    assert jobs_repo.update.await_count == 2, "the sweep still expires every job"


@pytest.mark.asyncio
async def test_expire_paused_jobs_without_a_callback_behaves_as_before() -> None:
    reaper, jobs_repo = _reaper_with_paused_jobs(["job-a"])
    await reaper._expire_paused_jobs()
    jobs_repo.update.assert_awaited_once_with("job-a", status="expired")
```

Append to `tests/unit/api/test_credential_wiring.py` next to `test_orphan_sweep_stamps_credential_uses` (same source-scanning style):

```python
def test_orphan_sweep_emits_job_finished_and_no_synthetic_node_marker() -> None:
    """spec 3.5: the boot orphan path emits job_finished through the job-events core,
    not a hand-made node_completed(action="orphaned")."""
    source = (REPO / "embry0" / "api" / "app.py").read_text(encoding="utf-8")
    start = source.index("fail_orphaned(")
    window = source[start : start + 3000]
    assert "emit_job_finished(" in window, "the orphan sweep must emit job_finished for every orphaned job"
    assert 'action="orphaned"' not in source, "the synthetic node_completed marker is replaced by job_finished"
```

(`REPO` is the module's existing repo-root constant; if the file names it differently, use that name.)

In `tests/unit/metrics/test_lifespan_wiring.py::test_the_lifespan_hands_the_facade_to_every_producer` add:

```python
    reaper = booted.state.container_reaper
    assert reaper.on_job_finished == booted.state.issue_executor._announce_finished, (
        "the paused-job reaper announces job_finished through the executor (plan B Task 3)"
    )
```

(If `app.state` stores the reaper under another attribute, read `app.py` ~1211 and use that name; if it is stored only in a local, store it on `app.state.container_reaper` as part of this task.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/execution/test_image_manager.py tests/unit/api/test_credential_wiring.py -q -k "announces or announcement or emits_job_finished" 2>&1 | tail -6`
Expected: the reaper tests FAIL (`AttributeError: on_job_finished` or `announced == []`), the source test FAILS ("must emit job_finished").

- [ ] **Step 3: The reaper**

In `ContainerReaper.__init__` add the keyword-only parameter and attribute:

```python
        on_job_finished: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        ...
        # spec 3.5: expiry is terminal, so each expired job is announced. Set
        # by the lifespan once the executor exists (the reaper is built first).
        self.on_job_finished = on_job_finished
```

In `_expire_paused_jobs`, right after each job's `expired` status write succeeds (the existing per-job try block), add:

```python
                if self.on_job_finished is not None:
                    try:
                        await self.on_job_finished(job_id)
                    except Exception:
                        logger.warning("job_finished_announce_failed", job_id=job_id, exc_info=True)
```

- [ ] **Step 4: The boot sweep and the wiring**

In `embry0/api/app.py` replace the synthetic-marker loop (lines ~2136-2143 of the dossier excerpt: the `for jid in orphaned_ids: synthetic = events.node_completed(...)` block) with:

```python
            # spec 3.5: the orphan path is a terminal transition. Nothing can be
            # subscribed before the server listens, so persist only (bus=None);
            # the WebSocket replay and the REST log serve it.
            from embry0.services.job_events import emit_job_finished

            boot_metrics = getattr(app.state, "loop_metrics", None)
            for jid in orphaned_ids:
                await emit_job_finished(boot_jobs_repo, None, boot_metrics, jid)
```

Remove the now-unused `events` import from `app.py` only if nothing else in the file uses it (`grep -n "events\." embry0/api/app.py`). After `_init_app_state(...)` returns in the lifespan, wire the reaper:

```python
    app.state.container_reaper.on_job_finished = app.state.issue_executor._announce_finished
```

(If the reaper is constructed after `_init_app_state`, pass `on_job_finished=app.state.issue_executor._announce_finished` to its constructor instead and drop the assignment.)

Amend spec section 3.5, the sentence "`job_finished` is emitted through `_broadcast_event` by the executor's outcome path for every terminal transition (…) and when a job is orphaned at startup": append "The paused-job reaper and the boot orphan sweep have no executor; they call the same job-events core directly (the boot sweep with no bus: nothing is subscribed before the server listens, so the event is persisted and replayed). The synthetic `node_completed(action="orphaned")` marker is gone; `job_finished` carries the orphan's error code."

- [ ] **Step 5: Run the suites**

Run: `.venv/bin/python -m pytest tests/unit/execution/test_image_manager.py tests/unit/api/test_credential_wiring.py -q 2>&1 | tail -3` then with Postgres `TEST_DATABASE_URL=... .venv/bin/python -m pytest tests/unit/metrics/test_lifespan_wiring.py tests/unit/storage/test_job_recovery.py -q 2>&1 | tail -3`.
Expected: all passed. `ruff check`, `ruff format --check` on the touched files, `mypy embry0/execution/image_manager.py embry0/api/app.py`.

- [ ] **Step 6: Commit**

```bash
git add embry0/execution/image_manager.py embry0/api/app.py tests/unit/execution/test_image_manager.py tests/unit/api/test_credential_wiring.py tests/unit/metrics/test_lifespan_wiring.py docs/superpowers/specs/2026-10-15-trace-fidelity-design.md
git commit -q -F - <<'EOF'
feat(api): job_finished from the paused-job reaper and the boot sweep

The two terminal transitions outside the executor service, a paused job
expiring and a job orphaned by a restart, ended with no event at all
(the boot path wrote a hand-made node_completed marker that lit the
wrong node). The reaper now announces through the executor once the
lifespan has built it, and the boot sweep persists job_finished for
every orphan through the job-events core, with the row's error code.

Refs: #37

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 4: `job_failed` persisted; the WebSocket closes on the terminal event

**Files:**
- Modify: `embry0/services/issue_executor.py:719-799` (`_track_task` done-callback)
- Modify: `embry0/api/ws/streaming.py` (whole route body after `accept`)
- Modify: `tests/unit/services/test_issue_executor.py` (`test_track_task_logs_and_publishes_on_failure`)
- Modify: `tests/unit/api/ws/test_streaming.py`

**Interfaces:**
- Consumes: `TERMINAL_JOB_STATUSES` from Task 1; `jobs_repo.get(job_id)`.
- Produces: the WebSocket closes with code 1000 after forwarding `job_finished`, or right after replay when the job is terminal; `job_failed` lands in `job_logs` with an `event_seq`.

- [ ] **Step 1: Write the failing tests**

Rewrite `test_track_task_logs_and_publishes_on_failure` in `tests/unit/services/test_issue_executor.py` so the executor has a `_jobs` and the assertions cover persistence:

```python
@pytest.mark.asyncio
async def test_track_task_logs_and_persists_job_failed_on_failure(capsys):
    """If the coroutine raises an unexpected exception, _track_task logs it AND
    routes a job_failed event through _broadcast_event, so it is persisted
    (replayable) as well as published (spec 3.7)."""
    from embry0.api.events.bus import EventBus
    from embry0.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._background_tasks = set()
    executor._tasks_by_job = {}
    executor._event_bus = EventBus()
    executor._jobs = MagicMock(append_log_event=AsyncMock(return_value=77), increment_cost=AsyncMock())

    queue = await executor._event_bus.subscribe("job-1")

    async def _boom() -> None:
        raise RuntimeError("kaboom")

    task = executor._track_task(_boom(), kind="workflow", job_id="job-1")
    try:
        await task
    except RuntimeError:
        pass
    # The done-callback schedules the broadcast as a tracked task; let it run.
    for _ in range(3):
        await asyncio.sleep(0)
    pending = [t for t in executor._background_tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending)

    captured = capsys.readouterr()
    assert "background_task_failed" in captured.out and "kaboom" in captured.out

    evt = queue.get_nowait()
    assert evt["type"] == "job_failed" and evt["job_id"] == "job-1" and "kaboom" in evt["message"]
    assert evt["event_seq"] == 77, "job_failed must be persisted before publish, like every other event"
    persisted = executor._jobs.append_log_event.await_args.args[1]
    assert persisted["type"] == "job_failed" and persisted["kind"] == "workflow"
```

Append to `tests/unit/api/ws/test_streaming.py`:

```python
class _FinishedJobRepo:
    """A job already finished: its log holds the terminal event, its row is terminal."""

    def __init__(self, rows: list[dict], status: str = "completed") -> None:
        self._rows = rows
        self._status = status

    async def get_log_events(self, job_id, stream="pipeline", limit=500, since_seq=0):
        return [r for r in self._rows if r["id"] > since_seq][:limit]

    async def get(self, job_id):
        return {"job_id": job_id, "status": self._status}


class _LiveRepo:
    """A running job whose first replay call injects live events into the bus."""

    def __init__(self, app, job_id: str, live: list[dict], rows: list[dict] | None = None) -> None:
        self._app = app
        self._job_id = job_id
        self._live = live
        self._rows = rows or []
        self._marked = False

    async def get_log_events(self, job_id, stream="pipeline", limit=500, since_seq=0):
        if not self._marked:
            self._marked = True
            for q in self._app.state.event_bus._subscribers[self._job_id]:
                for event in self._live:
                    q.put_nowait(event)
        return [r for r in self._rows if r["id"] > since_seq][:limit]

    async def get(self, job_id):
        return {"job_id": job_id, "status": "running"}


def test_ws_closes_with_1000_after_forwarding_job_finished():
    """spec 3.5: the route closes the socket after the terminal event."""
    app = _make_ws_app(auth_dev_mode=True)
    finished = {"type": "job_finished", "status": "completed", "event_seq": 5, "error_code": None, "error_message": None, "pr_url": None, "total_cost_usd": 0.0}
    app.state.jobs_repo = _LiveRepo(app, "job-live", live=[{"type": "progress", "event_seq": 4}, finished])
    client = TestClient(app)
    with client.websocket_connect("/ws/jobs/job-live/events") as ws:
        assert ws.receive_json()["type"] == "progress"
        assert ws.receive_json()["type"] == "job_finished"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1000, f"the server must close with 1000 after job_finished, got {exc.value.code}"


def test_ws_closes_after_job_finished_even_when_the_filter_excludes_it():
    """A client filtering on progress still has its socket closed when the job ends."""
    app = _make_ws_app(auth_dev_mode=True)
    finished = {"type": "job_finished", "status": "failed", "event_seq": 2, "error_code": "ERR_X", "error_message": "x", "pr_url": None, "total_cost_usd": 0.0}
    app.state.jobs_repo = _LiveRepo(app, "job-filt", live=[{"type": "progress", "event_seq": 1}, finished])
    client = TestClient(app)
    with client.websocket_connect("/ws/jobs/job-filt/events?event_types=progress") as ws:
        assert ws.receive_json()["type"] == "progress"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1000


def test_ws_replays_then_closes_for_a_job_that_already_finished():
    """A late joiner of a finished job gets the replay and then the close, no live wait."""
    app = _make_ws_app(auth_dev_mode=True)
    rows = [
        {"id": 1, "payload": {"type": "progress", "n": 1}},
        {"id": 2, "payload": {"type": "job_finished", "status": "completed", "error_code": None, "error_message": None, "pr_url": None, "total_cost_usd": 0.1}},
    ]
    app.state.jobs_repo = _FinishedJobRepo(rows)
    client = TestClient(app)
    with client.websocket_connect("/ws/jobs/job-done/events") as ws:
        assert ws.receive_json()["n"] == 1
        assert ws.receive_json()["type"] == "job_finished"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1000


def test_ws_closes_after_replay_when_the_row_is_terminal_but_no_job_finished_was_persisted():
    """Review focus 1: a finished job whose job_finished persist failed, or one that
    finished before the terminal event existed, still closes after replay."""
    app = _make_ws_app(auth_dev_mode=True)
    app.state.jobs_repo = _FinishedJobRepo([{"id": 1, "payload": {"type": "progress", "n": 1}}], status="failed")
    client = TestClient(app)
    with client.websocket_connect("/ws/jobs/job-old/events") as ws:
        assert ws.receive_json()["n"] == 1
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1000


def test_ws_since_seq_replays_only_newer_rows_and_drops_live_duplicates():
    """spec 5: since_seq replay and live dedupe (nothing pinned either before this plan)."""
    app = _make_ws_app(auth_dev_mode=True)
    rows = [{"id": i, "payload": {"type": "progress", "n": i}} for i in range(1, 6)]
    live = [{"type": "progress", "n": 5, "event_seq": 5}, {"type": "progress", "n": 6, "event_seq": 6}, {"type": "marker", "event_seq": 10**9}]
    app.state.jobs_repo = _LiveRepo(app, "job-seq", live=live, rows=rows)
    client = TestClient(app)
    seen = []
    with client.websocket_connect("/ws/jobs/job-seq/events?since_seq=3") as ws:
        while True:
            msg = ws.receive_json()
            if msg["type"] == "marker":
                break
            seen.append(msg["n"])
    assert seen == [4, 5, 6], f"replay must start after since_seq and the live copy of 5 must be dropped: {seen}"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/services/test_issue_executor.py::test_track_task_logs_and_persists_job_failed_on_failure tests/unit/api/ws/test_streaming.py -q -k "job_failed or closes or since_seq" 2>&1 | tail -8`
Expected: the `job_failed` test FAILS (`KeyError: 'event_seq'`); the four close tests FAIL (the client receive blocks on an open socket until the test client's timeout, or receives the marker instead of a disconnect; if a test hangs, that is the failure: note it and move on); the `since_seq` test PASSES already (it pins existing behaviour; keep it).

- [ ] **Step 3: `job_failed` through `_broadcast_event`**

In `_track_task`'s `_on_done`, replace the bus publish block with:

```python
            # spec 3.7: job_failed is persisted like every other event (it used
            # to bypass job_logs and reach only live clients). The callback is
            # synchronous, so the broadcast runs as a tracked task that a
            # clean shutdown awaits.
            pub_task = asyncio.create_task(self._broadcast_event(job_id, events.job_failed(kind=kind, message=str(exc))))
            self._background_tasks.add(pub_task)
            pub_task.add_done_callback(self._background_tasks.discard)
```

(No `if self._event_bus is not None` guard: `_broadcast_event` tolerates a missing bus.)

- [ ] **Step 4: The route**

In `embry0/api/ws/streaming.py` add near the top:

```python
from embry0.storage.repositories.jobs import TERMINAL_JOB_STATUSES

#: RFC 6455 normal closure: the server finished the stream on purpose. The
#: client treats it as "complete" and does not reconnect (spec 3.5).
FINISHED_CLOSE_CODE = 1000


def _is_job_finished(event: Any) -> bool:
    return isinstance(event, dict) and event.get("type") == "job_finished"


async def _job_is_terminal(jobs_repo: Any, job_id: str) -> bool:
    """True when the job row says the job ended: covers a job finished before
    job_finished existed and a job_finished whose persist failed."""
    try:
        row = await jobs_repo.get(job_id)
    except Exception:
        return False
    return isinstance(row, dict) and str(row.get("status") or "") in TERMINAL_JOB_STATUSES
```

Restructure the body after `queue = await event_bus.subscribe(job_id)` so one `try/finally` owns the unsubscribe:

```python
    watermark = since_seq
    replayed_finished = False
    client_gone: asyncio.Task[Any] | None = None
    next_event: asyncio.Task[Any] | None = None
    try:
        jobs_repo = websocket.app.state.jobs_repo
        try:
            async for event in _replay_rows(jobs_repo, job_id, since_seq):
                payload = event.get("payload", event)
                if not isinstance(payload, dict):
                    continue
                row_id = event.get("id")
                if isinstance(row_id, int):
                    payload = {**payload, "event_seq": row_id}
                    if row_id > watermark:
                        watermark = row_id
                if _is_job_finished(payload):
                    replayed_finished = True
                if not _passes_filter(payload, wanted_types):
                    continue
                await websocket.send_json(payload)
        except Exception:
            logger.warning("event_replay_failed", job_id=job_id, exc_info=True)

        # A finished job has nothing live to wait for: close after the replay.
        if replayed_finished or await _job_is_terminal(jobs_repo, job_id):
            logger.info("ws_closed_finished_job", job_id=job_id, replayed_terminal=replayed_finished)
            await websocket.close(code=FINISHED_CLOSE_CODE)
            return

        client_gone = asyncio.create_task(_wait_for_disconnect(websocket))
        while True:
            next_event = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({next_event, client_gone}, return_when=asyncio.FIRST_COMPLETED)
            if client_gone in done:
                logger.info("ws_disconnected", job_id=job_id)
                break
            event = next_event.result()
            event_seq = event.get("event_seq")
            if isinstance(event_seq, int) and event_seq <= watermark:
                continue  # already delivered via replay
            # The terminal event is checked before the filter: a client that
            # filters it out still has its socket closed (review focus 2).
            finished = _is_job_finished(event)
            if _passes_filter(event, wanted_types):
                await websocket.send_json(event)
            if finished:
                logger.info("ws_closed_finished_job", job_id=job_id, replayed_terminal=False)
                await websocket.close(code=FINISHED_CLOSE_CODE)
                break
    except WebSocketDisconnect:
        logger.info("ws_disconnected", job_id=job_id)
    except Exception:
        logger.exception("ws_error", job_id=job_id)
    finally:
        if client_gone is not None:
            client_gone.cancel()
        if next_event is not None:
            next_event.cancel()
        await event_bus.unsubscribe(job_id, queue)
```

- [ ] **Step 5: Run, lint, commit**

Run: `.venv/bin/python -m pytest tests/unit/api/ws tests/unit/services/test_issue_executor.py -q 2>&1 | tail -3 && .venv/bin/ruff check embry0/api/ws embry0/services/issue_executor.py tests/unit/api/ws tests/unit/services/test_issue_executor.py && .venv/bin/ruff format --check embry0/api/ws tests/unit/api/ws && .venv/bin/mypy embry0/api/ws/streaming.py embry0/services/issue_executor.py`
Expected: all passed, clean. The existing `test_ws_client_leaving_a_quiet_job_drops_its_subscription` still passes (its fake app has no `jobs_repo` with `get`, so `_job_is_terminal` is False and the live loop runs as before).

```bash
git add embry0/api/ws/streaming.py embry0/services/issue_executor.py tests/unit/api/ws/test_streaming.py tests/unit/services/test_issue_executor.py
git commit -q -F - <<'EOF'
feat(api): close the job socket on the terminal event

The socket stayed open until the client left, so Live stayed lit on
finished jobs and the client retried ten times against a job that was
over. The route now closes with 1000 after forwarding job_finished
(checked before the type filter, so a filtering client still gets the
close) and straight after replay when the job row is already terminal,
which also covers jobs finished before the event existed. job_failed
goes through _broadcast_event, so it is persisted and replayable.

Refs: #37

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---
### Task 5: The client keeps a cursor, drops replays, fills gaps and stops at the terminal event

**Files:**
- Rewrite: `frontend/src/hooks/useJobEventStream.ts`
- Modify: `frontend/src/api/logs.ts:11-24` (`fetchJobLogEvents(jobId, sinceSeq = 0)`)
- Modify: `frontend/src/lib/types/events-discriminated.ts:11-16` (`BaseLogEvent`), `:321-357` (union), new `JobFinishedEvent`
- Modify: `frontend/src/lib/types/events.ts` (re-export `JobFinishedEvent`; add `"job_finished"` to `LogEventType`)
- Create: `frontend/src/hooks/__tests__/useJobEventStream.test.ts`
- Modify: `frontend/src/hooks/__tests__/useJobLogs.test.ts:217-262` (the reconnect test carries `event_seq` and asserts the cursor)
- Modify: `docs/superpowers/specs/2026-10-15-trace-fidelity-design.md` section 3.5 (client bullet: close code 1000)

**Interfaces:**
- Produces: `fetchJobLogEvents(jobId: string, sinceSeq = 0): Promise<LogEvent[]>`; `useJobEventStream` unchanged signature; `BaseLogEvent` gains `event_seq?: number; job_id?: string`; `JobFinishedEvent` on the legacy union.
- Consumes: the WebSocket close code 1000 and the `job_finished` event from Task 4 (the hook does not depend on the backend change to compile or test).

- [ ] **Step 1: Write the failing tests**

`frontend/src/hooks/__tests__/useJobEventStream.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

vi.mock("@/api/jobs", () => ({
  fetchJobEventsToken: vi.fn().mockResolvedValue({ token: "test-ws-token", expires_in: 900 }),
}));
vi.mock("@/api/logs", () => ({
  fetchJobLogEvents: vi.fn(),
}));

import { useJobEventStream, type JobEventStreamHandlers } from "../useJobEventStream";
import { fetchJobLogEvents } from "@/api/logs";

interface MockWebSocketInstance {
  url: string;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  close: ReturnType<typeof vi.fn>;
  simulateOpen(): void;
  simulateMessage(data: object): void;
  simulateClose(code?: number): void;
}

let lastCreatedWs: MockWebSocketInstance | null = null;
const allCreatedWs: MockWebSocketInstance[] = [];

class MockWebSocket implements MockWebSocketInstance {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  url: string;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn();
  constructor(url: string) {
    this.url = url;
    // eslint-disable-next-line @typescript-eslint/no-this-alias
    lastCreatedWs = this;
    allCreatedWs.push(this);
  }
  simulateOpen() {
    this.onopen?.(new Event("open"));
  }
  simulateMessage(data: object) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(data) }));
  }
  simulateClose(code = 1006) {
    this.onclose?.(new CloseEvent("close", { code }));
  }
}

async function flushConnect() {
  await act(async () => {});
}

function harness(jobId = "job-1") {
  const onEvent = vi.fn();
  const onStreamEnd = vi.fn();
  const onFallbackEvents = vi.fn();
  const handlers: JobEventStreamHandlers = { onEvent, onStreamEnd, onFallbackEvents };
  const hook = renderHook(() => useJobEventStream(jobId, handlers, false));
  return { ...hook, onEvent, onStreamEnd, onFallbackEvents };
}

beforeEach(() => {
  lastCreatedWs = null;
  allCreatedWs.length = 0;
  vi.stubGlobal("WebSocket", MockWebSocket);
  Object.defineProperty(window, "location", {
    value: { protocol: "http:", host: "localhost:3001" },
    writable: true,
    configurable: true,
  });
  vi.useFakeTimers();
  vi.mocked(fetchJobLogEvents).mockReset().mockResolvedValue([]);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useJobEventStream replay cursor (spec 3.5, #38)", () => {
  it("connects without a cursor first and reconnects with ?since_seq=<last seen>", async () => {
    harness();
    await flushConnect();
    expect(lastCreatedWs!.url).toBe("ws://localhost:3001/ws/jobs/job-1/events");
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "a", event_seq: 11 });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t2", message: "b", event_seq: 12 });
      lastCreatedWs!.simulateClose();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(allCreatedWs).toHaveLength(2);
    expect(lastCreatedWs!.url).toBe("ws://localhost:3001/ws/jobs/job-1/events?since_seq=12");
  });

  it("drops a replayed event whose seq it already processed", async () => {
    const { onEvent } = harness();
    await flushConnect();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "a", event_seq: 5 });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "a", event_seq: 5 });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t0", message: "old", event_seq: 4 });
    });
    expect(onEvent).toHaveBeenCalledTimes(1);
  });

  it("fills a gap from the REST log before applying the live event, once each, in order", async () => {
    vi.mocked(fetchJobLogEvents).mockResolvedValue([
      { type: "progress", timestamp: "t3", message: "three", event_seq: 3 },
      { type: "progress", timestamp: "t4", message: "four", event_seq: 4 },
      { type: "progress", timestamp: "t5", message: "five", event_seq: 5 },
    ] as never);
    const { onEvent } = harness();
    await flushConnect();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t2", message: "two", event_seq: 2 });
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t5", message: "five", event_seq: 5 });
      // arrives while the fill is in flight: must wait behind it
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t6", message: "six", event_seq: 6 });
    });
    await act(async () => {});
    expect(fetchJobLogEvents).toHaveBeenCalledWith("job-1", 2);
    const seqs = onEvent.mock.calls.map((c) => (c[0] as { event_seq: number }).event_seq);
    expect(seqs).toEqual([2, 3, 4, 5, 6]);
  });

  it("does not treat the first event of a fresh connection as a gap", async () => {
    const { onEvent } = harness();
    await flushConnect();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t9", message: "nine", event_seq: 9 });
    });
    expect(fetchJobLogEvents).not.toHaveBeenCalled();
    expect(onEvent).toHaveBeenCalledTimes(1);
  });

  it("marks the stream complete on job_finished and never reconnects", async () => {
    const { result, onEvent, onStreamEnd } = harness();
    await flushConnect();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({
        type: "job_finished", timestamp: "t1", status: "completed", error_code: null, error_message: null, pr_url: null, total_cost_usd: 0.4, event_seq: 20,
      });
      lastCreatedWs!.simulateClose(1000);
    });
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onStreamEnd).toHaveBeenCalledTimes(1);
    expect(result.current.isComplete).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(allCreatedWs).toHaveLength(1);
  });

  it("treats a server close with code 1000 as complete even without job_finished", async () => {
    const { result } = harness();
    await flushConnect();
    await act(async () => {
      lastCreatedWs!.simulateOpen();
      lastCreatedWs!.simulateMessage({ type: "progress", timestamp: "t1", message: "a", event_seq: 1 });
      lastCreatedWs!.simulateClose(1000);
    });
    expect(result.current.isComplete).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(allCreatedWs).toHaveLength(1);
  });

  it("marks complete from a persisted job_finished on the REST fallback", async () => {
    vi.mocked(fetchJobLogEvents).mockResolvedValue([
      { type: "progress", timestamp: "t1", message: "a", event_seq: 1 },
      { type: "job_finished", timestamp: "t2", status: "failed", error_code: "ERR_X", error_message: "x", pr_url: null, total_cost_usd: 0, event_seq: 2 },
    ] as never);
    const { result, onFallbackEvents } = harness("job-done");
    await flushConnect();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });
    expect(fetchJobLogEvents).toHaveBeenCalledWith("job-done", 0);
    expect(onFallbackEvents).toHaveBeenCalledTimes(1);
    expect(result.current.isComplete).toBe(true);
  });
});
```

In `frontend/src/hooks/__tests__/useJobLogs.test.ts`, in "preserves accumulated events across reconnect after close", give the two initial messages `event_seq: 1` and `event_seq: 2`, and after `const newWs = lastCreatedWs!;` add:

```ts
    expect(newWs.url).toBe("ws://localhost:3001/ws/jobs/job-abc/events?since_seq=2");
```

Give the third message `event_seq: 3`. (The test keeps its "3 events total" assertion, now meaning no duplicates were appended.)

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/hooks/__tests__/useJobEventStream.test.ts src/hooks/__tests__/useJobLogs.test.ts 2>&1 | tail -15`
Expected: the seven new tests FAIL (`?since_seq=` missing from the URL, `onEvent` called 3 times instead of 1, `fetchJobLogEvents` not called with a cursor, `isComplete` false); the strengthened reconnect test FAILS on the URL. `npx tsc -b` also fails: `event_seq` is not on `LogEvent`, `"job_finished"` is not a member of the union.

- [ ] **Step 3: Types and the API client**

`frontend/src/lib/types/events-discriminated.ts`, the base:

```ts
interface BaseLogEvent {
  type: string;
  timestamp: string;
  /** Replay cursor stamped by the orchestrator after persist (job_logs.id). Absent on an event whose persist failed. */
  event_seq?: number;
  job_id?: string;
}
```

Add before the union:

```ts
/** spec 3.5: the terminal event; the server closes the socket after it. */
export interface JobFinishedEvent extends BaseLogEvent {
  type: "job_finished";
  status: string;
  error_code: string | null;
  error_message: string | null;
  pr_url: string | null;
  total_cost_usd: number;
}
```

and `| JobFinishedEvent` as the last member of `LegacyLogEvent`. In `frontend/src/lib/types/events.ts` add `JobFinishedEvent,` to the legacy re-export list and `| "job_finished"` to `LogEventType`.

`frontend/src/api/logs.ts`:

```ts
/** The persisted event log from `sinceSeq` (exclusive) to the tail; the API pages it (#203). */
export async function fetchJobLogEvents(jobId: string, sinceSeq = 0): Promise<LogEvent[]> {
  const events: LogEvent[] = [];
  let cursor = sinceSeq;
  for (;;) {
    const { data } = await api.get<LogEventsPage>(`/jobs/${jobId}/logs/events`, {
      params: { since_seq: cursor, limit: 1000 },
    });
    events.push(...data.events);
    const next = data.next_since_seq;
    if (!data.has_more || typeof next !== "number" || next <= cursor) return events;
    cursor = next;
  }
}
```

- [ ] **Step 4: The hook**

Replace `frontend/src/hooks/useJobEventStream.ts` with:

```ts
import { useEffect, useRef, useState, useCallback } from "react";
import type { LogEvent } from "@/lib/types";
import { fetchJobLogEvents } from "@/api/logs";
import { fetchJobEventsToken } from "@/api/jobs";

const MAX_RECONNECT_RETRIES = 10;
/** RFC 6455 normal closure: the server ended the stream on purpose (spec 3.5). */
const FINISHED_CLOSE_CODE = 1000;

/**
 * Consumer callbacks for {@link useJobEventStream}. Handlers are read through
 * a latest-ref, so passing a fresh object literal on every render is fine —
 * it never tears down or reconnects the WebSocket.
 */
export interface JobEventStreamHandlers {
  /** Called once for every event, in `event_seq` order, live or gap-filled. */
  onEvent: (event: LogEvent) => void;
  /** Called when the stream terminates (`job_finished`, or the server closed
   * with 1000) so consumers can flush batched state immediately. For a
   * `job_finished` event this fires after `onEvent` has delivered it. */
  onStreamEnd?: () => void;
  /** Called with persisted events when the REST fallback loads them (the WS
   * never connected — typically a job that finished before mount). */
  onFallbackEvents?: (events: LogEvent[]) => void;
  /** Called on teardown of each connection cycle (jobId change or unmount).
   * Reconnects within one cycle do NOT reset: the hook reconnects with
   * `?since_seq=<last event_seq>` and drops anything it already delivered. */
  onReset?: () => void;
}

export interface JobEventStreamResult {
  isConnected: boolean;
  isComplete: boolean;
}

function seqOf(event: LogEvent): number | undefined {
  const seq = (event as { event_seq?: unknown }).event_seq;
  return typeof seq === "number" ? seq : undefined;
}

/**
 * Shared WS connection core for per-job event streams.
 *
 * Replay and gaps (spec 3.5): the hook remembers the highest `event_seq` it
 * delivered. A reconnect asks the server for `?since_seq=<that>`; a live or
 * replayed event at or below it is a duplicate and is dropped; a live event
 * more than one past it means events were lost between (bus overflow, a
 * persist failure), so the hook fetches the persisted log from the cursor
 * and delivers it first, holding later live events until the fill lands.
 * `job_finished`, or a server close with code 1000, ends the stream: the
 * hook marks it complete and never reconnects.
 */
export function useJobEventStream(
  jobId: string | undefined,
  handlers: JobEventStreamHandlers,
  hasEvents: boolean,
): JobEventStreamResult {
  const [isConnected, setIsConnected] = useState(false);
  const [isComplete, setIsComplete] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const isCompleteRef = useRef(false);
  const retryCountRef = useRef(0);
  const wsEverConnectedRef = useRef(false);
  const connectAttemptRef = useRef(0);
  /** Highest event_seq delivered to the consumer; the reconnect cursor. */
  const lastSeqRef = useRef(0);
  /** A gap fill in flight; live events queue behind it. */
  const fillRef = useRef<Promise<void> | null>(null);
  const queuedRef = useRef<LogEvent[]>([]);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const markStreamComplete = useCallback(() => {
    if (isCompleteRef.current) return;
    setIsComplete(true);
    isCompleteRef.current = true;
    retryCountRef.current = 0;
    handlersRef.current.onStreamEnd?.();
  }, []);

  /** Deliver one event unless its seq was already delivered; advance the cursor. */
  const deliver = useCallback(
    (event: LogEvent) => {
      const seq = seqOf(event);
      if (seq !== undefined) {
        if (seq <= lastSeqRef.current) return;
        lastSeqRef.current = seq;
      }
      handlersRef.current.onEvent(event);
      if (event.type === "job_finished") markStreamComplete();
    },
    [markStreamComplete],
  );

  const applyLive = useCallback(
    (event: LogEvent, currentJobId: string) => {
      const seq = seqOf(event);
      if (seq !== undefined && seq <= lastSeqRef.current) return; // replayed duplicate
      if (fillRef.current) {
        queuedRef.current.push(event); // a fill owns the order until it lands
        return;
      }
      const gap = seq !== undefined && lastSeqRef.current > 0 && seq > lastSeqRef.current + 1;
      if (!gap) {
        deliver(event);
        return;
      }
      queuedRef.current.push(event);
      const from = lastSeqRef.current;
      fillRef.current = fetchJobLogEvents(currentJobId, from)
        .then((persisted) => {
          for (const p of persisted) deliver(p);
        })
        .catch(() => {
          // The live event still lands below; the gap stays a gap.
        })
        .finally(() => {
          fillRef.current = null;
          const queued = queuedRef.current;
          queuedRef.current = [];
          for (const q of queued) deliver(q);
        });
    },
    [deliver],
  );

  const connect = useCallback(() => {
    if (!jobId) return;
    const attempt = ++connectAttemptRef.current;

    void (async () => {
      // RAV-1070: trade the Bearer key for a short-lived job-scoped token —
      // the raw API key never rides the WS subprotocol. A failed mint
      // connects bare: dev-mode servers accept it, production closes the
      // socket and the reconnect/REST-fallback paths take over.
      let subprotocols: string[] = [];
      try {
        const { token } = await fetchJobEventsToken(jobId);
        subprotocols = [`embry0.token.${token}`];
      } catch {
        // fall through — see above
      }
      if (attempt !== connectAttemptRef.current) return;

      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const cursor = lastSeqRef.current > 0 ? `?since_seq=${lastSeqRef.current}` : "";
      const ws = new WebSocket(`${protocol}//${window.location.host}/ws/jobs/${jobId}/events${cursor}`, subprotocols);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        wsEverConnectedRef.current = true;
        retryCountRef.current = 0;
      };

      ws.onmessage = (e) => {
        try {
          const event: LogEvent = JSON.parse(e.data);
          applyLive(event, jobId);
        } catch {
          // Ignore malformed messages
        }
      };

      ws.onclose = (e) => {
        setIsConnected(false);
        if (e.code === FINISHED_CLOSE_CODE) {
          // The server closed on purpose: the job is over (spec 3.5).
          markStreamComplete();
          return;
        }
        if (!isCompleteRef.current && retryCountRef.current < MAX_RECONNECT_RETRIES) {
          retryCountRef.current += 1;
          reconnectTimeoutRef.current = setTimeout(connect, 3000);
        }
      };

      ws.onerror = () => ws.close();
    })();
  }, [jobId, applyLive, markStreamComplete]);

  useEffect(() => {
    setIsConnected(false);
    setIsComplete(false);
    isCompleteRef.current = false;
    retryCountRef.current = 0;
    wsEverConnectedRef.current = false;
    lastSeqRef.current = 0;
    fillRef.current = null;
    queuedRef.current = [];
    connect();

    return () => {
      clearTimeout(reconnectTimeoutRef.current);
      // eslint-disable-next-line react-hooks/exhaustive-deps
      connectAttemptRef.current++;
      const ws = wsRef.current;
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onclose = null;
        ws.onerror = null;
        ws.close();
      }
      handlersRef.current.onReset?.();
    };
  }, [connect]);

  // Fallback: load persisted events for a job whose socket never connected.
  useEffect(() => {
    if (!jobId || isConnected || wsEverConnectedRef.current || hasEvents) return;
    const timer = setTimeout(async () => {
      try {
        const persisted = await fetchJobLogEvents(jobId, 0);
        if (persisted.length > 0) {
          for (const ev of persisted) {
            const seq = seqOf(ev);
            if (seq !== undefined && seq > lastSeqRef.current) lastSeqRef.current = seq;
          }
          if (persisted.some((ev) => ev.type === "job_finished")) {
            setIsComplete(true);
            isCompleteRef.current = true;
          }
          handlersRef.current.onFallbackEvents?.(persisted);
        }
      } catch {
        // Ignore — no persisted events available
      }
    }, 1000);
    return () => clearTimeout(timer);
  }, [jobId, isConnected, hasEvents]);

  return { isConnected, isComplete };
}
```

The `stream_end`/`complete` branches are gone: nothing emits them (spec 1, item 3), and `job_finished` is the terminal event now. Update the two consumer tests that fed those phantom types to terminate the stream ("marks isComplete … on stream_end", "… on complete event", "does not reconnect after stream_end close" in both `useJobLogs.test.ts` and `useLiveJobSummary.test.ts`): send a `job_finished` event instead, keep each test's assertion. `useJobLogs`' and `useLiveJobSummary`'s own `complete` cost branches stay until #41.

Amend spec 3.5's client bullet: after "On `job_finished` the client marks the stream complete and never reconnects." add "A server close with code 1000 means the same (the route closes that way straight after replay for a job that was already terminal)."

- [ ] **Step 5: Run, type-check, lint**

Run: `cd frontend && npx vitest run src/hooks 2>&1 | tail -6 && npx tsc -b && npm run lint 2>&1 | tail -2`
Expected: all hook tests pass; `tsc -b` silent; 0 lint errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/hooks/useJobEventStream.ts frontend/src/api/logs.ts frontend/src/lib/types/events-discriminated.ts frontend/src/lib/types/events.ts frontend/src/hooks/__tests__ docs/superpowers/specs/2026-10-15-trace-fidelity-design.md
git commit -q -F - <<'EOF'
feat(frontend): replay cursor, dedupe and gap fill on the job stream

A reconnect replayed the whole log onto the existing buffer, a bus
overflow was invisible, and completion depended on two event types
nothing ever sent. The hook now remembers the last event_seq, reconnects
with ?since_seq=, drops anything already delivered, fills a seq gap
from the REST log before applying the live event that revealed it, and
ends on job_finished or a server close with code 1000, never
reconnecting after either.

Fixes: #38

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 6: Redaction before the cap in the sandbox, and the remaining truncation rules

**Files:**
- Modify: `embry0/safety/redaction.py` (add `redact_event`)
- Modify: `embry0/agents/_common.py` (add `make_scrubber`)
- Modify: `embry0/agents/executor.py` (`run()`: build the scrubber; `consume()`: scrub `text`, `thinking`, `tool_result.content`, `agent_completed.output`)
- Modify: `embry0/agents/executor_xai.py` (same four sites: lines ~247, ~249, ~300, ~318, ~409)
- Modify: `embry0/sandbox/runner.py:29-33` (`_emit` scrubs every string value)
- Modify: `embry0/execution/events.py:399-466, 538-575` (`_capped(keep=)`, `tool_input` string cap)
- Modify: `tests/unit/safety/test_redaction.py`, `tests/unit/agents/test_sdk_executor.py`, `tests/unit/agents/test_executor_xai.py`, `tests/unit/sandbox/test_runner.py`, `tests/unit/execution/test_events.py`
- Modify: `docs/superpowers/specs/2026-10-15-trace-fidelity-design.md` section 3.6 (sandbox sentence)

**Interfaces:**
- Produces: `embry0.safety.redaction.redact_event(event: dict[str, Any], known_values: Iterable[str]) -> dict[str, Any]` (copy with every string value scrubbed, recursively through dicts and lists; the top-level correlation keys `type`, `timestamp`, `job_id`, `event_seq`, `run_id`, `node`, `agent`, `parent_tool_use_id`, `tool_id`, `tool_name` untouched); `embry0.agents._common.make_scrubber() -> Callable[[str], str]` (one `secret_env_values()` call, applied by `redact_secrets`); `events.TOOL_INPUT_STRING_CAP = 65536`; `_capped(..., keep: Literal["head", "tail"] = "head")`.
- Consumes: `redact_secrets`, `secret_env_values` (existing).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/safety/test_redaction.py`:

```python
from embry0.safety.redaction import REDACTED, redact_event


def test_redact_event_scrubs_strings_recursively_and_leaves_correlation_keys() -> None:
    secret = "sekrit-value-0123456789"
    event = {
        "type": "tool_call",
        "timestamp": "2026-10-16T00:00:00+00:00",
        "run_id": "run-" + "a" * 32,
        "tool_id": "toolu_01" + secret[:8],
        "tool_name": "Bash",
        "summary": f"curl -H 'Authorization: Bearer {secret}'",
        "tool_input": {"command": f"export TOKEN={secret}", "env": [{"name": "X", "value": secret}]},
        "content": [{"type": "text", "text": f"saw {secret}"}],
        "is_error": False,
        "count": 3,
    }
    out = redact_event(event, [secret])
    assert secret not in repr(out), "every string value, however nested, is scrubbed"
    assert out["tool_input"]["env"][0]["value"] == REDACTED
    assert out["content"][0]["text"] == f"saw {REDACTED}"
    assert out["tool_id"] == event["tool_id"] and out["run_id"] == event["run_id"], "correlation keys are never rewritten"
    assert out["is_error"] is False and out["count"] == 3
    assert event["summary"].endswith(secret), "the input event is not mutated"
```

Append to `tests/unit/execution/test_events.py`:

```python
def test_agent_completed_output_keeps_the_tail_when_cut() -> None:
    output = "x" * 9000 + "THE END"
    event = events.agent_completed(
        is_error=False, error_code=None, error_message="", output=output + "y" * 2000, cost_usd=0.0, duration_ms=1,
        tools_called={}, policy_denials={}, input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0,
        run_id="r", node="developer", agent="developer",
    )
    assert event["truncated"] is True and event["original_length"] == 11007
    assert len(event["output"]) == events.OUTPUT_CAP
    assert "THE END" in event["output"], "spec 3.1: agent_completed.output is the tail, where the conclusion is"


def test_text_still_keeps_the_head_when_cut() -> None:
    event = events.text(text="A" + "b" * 5000, run_id="r", node="n", agent="a")
    assert event["text"].startswith("A") and len(event["text"]) == events.TEXT_CAP


def test_tool_input_string_values_above_64kib_are_cut_with_an_in_string_marker() -> None:
    big = "z" * (events.TOOL_INPUT_STRING_CAP + 10)
    event = events.tool_call(
        tool_id="t", tool_name="Write", summary="write", tool_input={"file_path": "a.py", "content": big},
        run_id="r", node="n", agent="a",
    )
    stored = event["tool_input"]["content"]
    assert len(stored) < len(big) and stored.startswith("z" * 100)
    assert f"original_length={len(big)}" in stored, "the cut is marked inside the value, so the contract shape is unchanged"
    assert event["tool_input"]["file_path"] == "a.py"
    assert "truncated" not in event, "tool_call carries no top-level marker (spec 3.6: the value is cut, not the event)"
```

Append to `tests/unit/agents/test_sdk_executor.py` (reuses `_assistant_with`, `_FakeToolUse`, `_FakeUserMessage`, `_FakeToolResultBlock`, `_FakeBlock`, `_run`):

```python
@pytest.mark.asyncio
async def test_executor_scrubs_text_and_tool_results_with_the_sandbox_env_secrets(tmp_path, monkeypatch) -> None:
    secret = "env-secret-value-9876543210"
    monkeypatch.setenv("SOME_SERVICE_TOKEN", secret)
    messages = [
        _assistant_with([_FakeBlock(f"the token is {secret}"), _FakeToolUse("t1", "Bash", {"command": "cat .env"})]),
        _FakeUserMessage([_FakeToolResultBlock("t1", f"SOME_SERVICE_TOKEN={secret}\n")]),
        _FakeResultMessage(f"done, used {secret}", 0.001),
    ]
    events_out = await _run(messages, monkeypatch, tmp_path)
    text = next(e for e in events_out if e["type"] == "text")
    result = next(e for e in events_out if e["type"] == "tool_result")
    completed = next(e for e in events_out if e["type"] == "agent_completed")
    assert secret not in text["text"] and "***REDACTED***" in text["text"]
    assert secret not in result["content"]
    assert secret not in completed["output"]


@pytest.mark.asyncio
async def test_a_secret_straddling_the_cap_leaves_no_fragment(tmp_path, monkeypatch) -> None:
    """Redact before cap (spec 3.6): a token cut in half matches no pattern."""
    secret = "straddle-secret-abcdefghij0123456789"
    monkeypatch.setenv("STRADDLE_TOKEN", secret)
    content = "p" * 3990 + secret + "q" * 200  # the cut at 4000 would land inside the secret
    messages = [
        _assistant_with([_FakeToolUse("t1", "Bash", {"command": "x"})]),
        _FakeUserMessage([_FakeToolResultBlock("t1", content)]),
        _FakeResultMessage("done", 0.001),
    ]
    events_out = await _run(messages, monkeypatch, tmp_path)
    result = next(e for e in events_out if e["type"] == "tool_result")
    assert secret[:10] not in result["content"], "a fragment of the secret survived the cut"
    assert "***REDACTED***" in result["content"]
```

Append to `tests/unit/agents/test_executor_xai.py` (reuses `_install`, `_Resp`, `_Block`, `_Usage`, `_invocation`, `env`):

```python
@pytest.mark.asyncio
async def test_xai_executor_scrubs_text_and_tool_results(env, monkeypatch) -> None:
    secret = "xai-env-secret-value-1234567890"
    monkeypatch.setenv("ANOTHER_API_KEY", secret)
    _install(
        monkeypatch,
        [
            _Resp([_Block("text", text=f"key {secret}"), _Block("tool_use", id="t1", name="Bash", input={"command": "echo hi"})], "tool_use", _Usage(5, 5)),
            _Resp([_Block("text", text="done")], "end_turn", _Usage(5, 5)),
        ],
    )
    monkeypatch.setattr(ex, "execute_tool", lambda name, tool_input, cwd, policy: (f"out {secret}", False))
    events_out: list[dict] = []
    await DirectXaiExecutor().run(_invocation(), config={"configurable": {}, "_test_writer": events_out.append})
    assert all(secret not in e.get("text", "") for e in events_out if e["type"] == "text")
    assert all(secret not in e.get("content", "") for e in events_out if e["type"] == "tool_result")
```

Append to `tests/unit/sandbox/test_runner.py`:

```python
def test_emit_scrubs_every_string_value_before_stdout(capsys, monkeypatch) -> None:
    """The runner's writer is the sandbox's last line: tool_input and anything a
    constructor did not cap are scrubbed here (spec 3.6, second pass)."""
    from embry0.sandbox.runner import _emit

    secret = "runner-secret-value-abcdef123456"
    monkeypatch.setenv("RUNNER_TEST_TOKEN", secret)
    _emit(events.tool_call(tool_id="t1", tool_name="Bash", summary=f"echo {secret}", tool_input={"command": f"echo {secret}", "env": [secret]}, run_id="r", node="n", agent="a"))
    line = json.loads(capsys.readouterr().out)
    assert secret not in json.dumps(line)
    assert line["tool_id"] == "t1" and line["type"] == "tool_call"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/safety/test_redaction.py tests/unit/execution/test_events.py tests/unit/agents tests/unit/sandbox/test_runner.py -q -k "redact_event or tail or 64kib or scrubs or straddling" 2>&1 | tail -12`
Expected: every new test FAILS with its own message (`ImportError: redact_event`, "THE END" missing, no in-string marker, the secret present in the event).

- [ ] **Step 3: `redact_event` and `make_scrubber`**

Append to `embry0/safety/redaction.py`:

```python
# ---- one event, every string -------------------------------------------------

#: Top-level event keys that carry identifiers, never text. Left as they are
#: so a scrub can never break the pairing of a result with its call or of an
#: event with its run.
EVENT_KEYS_NOT_REDACTED = frozenset(
    {"type", "timestamp", "job_id", "event_seq", "run_id", "node", "agent", "parent_tool_use_id", "tool_id", "tool_name"}
)


def redact_event(event: dict[str, Any], known_values: Iterable[str]) -> dict[str, Any]:
    """A copy of ``event`` with every string value passed through
    :func:`redact_secrets`, recursively through dicts and lists, so a secret
    inside ``tool_input`` or a content block is caught too. The input is not
    mutated. Top-level correlation keys are left as they are."""
    values = list(known_values)

    def _walk(value: Any) -> Any:
        if isinstance(value, str):
            return redact_secrets(value, values)
        if isinstance(value, dict):
            return {k: _walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v) for v in value]
        return value

    return {k: (v if k in EVENT_KEYS_NOT_REDACTED else _walk(v)) for k, v in event.items()}
```

(`Any` is imported from `typing` at the module top; add it to the existing import line.)

Append to `embry0/agents/_common.py`:

```python
from collections.abc import Callable

from embry0.safety.redaction import redact_secrets, secret_env_values


def make_scrubber() -> Callable[[str], str]:
    """One scrubber per agent run: the sandbox's secret env values are read
    once and every text the executor emits goes through redact_secrets BEFORE
    the constructor caps it (spec 3.6: redact, then cut)."""
    values = secret_env_values()

    def scrub(text: str) -> str:
        return redact_secrets(text, values) if text else text

    return scrub
```

- [ ] **Step 4: The executors and the runner**

`embry0/agents/executor.py`: at the top of `run()` where `ids = _ids(invocation)` is computed, add `scrub = make_scrubber()` (import `make_scrubber` from `embry0.agents._common`). In `consume()` wrap the four values: `events.text(text=scrub(block_text), ...)`, `events.thinking(text=scrub(str(getattr(block, "thinking", ""))), ...)`, in the user-message branch compute `result_text = _render_tool_result(...)` as today, keep `_extract_embedded_ask_user_events(result_text)` on the raw text (the embedded JSON must parse), and pass `content=scrub(result_text)` to `events.tool_result`; at the end `events.agent_completed(..., output=scrub(output), ...)`. The `output` returned on `AgentOutput` stays unscrubbed (the job row and the trace need it; the orchestrator scrubs the trace summary in Task 7).

`embry0/agents/executor_xai.py`: same four wraps at the `text` (~247), `thinking` (~249), both `tool_result` (~300 verdict reason stays as is, ~318 `content=scrub(result_text)`) and `agent_completed` (~409 `output=scrub(output)`) sites, with `scrub = make_scrubber()` computed once in `run()`.

`embry0/sandbox/runner.py`:

```python
from embry0.safety.redaction import redact_event, redact_secrets, secret_env_values


def _emit(event: dict[str, Any]) -> None:
    """Write a JSON event to stdout and flush.

    Second redaction pass (spec 3.6): the executors scrub their text fields
    before the constructors cap them; this scrubs every string the event
    still carries (tool_input, summaries, error messages) with the secrets
    this process can see, so no line depends on one side alone.
    """
    event.setdefault("timestamp", datetime.now(UTC).isoformat())
    event = redact_event(event, secret_env_values())
    sys.stdout.write(json.dumps(event, default=str) + "\n")
    sys.stdout.flush()
```

- [ ] **Step 5: `events.py`: tail for output, string cap for `tool_input`**

```python
def _capped(fields: dict[str, Any], key: str, value: str, cap: int, *, keep: Literal["head", "tail"] = "head") -> dict[str, Any]:
    """Store ``value`` under ``key`` cut at ``cap`` characters, with a marker when cut.
    ``keep="tail"`` keeps the end (an agent's final output ends with its conclusion)."""
    if len(value) > cap:
        fields[key] = value[:cap] if keep == "head" else value[-cap:]
        fields["truncated"] = True
        fields["original_length"] = len(value)
    else:
        fields[key] = value
    return fields


TOOL_INPUT_STRING_CAP = 64 * 1024


def _cap_tool_input(tool_input: dict[str, Any]) -> dict[str, Any]:
    """``tool_input`` is stored whole, except that a single string value above
    TOOL_INPUT_STRING_CAP is cut with a marker inside the value, so a Write of
    a large file does not store the file twice (spec 3.6). The event shape is
    unchanged: the marker lives in the string."""
    out: dict[str, Any] = {}
    for key, value in tool_input.items():
        if isinstance(value, str) and len(value) > TOOL_INPUT_STRING_CAP:
            out[key] = value[:TOOL_INPUT_STRING_CAP] + f"\n… [truncated; original_length={len(value)}]"
        else:
            out[key] = value
    return out
```

`tool_call` passes `"tool_input": _cap_tool_input(tool_input)`; `agent_completed` passes `keep="tail"` to its `_capped` call. `Literal` joins the `typing` import.

Amend spec 3.6, the sentence "The sandbox applies the same function to `text`, `thinking`, `tool_result.content` and `agent_completed.output` before writing to stdout": "The sandbox executors scrub `text`, `thinking`, `tool_result.content` and `agent_completed.output` with the secret values they can see (their own env) before the constructor caps them, so a token is never cut before it is matched; the runner's writer scrubs every string value of the event once more before stdout, so `tool_input` is covered and no line depends on one side alone." And after "`tool_input` is stored whole (it already is) but a single string value above 64 KiB is cut with the same marker": replace "with the same marker" by "with a marker inside the value (the event shape is unchanged)".

- [ ] **Step 6: Run, lint, commit**

Run: `.venv/bin/python -m pytest tests/unit/safety tests/unit/execution tests/unit/agents tests/unit/sandbox -q 2>&1 | tail -3 && .venv/bin/ruff check embry0 tests && .venv/bin/ruff format --check embry0/safety embry0/agents embry0/sandbox embry0/execution/events.py tests/unit/safety tests/unit/agents tests/unit/sandbox tests/unit/execution && .venv/bin/mypy embry0/safety/redaction.py embry0/agents embry0/sandbox/runner.py embry0/execution/events.py`
Expected: all passed; clean. The contract test still passes: no TypedDict changed.

```bash
git add embry0/safety/redaction.py embry0/agents/_common.py embry0/agents/executor.py embry0/agents/executor_xai.py embry0/sandbox/runner.py embry0/execution/events.py tests/unit/safety/test_redaction.py tests/unit/execution/test_events.py tests/unit/agents/test_sdk_executor.py tests/unit/agents/test_executor_xai.py tests/unit/sandbox/test_runner.py docs/superpowers/specs/2026-10-15-trace-fidelity-design.md
git commit -q -F - <<'EOF'
feat(sandbox): scrub event text before the cap and every string at the writer

Nothing on the event path was redacted: a cat of .env in a tool result
reached job_logs verbatim. The executors now scrub text, thinking, tool
results and the final output with the sandbox's own secret values
before the constructors cut them (a token cut in half matches nothing),
and the runner's writer scrubs every string the event carries once more.
agent_completed.output keeps its tail, and a tool_input string above
64 KiB is cut with a marker inside the value.

Refs: #39

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---
### Task 7: Orchestrator-side redaction, drop on scrub failure, secret registration, redacted trace summary

**Files:**
- Modify: `embry0/services/job_events.py` (redact in `broadcast_job_event`)
- Modify: `embry0/execution/sandbox_manager.py` (~672 after `enroll_sandbox` in `create`, ~848 in the resume path)
- Modify: `embry0/services/_executor_run.py:469-517` (`_precheck_llm_accounts` registers the resolved secrets)
- Modify: `embry0/orchestration/nodes/agent.py:82-100, 599-619` (`result_summary` redacted before its cap)
- Modify: `tests/unit/services/test_job_events.py`, `tests/unit/execution/test_sandbox_manager*.py` (the file that already tests `create`'s enrollment), `tests/unit/services/` (the precheck's existing test file; `grep -rln _precheck_llm_accounts tests/unit`), `tests/unit/orchestration/nodes/test_agent_node.py`
- Modify: `docs/superpowers/specs/2026-10-15-trace-fidelity-design.md` sections 3.6 and 3.9

**Interfaces:**
- Consumes: `redact_event`, `secret_env_values`, `redact_secrets`, `register_known_secrets`, `known_secret_values` (`embry0/safety/redaction.py`); `broadcast_job_event` from Task 1.
- Produces: `broadcast_job_event` returns `None` and publishes nothing when the scrub raises.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/services/test_job_events.py`:

```python
from embry0.safety.redaction import REDACTED, register_known_secrets


@pytest.fixture(autouse=True)
def _clean_known_secrets():
    register_known_secrets([], reset=True)
    yield
    register_known_secrets([], reset=True)


@pytest.mark.asyncio
async def test_broadcast_redacts_nested_values_before_persist_and_publish() -> None:
    """Review focus 3: a registered secret two levels down and one in a list block."""
    secret = "registered-secret-value-00112233"
    register_known_secrets([secret])
    jobs = _jobs(seq=1)
    bus = EventBus()
    queue = await bus.subscribe("job-1")
    event = {
        "type": "tool_call",
        "tool_id": "t1",
        "tool_name": "Bash",
        "summary": "run",
        "tool_input": {"command": "run", "env": [{"name": "K", "value": secret}]},
        "run_id": "run-1",
    }
    await broadcast_job_event(jobs, bus, None, "job-1", event)
    persisted = jobs.append_log_event.await_args.args[1]
    published = queue.get_nowait()
    assert persisted["tool_input"]["env"][0]["value"] == REDACTED, "scrubbed before persist"
    assert published["tool_input"]["env"][0]["value"] == REDACTED, "scrubbed before publish"
    assert persisted["tool_id"] == "t1" and persisted["run_id"] == "run-1"
    assert event["tool_input"]["env"][0]["value"] == secret, "the caller's dict is not mutated"

    content_event = {"type": "tool_result", "tool_id": "t1", "is_error": False, "content": f"line {secret}"}
    await broadcast_job_event(jobs, bus, None, "job-1", content_event)
    assert secret not in jobs.append_log_event.await_args.args[1]["content"]


@pytest.mark.asyncio
async def test_a_scrub_failure_drops_the_event_and_logs_only_its_type(monkeypatch, capsys) -> None:
    """spec 3.9: leaking is worse than a missing line."""
    import embry0.services.job_events as job_events

    def _boom(event, values):
        raise RuntimeError("regex exploded")

    monkeypatch.setattr(job_events, "redact_event", _boom)
    jobs = _jobs(seq=1)
    bus = EventBus()
    queue = await bus.subscribe("job-1")
    out = await broadcast_job_event(jobs, bus, None, "job-1", {"type": "text", "text": "the payload"})
    assert out is None
    jobs.append_log_event.assert_not_awaited()
    assert queue.qsize() == 0, "a line that could not be scrubbed must not reach anyone"
    logged = capsys.readouterr().out
    assert "event_redaction_failed" in logged and "text" in logged
    assert "the payload" not in logged, "the log line carries the event type only"
```

Append to `tests/unit/execution/test_sandbox_manager_credentials.py` (its `_resolved`, `_docker`, `_proxy` helpers; the fake `enroll_sandbox` returns `"bearer-token-" + "x" * 30`):

```python
async def test_create_registers_the_sandbox_bearer_with_the_scrubber():
    """spec 3.6: the per-sandbox bearer must be an exact-value scrub target; it
    matches no pattern on its own."""
    from embry0.safety.redaction import known_secret_values, register_known_secrets

    register_known_secrets([], reset=True)
    try:
        resolver = MagicMock()
        resolver.resolve_forge = AsyncMock(return_value=_resolved())
        pm = _proxy()
        mgr = SandboxManager(docker=_docker(), proxy_manager=pm, credential_resolver=resolver)
        await mgr.create("job-1", repo="acme/widgets", forge="github")
        assert "bearer-token-" + "x" * 30 in known_secret_values(), "the bearer is registered as a known secret"
    finally:
        register_known_secrets([], reset=True)
```

Append to `tests/unit/services/test_llm_precheck.py` (its `_resolved(provider)` builds a `ResolvedCredential` whose secret is `"super-secret-token"`; its `_executor(resolver, build_graph_config=...)` and `_Saver` helpers drive `_execute_workflow_stream`):

```python
@pytest.mark.asyncio
async def test_precheck_registers_the_resolved_secrets_with_the_scrubber(monkeypatch) -> None:
    """spec 3.6: an env-fallback LLM credential is not in the registry, so the
    job's resolution is what registers it."""
    from embry0.safety.redaction import known_secret_values, register_known_secrets

    register_known_secrets([], reset=True)
    try:

        async def resolve_llm_for_job(repo, providers, *, job_id, parent_job_id=None):
            return {provider: _resolved(provider) for provider in providers}

        resolver = MagicMock()
        resolver.resolve_llm_for_job = resolve_llm_for_job
        executor = _executor(resolver, build_graph_config=lambda job_id, **kw: {"configurable": {"thread_id": job_id}})
        monkeypatch.setattr("embry0.services._executor_run.checkpointer_context", lambda _url: _Saver())
        await executor._execute_workflow_stream({"repo": "acme/widgets", "job_id": "job-1"}, "iss-1", "job-1")
        assert "super-secret-token" in known_secret_values(), "every resolved LLM secret is a known scrub value"
    finally:
        register_known_secrets([], reset=True)
```

Append to `tests/unit/orchestration/nodes/test_agent_node.py`:

```python
@pytest.mark.asyncio
async def test_trace_result_summary_is_redacted_before_its_cap(monkeypatch) -> None:
    """spec 3.6: traces.result_summary is redacted at write, and before the 500-char cut."""
    secret = "trace-secret-value-0011223344"
    monkeypatch.setenv("TRACE_TEST_TOKEN", secret)
    runner = _make_fake_runner(is_error=True, error_message="x" * 490 + secret, output="")
    traces_repo = AsyncMock()
    traces_repo.create = AsyncMock(return_value="run-x")
    await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": {}},
        agent_runner=runner,
        agent_type="developer",
        prompt="go",
        agent_definition={"model": "claude-sonnet-5", "tools": [], "skills": [], "system_prompt": "", "mcp_servers": {}, "execution_mode": None, "auth_mode": None},
        credentials={"api_key": "", "oauth_token": "t"},
        config={"configurable": {"job_id": "job-test", "traces_repo": traces_repo}},
    )
    summary = traces_repo.finish.call_args.kwargs["result_summary"]
    assert secret not in summary and secret[:8] not in summary, "no fragment of the secret may survive the cut"
    assert len(summary) <= 500
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/services/test_job_events.py tests/unit/orchestration/nodes/test_agent_node.py tests/unit/execution/test_sandbox_manager_credentials.py tests/unit/services/test_llm_precheck.py -q -k "redact or scrub or bearer or registers" 2>&1 | tail -8`
Expected: each new test FAILS with its own message (the secret present in the persisted event; `register_known_secrets` never called; the fragment present in the summary).

- [ ] **Step 3: Redact in the core**

In `embry0/services/job_events.py`, after the `timestamp` stamp and before the persist:

```python
    # spec 3.6: redact before persisting and before publishing, every string
    # value, recursively. spec 3.9: a scrubber that raises drops the event;
    # the log carries the type only, never the payload.
    try:
        event = redact_event(event, secret_env_values())
    except Exception:
        logger.error("event_redaction_failed", job_id=job_id, event_type=str(event.get("type") or "unknown"))
        return None
```

with `from embry0.safety.redaction import redact_event, secret_env_values` at the top. `secret_env_values()` scans this process's env on every call; it is a dict comprehension over `os.environ` plus a sort of the registered set, acceptable per event (a few hundred strings). Do not cache it across calls: the registered set grows while the process runs.

- [ ] **Step 4: Register the secrets the scrubber cannot see otherwise**

`embry0/execution/sandbox_manager.py`, immediately after both `sandbox_token = await self._proxy_manager.enroll_sandbox(...)` calls (create ~672, resume ~848):

```python
            # spec 3.6: the bearer matches no pattern on its own, so it is an
            # exact-value scrub target from the moment it exists.
            register_known_secrets([sandbox_token])
```

(`from embry0.safety.redaction import register_known_secrets` at the module top.)

`embry0/services/_executor_run.py`, in `_precheck_llm_accounts` right after `resolved = await resolver.resolve_llm_for_job(...)`:

```python
    # spec 3.6: registry secrets are registered at startup; an env-fallback
    # credential is not, so register whatever this job resolved to.
    register_known_secrets(cred.secret for cred in resolved.values() if isinstance(cred.secret, str))
```

`embry0/orchestration/nodes/agent.py`: callers stop pre-cutting the summary. `_crashed_trace_fields` passes `"result_summary": str(exc)` and the success/error finish passes `result_summary=result.error_message or result.output or ""`; `_finish_trace` redacts and caps:

```python
    async def _finish_trace(**fields: Any) -> None:
        if traces_repo is None or not trace_job_id:
            return
        # spec 3.6: the summary is redacted at write, and before the cap, so a
        # cut can never leave a fragment no pattern recognises.
        summary = str(fields.get("result_summary") or "")
        fields["result_summary"] = redact_bounded(summary, TRACE_SUMMARY_CAP, redact=_redact_trace_text)
        ...
```

with, at module level:

```python
TRACE_SUMMARY_CAP = 500


def _redact_trace_text(text: str) -> str:
    return redact_secrets(text, secret_env_values())
```

and `from embry0.safety.redaction import redact_bounded, redact_secrets, secret_env_values`. The existing trace tests that assert an exact summary (`test_trace_summary_is_the_provider_sentence`) keep passing: a sentence without secrets is unchanged.

Spec amendments. 3.6: replace "Per-sandbox bearers and credential secrets are already registered via `register_known_secrets`; the executor service registers the job's resolved credential secrets at job start so the pattern-based scrub is complemented by exact-value scrubbing." with "Registry credential secrets are registered with `register_known_secrets` at startup and after create or rotate; the sandbox manager registers each per-sandbox bearer when it enrolls the sandbox (the bearer matches no pattern on its own), and the executor service registers the job's resolved LLM secrets at job start (which covers env-fallback credentials). Every scrub uses `secret_env_values()`, the process's secret env values plus the registered set." 3.9: replace "A malformed line from the sandbox (not JSON, or JSON without `type`) is wrapped as `system` with the raw text, as today, and counted." with "A malformed line from the sandbox (not JSON) is dropped by the agent runner with an `event_parse_failed` log line, as today; wrapping it as a `system` event is not part of this workstream."

- [ ] **Step 5: Run, lint, commit**

Run: `.venv/bin/python -m pytest tests/unit/services tests/unit/execution tests/unit/orchestration tests/unit/safety -q 2>&1 | tail -3` (Postgres for the `requires_postgres` tests among them) and `.venv/bin/ruff check embry0 tests && .venv/bin/ruff format --check embry0/services embry0/execution/sandbox_manager.py embry0/orchestration/nodes/agent.py tests/unit/services tests/unit/execution tests/unit/orchestration && .venv/bin/mypy embry0/services/job_events.py embry0/services/_executor_run.py embry0/execution/sandbox_manager.py embry0/orchestration/nodes/agent.py`
Expected: all passed; clean.

```bash
git add embry0/services/job_events.py embry0/services/_executor_run.py embry0/execution/sandbox_manager.py embry0/orchestration/nodes/agent.py tests docs/superpowers/specs/2026-10-15-trace-fidelity-design.md
git commit -q -F - <<'EOF'
feat(services): redact every event before it is persisted or published

The orchestrator stored and forwarded sandbox events verbatim. The
job-events core now scrubs every string value, recursively, with the
process's secret env values and the registered set before the persist
and the publish, and drops an event whose scrub raises rather than let
it through. The per-sandbox bearer and the job's resolved LLM secrets
are registered with the scrubber the moment they exist, and the trace
summary is redacted before its cap.

Refs: #39

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 8: The persist-failed alert rule, shown firing

**Files:**
- Create: `infra/prometheus/job-events.rules.yml`, `infra/prometheus/job-events.rules.test.yml`
- Modify: `.github/workflows/ci.yml:213-217`, `docs/ops/verification.md:29-31`, `docs/ops/metrics.md` (Jobs section)
- Create: `tests/unit/infra/test_alert_rule_files_are_run.py`

**Interfaces:**
- Consumes: the counter `embry0_job_events_persist_failed_total` from Task 1.

- [ ] **Step 1: Write the failing test**

`tests/unit/infra/test_alert_rule_files_are_run.py`:

```python
"""Every promtool rule test under infra/prometheus/ is named in the CI job and the verification doc.

The CI job runs an explicit file list, so a new rule test that is not added
there never runs and its rule is never shown able to fire (CLAUDE.md).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _rule_test_files() -> list[str]:
    return sorted(p.name for p in (REPO / "infra" / "prometheus").glob("*.rules.test.yml"))


def test_ci_runs_every_rule_test_file() -> None:
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    files = _rule_test_files()
    assert "job-events.rules.test.yml" in files, "plan B adds the job-events rule test"
    missing = [f for f in files if f not in ci]
    assert not missing, f"rule tests not run by the CI promtool step: {missing}"


def test_verification_doc_names_every_rule_test_file() -> None:
    doc = (REPO / "docs" / "ops" / "verification.md").read_text(encoding="utf-8")
    missing = [f for f in _rule_test_files() if f not in doc]
    assert not missing, f"rule tests missing from docs/ops/verification.md: {missing}"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/infra/test_alert_rule_files_are_run.py -q 2>&1 | tail -4`
Expected: both FAIL ("plan B adds the job-events rule test").

- [ ] **Step 3: The rule and its test**

`infra/prometheus/job-events.rules.yml`:

```yaml
# Job-event persistence failures (spec 3.5, #39). Load this into the
# Prometheus that scrapes the orchestrator's /metrics (docs/ops/metrics.md).
#
# embry0_job_events_persist_failed_total does not exist as a series until the
# first failure, so the expression is `or vector(0)`: it evaluates to 0 on a
# healthy deployment instead of to nothing (a rule that produces no series
# cannot be told from a rule that never fires, CLAUDE.md). The sum has no
# labels, so there is no `and on(...)` join to get wrong (#371).
# Tested by job-events.rules.test.yml (promtool test rules).
groups:
  - name: embry0-job-events
    rules:
      - alert: Embry0JobEventsNotPersisted
        expr: |
          (sum(increase(embry0_job_events_persist_failed_total[15m])) or vector(0)) > 0
        for: 0m
        labels:
          severity: warning
        annotations:
          summary: "Job events are failing to persist"
          description: >-
            {{ $value | humanize }} job event(s) in the last 15 minutes were
            published to live clients but could not be written to job_logs, so
            no reconnecting client can replay them. Check the orchestrator log
            for event_persist_failed and the database.
```

`infra/prometheus/job-events.rules.test.yml`:

```yaml
# promtool test rules job-events.rules.test.yml
rule_files:
  - job-events.rules.yml

evaluation_interval: 1m

tests:
  # No series at all (a deployment that never failed a persist): the rule
  # evaluates (vector(0)) and stays quiet.
  - interval: 1m
    input_series: []
    alert_rule_test:
      - eval_time: 30m
        alertname: Embry0JobEventsNotPersisted
        exp_alerts: []

  # The counter exists and does not move: quiet.
  - interval: 1m
    input_series:
      - series: 'embry0_job_events_persist_failed_total{instance="embry0-orchestrator:8000",job="embry0_orchestrator_metrics"}'
        values: '3x60'
    alert_rule_test:
      - eval_time: 30m
        alertname: Embry0JobEventsNotPersisted
        exp_alerts: []

  # Two failures at minute 20: fires (the proof the expression can produce a
  # series at all), and is quiet again once 15 minutes have passed.
  - interval: 1m
    input_series:
      - series: 'embry0_job_events_persist_failed_total{instance="embry0-orchestrator:8000",job="embry0_orchestrator_metrics"}'
        values: '0x20 2x60'
    alert_rule_test:
      - eval_time: 22m
        alertname: Embry0JobEventsNotPersisted
        exp_alerts:
          - exp_labels:
              severity: warning
            exp_annotations:
              summary: "Job events are failing to persist"
              description: "2 job event(s) in the last 15 minutes were published to live clients but could not be written to job_logs, so no reconnecting client can replay them. Check the orchestrator log for event_persist_failed and the database."
      - eval_time: 50m
        alertname: Embry0JobEventsNotPersisted
        exp_alerts: []
```

Run it locally before trusting it: `docker run --rm -v "$PWD/infra/prometheus:/rules:ro" -w /rules --entrypoint promtool prom/prometheus:v3.14.0@sha256:5ce7540c3c00ef4ab0c9d2c995c6a5b9c421f44b4a115d97a2c7af3b1c21cbb0 test rules job-events.rules.test.yml`. Expected: `SUCCESS`. If promtool's `increase()` extrapolation makes the firing case's `$value` read other than `2` (it interpolates over the range), adjust the `description` expectation to the value promtool prints, keeping the `> 0` semantics; the point of the case is that the alert exists at 22m and not at 50m.

Add `job-events.rules.test.yml` to the `promtool test rules` command in `.github/workflows/ci.yml` (after `offhost-backup.rules.test.yml`) and to the same command in `docs/ops/verification.md`. In `docs/ops/metrics.md`, under the Jobs section's counter row from Task 1, add: "Alert `Embry0JobEventsNotPersisted` (`infra/prometheus/job-events.rules.yml`) fires on any increase in 15 minutes."

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/unit/infra -q 2>&1 | tail -3` and the promtool command above.
Expected: all passed; `SUCCESS`.

```bash
git add infra/prometheus/job-events.rules.yml infra/prometheus/job-events.rules.test.yml .github/workflows/ci.yml docs/ops/verification.md docs/ops/metrics.md tests/unit/infra/test_alert_rule_files_are_run.py
git commit -q -F - <<'EOF'
feat(infra): alert when job events fail to persist

A persist failure used to be one warning line nobody read. The counter
from the job-events core gets a rule with or vector(0), so it evaluates
on a deployment that never failed, and a promtool test that shows it
firing and going quiet again. The rule test joins the explicit list CI
runs, and a unit test keeps that list complete.

Refs: #39

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---
### Task 9: QA status on the job stream; the QA nodes bracket themselves

**Files:**
- Modify: `embry0/execution/events.py` (`QaSubtaskStatusEvent.boot_phase` becomes a dict; constructor parameter type)
- Regenerate: `docs/contracts/events.json`, `docs/api.md` (events table), `frontend/src/lib/types/events.ts` (`QaSubtaskStatusContract.boot_phase?: Record<string, unknown>`; the `EVENT_FIELDS` row is unchanged, the field was already optional)
- Modify: `embry0/workflows/qa/orchestrator_helpers.py:100-210` (`fan_out_subtasks`), `embry0/workflows/qa/subtask_nodes.py:1068-1135` (`emit_result_node`), `embry0/workflows/qa/orchestrator.py` (`qa_orchestrator_node` wrapper, `init_orchestrator_node`), `embry0/workflows/qa/orchestrator_report.py` (`qa_report_node`)
- Modify: `tests/unit/workflows/qa/test_qa_live_events.py`, `tests/unit/workflows/qa/test_subtask_nodes.py`, `tests/unit/workflows/qa/test_orchestrator.py`, `tests/unit/execution/test_events.py`
- Modify: `docs/superpowers/specs/2026-10-15-trace-fidelity-design.md` section 3.7

**Interfaces:**
- Produces: `events.qa_subtask_status(..., boot_phase: dict[str, Any] | None = None)`; on the job stream, `qa_subtask_status` at sub-task start (`status="running"`) and at its end (final status, `duration_ms`, `failure_summary`, `boot_phase`); `node_started`/`node_completed` with `node="init_orchestrator"`, `"orchestrate_qa"` (its `node_completed` carries `action=<overall_status>`), `"qa_report"`.
- Consumes: `configurable["qa_parent_writer"]` (existing), `get_stream_writer()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/execution/test_events.py`:

```python
def test_qa_subtask_status_carries_the_boot_phase_dict() -> None:
    boot_phase = {"outcome": "timeout", "attempts": 12, "duration_ms": 600_000}
    event = events.qa_subtask_status(app="hub", status="boot_failure", duration_ms=3000, boot_phase=boot_phase)
    assert event["boot_phase"] == boot_phase, "boot_phase is the structured boot outcome, not a string"
```

Append to `tests/unit/workflows/qa/test_qa_live_events.py`:

```python
@pytest.mark.asyncio
async def test_fan_out_emits_running_status_through_the_parent_writer(monkeypatch):
    """spec 3.7: the status change rides the job stream, not only the QA bus."""
    events_out: list[dict] = []
    monkeypatch.setattr(
        orchestrator_helpers, "run_subtask", AsyncMock(side_effect=lambda resolved, **kw: _result(resolved.app_name))
    )
    await orchestrator_helpers.fan_out_subtasks(
        [_resolved("hub")], parent_run_id="job-p1", repo="x/y", branch_name="main", max_concurrent=1,
        config={"configurable": {"qa_parent_writer": events_out.append}},
    )
    running = [e for e in events_out if e["type"] == "qa_subtask_status"]
    assert running and running[0]["app"] == "hub" and running[0]["status"] == "running"
    assert "run_id" not in running[0], "the job id is not the contract's run_id (an agent run); the orchestrator stamps job_id"
    types = [e["type"] for e in events_out]
    assert types.index("qa_subtask_started") < types.index("qa_subtask_status") < types.index("qa_subtask_finished")
```

Append to `tests/unit/workflows/qa/test_subtask_nodes.py` next to `test_emit_result_node_publishes_subtask_status_to_event_bus`:

```python
@pytest.mark.asyncio
async def test_emit_result_node_emits_the_final_status_through_the_parent_writer():
    from embry0.workflows.qa.subtask_nodes import emit_result_node

    events_out: list[dict] = []
    boot_phase = {"outcome": "timeout", "attempts": 12, "duration_ms": 600_000}
    now = time.monotonic()
    state = {
        "app_name": "hub", "parent_run_id": "JOB-42", "status": SubTaskStatus.BOOT_FAILURE,
        "failure_summary": "boot timeout", "started_at": now - 3.0, "completed_at": now, "raw_result": {}, "boot_phase": boot_phase,
    }
    await emit_result_node(state, {"configurable": {"qa_parent_writer": events_out.append}})
    status = next(e for e in events_out if e["type"] == "qa_subtask_status")
    assert status["app"] == "hub" and status["status"] == "boot_failure"
    assert status["duration_ms"] >= 2900 and status["failure_summary"] == "boot timeout"
    assert status["boot_phase"] == boot_phase
```

Append to `tests/unit/workflows/qa/test_orchestrator.py` next to `test_qa_orchestrator_publishes_done_event_on_success` (reuse its state/config fakes; it patches `_qa_orchestrator_node_impl`):

```python
@pytest.mark.asyncio
async def test_qa_orchestrator_node_brackets_itself_on_the_job_stream(monkeypatch):
    """spec 3.7: the run-level done is node_completed(node="orchestrate_qa", action=<overall_status>)."""
    from embry0.workflows.qa import orchestrator as orch

    events_out: list[dict] = []
    monkeypatch.setattr(orch, "get_stream_writer", lambda: events_out.append)
    monkeypatch.setattr(
        orch, "_qa_orchestrator_node_impl",
        AsyncMock(return_value={"qa": {"outcome": {"overall_status": "passed"}}}),
    )
    await orch.qa_orchestrator_node({"job_id": "JOB-1"}, {"configurable": {}})
    started = next(e for e in events_out if e["type"] == "node_started")
    completed = next(e for e in events_out if e["type"] == "node_completed")
    assert started["node"] == "orchestrate_qa" and completed["node"] == "orchestrate_qa"
    assert completed["action"] == "passed"
    assert events_out.index(started) < events_out.index(completed)
```

(If `get_stream_writer` is imported into `orchestrator.py` under another name or from `langgraph.config` at call time, patch where the wrapper looks it up; the existing `_qa_orchestrator_node_impl` tests show the pattern.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/execution/test_events.py tests/unit/workflows/qa -q -k "boot_phase_dict or running_status_through or final_status_through or brackets_itself" 2>&1 | tail -8`
Expected: all four FAIL (the constructor types `boot_phase` as `str`; no `qa_subtask_status` on the writer; no `node_started` from the wrapper).

- [ ] **Step 3: The contract**

In `embry0/execution/events.py`: `QaSubtaskStatusEvent.boot_phase: NotRequired[dict[str, Any]]` and `qa_subtask_status(..., boot_phase: dict[str, Any] | None = None, ...)`. Regenerate: `.venv/bin/python -m embry0.execution.events --write-contract docs/contracts/events.json` (the field list does not change, so the JSON may be byte-identical; run it anyway) and paste `--render-markdown` between the `docs/api.md` markers. In `frontend/src/lib/types/events.ts` change `boot_phase?: string` to `boot_phase?: Record<string, unknown>` on `QaSubtaskStatusContract`.

- [ ] **Step 4: The emit sites**

`fan_out_subtasks`, right after the `qa_subtask_started` write and before the bus publish:

```python
            writer(events.qa_subtask_status(app=resolved.app_name, status="running"))
```

`emit_result_node`, before the bus block (import `_parent_writer` is in the same module):

```python
    # spec 3.7: the final per-app status rides the job stream too, so it is
    # persisted and replayed; the QA bus below keeps feeding the SSE route.
    _parent_writer(config)(
        events.qa_subtask_status(
            app=state["app_name"],
            status=str(status),
            duration_ms=duration_ms,
            failure_summary=failure_summary,
            boot_phase=state.get("boot_phase") if isinstance(state.get("boot_phase"), dict) else None,
        )
    )
```

`qa_orchestrator_node` (the wrapper), at its start and end:

```python
    try:
        writer = get_stream_writer()
    except RuntimeError:  # outside a runnable context (unit tests)
        writer = lambda _event: None  # noqa: E731
    writer(events.node_started(node="orchestrate_qa"))
    out = await _qa_orchestrator_node_impl(state, config)
    ...existing bus block...
    # spec 3.7: the run-level outcome on the job stream (the QA bus `done`
    # keeps closing the SSE route).
    writer(events.node_completed(node="orchestrate_qa", action=str(overall_status) if overall_status else None))
    return out
```

(`overall_status` is already computed in the wrapper for the bus; hoist it above the `if bus is not None` so both uses see it.) `init_orchestrator_node` emits `events.node_started(node="init_orchestrator")` first and `events.node_completed(node="init_orchestrator")` on each return path that is not a raise; `qa_report_node` likewise with `node="qa_report"`. Both already resolve a writer or can with the same `get_stream_writer()` try/except.

Amend spec 3.7, first bullet: replace "The QA orchestrator publishes `qa_subtask_status` and the QA `done` through the parent writer as well as the QA bus, so they persist and replay on the job stream." with "The QA orchestrator emits `qa_subtask_status` through the parent writer at sub-task start and end (as well as to the QA bus), and the three QA graph nodes emit `node_started`/`node_completed`, the orchestrator's `node_completed` carrying `action=<overall_status>`, which is the run-level outcome on the job stream; the QA bus `done` keeps closing the SSE route and gains no contract type." In the 3.1 table, the `qa_subtask_status` row's payload note: `boot_phase` is a dict.

- [ ] **Step 5: Run, lint, commit**

Run: `.venv/bin/python -m pytest tests/unit/workflows/qa tests/unit/execution -q 2>&1 | tail -3 && (cd frontend && npx vitest run src/lib/types && npx tsc -b) && .venv/bin/ruff check embry0/workflows/qa embry0/execution/events.py tests/unit/workflows/qa && .venv/bin/ruff format --check embry0/workflows/qa tests/unit/workflows/qa && .venv/bin/mypy embry0/workflows/qa embry0/execution/events.py`
Expected: all passed (the contract tests included); clean.

```bash
git add embry0/execution/events.py docs/contracts/events.json docs/api.md frontend/src/lib/types/events.ts embry0/workflows/qa tests/unit/workflows/qa tests/unit/execution/test_events.py docs/superpowers/specs/2026-10-15-trace-fidelity-design.md
git commit -q -F - <<'EOF'
feat(qa): sub-task status and node brackets on the job stream

QA state changes lived only on the second bus, which has no replay and
drops the oldest event when a subscriber lags, so the job page could
not show them. Each sub-task's running and final status now also rides
the parent writer into job_logs, with boot_phase as the structured
outcome it already was, and the three QA nodes emit node_started and
node_completed, the orchestrator's carrying the run's overall status.

Fixes: #40

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 10: `pipeline_graph` for onboarding and QA jobs

**Files:**
- Create: `embry0/workflows/pipeline_graphs.py`
- Modify: `embry0/workflows/onboard/nodes.py:55-80` (`init_onboard_node`), `embry0/workflows/qa/orchestrator.py` (`init_orchestrator_node`)
- Create: `tests/unit/workflows/test_pipeline_graphs.py`
- Modify: `tests/unit/workflows/onboard/test_nodes.py`, `tests/unit/workflows/qa/test_orchestrator.py`

**Interfaces:**
- Produces: `embry0.workflows.pipeline_graphs.onboard_pipeline_graph(job_id: str) -> dict[str, Any]`, `qa_pipeline_graph(job_id: str) -> dict[str, Any]`, both in the frontend `PipelineGraph` shape (`graph_id`, `name`, `description`, `nodes[]` with `node_id`, `agent_type`, `label`, `position`, `physical_node`, `edges[]` with `edge_id`, `source`, `target`, `edge_type`, `metadata`); `ONBOARD_NODE_IDS`, `QA_NODE_IDS` tuples.
- Consumes: `events.pipeline_graph(graph=..., node=...)`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/workflows/test_pipeline_graphs.py`:

```python
"""Static pipeline graphs for the jobs whose graph is not planned by triage (spec 3.8 / #40)."""

from __future__ import annotations

from embry0.workflows.pipeline_graphs import onboard_pipeline_graph, qa_pipeline_graph


def _compiled_node_names(workflow) -> set[str]:
    return {n for n in workflow.compile().get_graph().nodes if not n.startswith("__")}


def test_onboard_graph_names_exactly_the_compiled_nodes() -> None:
    from embry0.workflows.onboard.graph import OnboardWorkflow

    graph = onboard_pipeline_graph("job-1")
    assert {n["node_id"] for n in graph["nodes"]} == _compiled_node_names(OnboardWorkflow()), (
        "the static graph must list the LangGraph nodes, no more, no less"
    )
    assert all(n["physical_node"] == n["node_id"] for n in graph["nodes"]), "onboard nodes emit node_started under their own id"
    assert graph["graph_id"] == "onboard-job-1"


def test_qa_graph_names_exactly_the_compiled_nodes() -> None:
    from embry0.workflows.qa.graph import QAWorkflow

    graph = qa_pipeline_graph("job-2")
    assert {n["node_id"] for n in graph["nodes"]} == _compiled_node_names(QAWorkflow())
    assert [n["node_id"] for n in graph["nodes"]] == ["init_orchestrator", "orchestrate_qa", "qa_report"]
    assert graph["graph_id"] == "qa-job-2"


def test_graphs_have_the_frontend_shape() -> None:
    for graph in (onboard_pipeline_graph("j"), qa_pipeline_graph("j")):
        assert set(graph) >= {"graph_id", "name", "description", "nodes", "edges", "metadata"}
        for node in graph["nodes"]:
            assert set(node) >= {"node_id", "agent_type", "label", "position", "physical_node"}
            assert set(node["position"]) == {"x", "y"}
        ids = {n["node_id"] for n in graph["nodes"]}
        for edge in graph["edges"]:
            assert set(edge) == {"edge_id", "source", "target", "edge_type"}
            assert edge["source"] in ids and edge["target"] in ids, f"dangling edge {edge}"


def test_onboard_graph_carries_the_retry_edges() -> None:
    edges = {(e["source"], e["target"]) for e in onboard_pipeline_graph("j")["edges"]}
    assert {("validate", "analyze"), ("smoke", "analyze")} <= edges, "the retry loops are part of the real graph"
    assert ("init_onboard", "analyze") in edges and ("write_config", "cleanup") in edges
```

Append to `tests/unit/workflows/onboard/test_nodes.py` (the same fakes as `test_init_onboard_clones_the_repo_forge` at line 247: `_Sb`, `_Profiles`, `_Docker`, `_ForgeRepos`, the patched `prep_qa_sandbox_clone`):

```python
@pytest.mark.asyncio
async def test_init_onboard_emits_the_pipeline_graph_first(monkeypatch):
    """spec 3.8: the onboarding job page no longer shows an empty map."""
    from types import SimpleNamespace

    from embry0.workflows.onboard import nodes as onboard_nodes
    from embry0.workflows.onboard.nodes import init_onboard_node

    events_out: list[dict] = []
    monkeypatch.setattr(onboard_nodes, "_writer", lambda: events_out.append)

    class _Sb:
        async def create(self, job_id, profile=None, env=None, repo=None, forge="github", **_kw):
            return "cid-1", "tok-" + "A" * 40

    class _Profiles:
        async def get(self, name):
            return {"name": "slim"}

    class _Docker:
        def _build_base_cmd(self):
            return ["docker"]

        def build_exec_cmd(self, cid, cmd):
            return ["docker", "exec", cid, *cmd]

        async def run_cmd(self, cmd, timeout=None):
            return ""

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.workflows.qa._subtask_prep.prep_qa_sandbox_clone", fake_clone)
    await init_onboard_node(
        {"job_id": "job-1", "repo": "acme/group/backend", "branch_name": "dev"},
        {
            "configurable": {
                "docker": _Docker(),
                "sandbox_manager": _Sb(),
                "profiles_repo": _Profiles(),
                "repos_repo": _ForgeRepos("gitlab"),
                "embry0_config": SimpleNamespace(gitlab_url="https://gitlab.example.com"),
            }
        },
    )
    types = [e["type"] for e in events_out]
    assert "pipeline_graph" in types, "init_onboard must emit the graph"
    assert types.index("node_started") < types.index("pipeline_graph")
    graph = next(e for e in events_out if e["type"] == "pipeline_graph")
    assert graph["node"] == "init_onboard" and graph["graph"]["graph_id"] == "onboard-job-1"
```

Append to `tests/unit/workflows/qa/test_orchestrator.py` (the same fakes as `test_init_orchestrator_clones_the_repo_forge` at line 2424: `_rav1251_harness()`, `_FakeQAConfigs`, `_QA_YAML_V2`, `_ForgeRepos`, the patched `prep_qa_sandbox_clone`):

```python
@pytest.mark.asyncio
async def test_init_orchestrator_emits_the_pipeline_graph(monkeypatch):
    from types import SimpleNamespace

    from embry0.workflows.qa import orchestrator as orch

    events_out: list[dict] = []
    monkeypatch.setattr(orch, "get_stream_writer", lambda: events_out.append)
    sb, profiles, docker = _rav1251_harness()

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.workflows.qa._subtask_prep.prep_qa_sandbox_clone", fake_clone)
    await init_orchestrator_node(
        {"job_id": "run-graph", "repo": "acme/group/backend", "branch_name": "dev", "qa": {}},
        {
            "configurable": {
                "docker": docker,
                "sandbox_manager": sb,
                "profiles_repo": profiles,
                "proxy_manager": None,
                "qa_configs_repo": _FakeQAConfigs({"acme/group/backend": _QA_YAML_V2}),
                "repos_repo": _ForgeRepos("gitlab"),
                "embry0_config": SimpleNamespace(gitlab_url="https://gitlab.example.com"),
            }
        },
    )
    types = [e["type"] for e in events_out]
    assert types.index("node_started") < types.index("pipeline_graph"), "node_started (Task 9) comes first, then the graph"
    graph = next(e for e in events_out if e["type"] == "pipeline_graph")
    assert graph["node"] == "init_orchestrator" and graph["graph"]["graph_id"] == "qa-run-graph"
    assert [n["node_id"] for n in graph["graph"]["nodes"]] == ["init_orchestrator", "orchestrate_qa", "qa_report"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/workflows/test_pipeline_graphs.py tests/unit/workflows/onboard/test_nodes.py tests/unit/workflows/qa/test_orchestrator.py -q -k "pipeline_graph" 2>&1 | tail -8`
Expected: collection error on `embry0.workflows.pipeline_graphs`; the two node tests FAIL ("must emit the graph").

- [ ] **Step 3: The module**

`embry0/workflows/pipeline_graphs.py`:

```python
"""Static pipeline graphs for the jobs whose graph triage does not plan.

issue-to-pr jobs get their graph from plan_route (built from the route plan);
onboarding and standalone QA jobs run a fixed LangGraph, so their graph is
written down here in the frontend PipelineGraph shape and emitted by the
first node. tests/unit/workflows/test_pipeline_graphs.py pins each node list
to the compiled graph's nodes, so the two cannot drift.

``physical_node`` is the LangGraph node whose node_started/node_completed
events light the graph node; every node here emits under its own id.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

_SPACING_X = 230
_BASE_X = 40
_BASE_Y = 120

#: (node_id, agent_type, label). The ids are the LangGraph node names.
ONBOARD_NODE_IDS: tuple[tuple[str, str, str], ...] = (
    ("init_onboard", "onboarding", "Init"),
    ("analyze", "onboarding", "Analyze"),
    ("validate", "onboarding", "Validate"),
    ("smoke", "onboarding", "Smoke"),
    ("write_config", "onboarding", "Write config"),
    ("cleanup", "onboarding", "Cleanup"),
)
#: (source, target, edge_type). "flow" is the forward path, "feedback" a retry.
_ONBOARD_EDGES: tuple[tuple[str, str, str], ...] = (
    ("init_onboard", "analyze", "flow"),
    ("analyze", "validate", "flow"),
    ("validate", "smoke", "flow"),
    ("smoke", "write_config", "flow"),
    ("write_config", "cleanup", "flow"),
    ("validate", "analyze", "feedback"),
    ("smoke", "analyze", "feedback"),
    ("validate", "cleanup", "flow"),
    ("smoke", "cleanup", "flow"),
)

QA_NODE_IDS: tuple[tuple[str, str, str], ...] = (
    ("init_orchestrator", "qa", "Init"),
    ("orchestrate_qa", "qa", "QA"),
    ("qa_report", "qa", "Report"),
)
_QA_EDGES: tuple[tuple[str, str, str], ...] = (
    ("init_orchestrator", "orchestrate_qa", "flow"),
    ("orchestrate_qa", "qa_report", "flow"),
)


def _static_graph(
    *,
    graph_id: str,
    name: str,
    description: str,
    nodes: Sequence[tuple[str, str, str]],
    edges: Sequence[tuple[str, str, str]],
) -> dict[str, Any]:
    return {
        "graph_id": graph_id,
        "name": name,
        "description": description,
        "nodes": [
            {
                "node_id": node_id,
                "agent_type": agent_type,
                "label": label,
                "position": {"x": _BASE_X + index * _SPACING_X, "y": _BASE_Y},
                "physical_node": node_id,
            }
            for index, (node_id, agent_type, label) in enumerate(nodes)
        ],
        "edges": [
            {"edge_id": f"{source}->{target}", "source": source, "target": target, "edge_type": edge_type}
            for source, target, edge_type in edges
        ],
        # PipelineMetadata (frontend/src/lib/types/pipelines.ts): a fixed graph has
        # no budget or loop cap of its own and was not planned by triage.
        "metadata": {"max_total_budget_usd": 0, "max_total_loops": 0, "created_by": "auto", "template_id": None},
    }


def onboard_pipeline_graph(job_id: str) -> dict[str, Any]:
    return _static_graph(
        graph_id=f"onboard-{job_id}",
        name="onboarding",
        description="Analyze the repository and write its QA config",
        nodes=ONBOARD_NODE_IDS,
        edges=_ONBOARD_EDGES,
    )


def qa_pipeline_graph(job_id: str) -> dict[str, Any]:
    return _static_graph(
        graph_id=f"qa-{job_id}",
        name="standalone QA",
        description="Boot the apps and run the browser QA agent per app",
        nodes=QA_NODE_IDS,
        edges=_QA_EDGES,
    )
```

`"flow"` and `"feedback"` are the two members of `EdgeType` in `frontend/src/lib/types/pipelines.ts:35`, and `metadata` above carries every key `PipelineMetadata` (`pipelines.ts:51-56`) requires, with `created_by: "auto"` from its union. No frontend change.

- [ ] **Step 4: The emit sites**

`init_onboard_node`, right after `writer(events.node_started(node="init_onboard"))`:

```python
    writer(events.pipeline_graph(graph=onboard_pipeline_graph(job_id), node="init_onboard"))
```

`init_orchestrator_node`, right after the `node_started` it emits since Task 9:

```python
    writer(events.pipeline_graph(graph=qa_pipeline_graph(str(state.get("job_id") or "")), node="init_orchestrator"))
```

- [ ] **Step 5: Run, lint, commit**

Run: `.venv/bin/python -m pytest tests/unit/workflows -q 2>&1 | tail -3 && (cd frontend && npx tsc -b) && .venv/bin/ruff check embry0/workflows tests/unit/workflows && .venv/bin/ruff format --check embry0/workflows tests/unit/workflows && .venv/bin/mypy embry0/workflows/pipeline_graphs.py embry0/workflows/onboard/nodes.py embry0/workflows/qa/orchestrator.py`
Expected: all passed; clean.

```bash
git add embry0/workflows/pipeline_graphs.py embry0/workflows/onboard/nodes.py embry0/workflows/qa/orchestrator.py tests/unit/workflows/test_pipeline_graphs.py tests/unit/workflows/onboard/test_nodes.py tests/unit/workflows/qa/test_orchestrator.py
git commit -q -F - <<'EOF'
feat(workflows): pipeline_graph for onboarding and standalone QA jobs

Only issue-to-pr jobs emitted a pipeline_graph, so the onboarding and QA
job pages waited forever for a map. Both workflows run a fixed graph,
written down here in the frontend's shape and emitted by the first node;
a test pins each node list to the compiled LangGraph so they cannot
drift.

Refs: #40

Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ
EOF
```

---

### Task 11: Whole-tree verification and the pull request

- [ ] **Step 1: Merge-hazard checks**

Run: `git grep -nE '^(<<<<<<< |=======$|>>>>>>> )' -- . ':!*.md' | head; for f in $(git diff --name-only origin/main -- '*.py'); do [ -f "$f" ] && grep -oE '^(async )?def [A-Za-z_0-9]+' "$f" | sed 's/async //' | sort | uniq -d | sed "s|^|$f: |"; done`
Expected: no output.

- [ ] **Step 2: Contract, guards, baseline and full suite**

`.venv/bin/python -m pytest tests/unit/execution/test_event_contract.py tests/unit/services/test_job_finished_sites.py tests/unit/services/test_outcomes_credential_uses.py -q`: all passed. Then per CLAUDE.md: a throwaway Postgres, one database for a clean `git archive origin/main` export and one for this branch, `.venv/bin/python -m pytest tests/unit -q` in each with `TEST_DATABASE_URL` set, failure sets diffed (`-rfE`). `cd frontend && npm ci && npx tsc -b && npm run lint && npm run test -- --run && npm run build`. The promtool command from Task 8.
Expected: the branch's failure set is empty (so is the baseline's: 9940 passed on 7f92ee47); frontend green; promtool `SUCCESS`.

- [ ] **Step 3: Sandbox image build smoke**

Run: `docker build -f infra/Dockerfile.sandbox -t embry0-sandbox:trace-fidelity-b . 2>&1 | tail -3 && docker run --rm --entrypoint python3 embry0-sandbox:trace-fidelity-b -c "from embry0.sandbox import runner; from embry0.agents import executor, executor_xai; from embry0.safety.redaction import redact_event; print('ok')"`
Expected: build succeeds, prints `ok` (the runner, both executors and the redactor import inside the image).

- [ ] **Step 4: Open the pull request**

Use `superpowers:finishing-a-development-branch`. The PR body carries `Fixes: #37`, `Fixes: #38`, `Fixes: #39`, `Fixes: #40`, the deploy note "rebuild the sandbox images (executors, runner, contract module and redaction changed): `docs/ops/deploy.md`, Images: registry, pins and rebuilds; load `infra/prometheus/job-events.rules.yml` into the Prometheus that scrapes the orchestrator", the note that the spec amendments of sections 3.5, 3.6, 3.7 and 3.9 are in this PR, and ends with `https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ`. Do not merge; do not deploy.
