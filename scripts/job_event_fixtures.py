#!/usr/bin/env python3
"""Generate tests/fixtures/job-events/*.json from the event constructors (#41).

One fixture per job kind, in the shape GET /jobs/{id}/logs/events returns: the
persisted payload plus event_seq, with job_id and prev_seq stamped as
embry0.services.job_events does. Deterministic: a fixed clock and fixed ids.
Run with no arguments to rewrite the files; --check exits 1 when they differ.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from embry0.execution import events as ev
from embry0.workflows.issue_to_pr.plan_route import build_pipeline_graph
from embry0.workflows.pipeline_graphs import ONBOARD_NODE_IDS, QA_NODE_IDS, onboard_pipeline_graph, qa_pipeline_graph

OUT_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "job-events"
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
FIRST_SEQ = 1000
SEQ_STEP = 3  # event_seq is job_logs.id, shared by every job: never contiguous for one job


class Stream:
    """Stamps job_id, timestamp, event_seq and prev_seq the way the orchestrator and the API do."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.events: list[dict[str, Any]] = []
        self._seq = FIRST_SEQ
        self._prev: int | None = None

    def add(self, event: dict[str, Any]) -> dict[str, Any]:
        stamped = {**event, "job_id": self.job_id}
        stamped["timestamp"] = (EPOCH + timedelta(seconds=len(self.events))).isoformat().replace("+00:00", "Z")
        if self._prev is not None:
            stamped["prev_seq"] = self._prev
        stamped["event_seq"] = self._seq
        self._prev = self._seq
        self._seq += SEQ_STEP
        self.events.append(stamped)
        return stamped


def issue_to_pr() -> list[dict[str, Any]]:
    s = Stream("job-fixture-issue-to-pr")
    graph = build_pipeline_graph(job_id=s.job_id, plan=[{"agent_type": "developer"}, {"agent_type": "reviewer"}])
    s.add(ev.pipeline_graph(graph=graph, node="plan_route"))
    # triage: one short run
    s.add(ev.node_started(node="triage", attempt=1, agent="triage"))
    r1 = dict(run_id="run-fixture-1", node="triage", agent="triage")
    s.add(ev.agent_started(model="claude-sonnet-5-5", max_turns=20, **r1))
    s.add(ev.turn_start(turn_number=1, model="claude-sonnet-5-5", **r1))
    s.add(ev.thinking(text="The issue asks for a CLI flag.", **r1))
    s.add(
        ev.tool_call(
            tool_id="toolu_fx_01",
            tool_name="Read",
            summary="Read README.md",
            tool_input={"file_path": "README.md"},
            **r1,
        )
    )
    s.add(ev.tool_result(tool_id="toolu_fx_01", content="# example\n", is_error=False, **r1))
    s.add(ev.text(text="Routing to developer then reviewer.", **r1))
    s.add(
        ev.turn_usage(
            input_tokens=1200,
            output_tokens=80,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd=0.0042,
            model="claude-sonnet-5-5",
            **r1,
        )
    )
    s.add(
        ev.cost_update(
            cost_usd=0.0042,
            duration_ms=3100,
            duration_api_ms=2900,
            num_turns=1,
            tokens_in=1200,
            tokens_out=80,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            **r1,
        )
    )
    s.add(
        ev.agent_completed(
            is_error=False,
            error_code=None,
            error_message="",
            output="route: developer, reviewer",
            cost_usd=0.0042,
            duration_ms=3100,
            tools_called={"Read": 1},
            policy_denials={},
            input_tokens=1200,
            output_tokens=80,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            **r1,
        )
    )
    s.add(ev.node_completed(node="triage", attempt=1, agent="triage", action="route", job_kind="code"))
    # developer: a subagent, a compaction, a denied tool, an unpriced turn
    s.add(ev.node_started(node="developer", attempt=1, agent="developer"))
    r2 = dict(run_id="run-fixture-2", node="developer", agent="developer")
    s.add(ev.agent_started(model="claude-opus-5-5", max_turns=50, **r2))
    s.add(ev.turn_start(turn_number=1, model="claude-opus-5-5", **r2))
    s.add(
        ev.tool_call(
            tool_id="toolu_fx_task",
            tool_name="Task",
            summary="Explore the CLI module",
            tool_input={"prompt": "Find the argparse setup", "subagent_type": "Explore"},
            **r2,
        )
    )
    s.add(ev.subagent_start(tool_id="toolu_fx_task", name="Explore", **r2))
    s.add(
        ev.tool_call(
            tool_id="toolu_fx_sub1",
            tool_name="Grep",
            summary="Grep argparse",
            tool_input={"pattern": "argparse"},
            parent_tool_use_id="toolu_fx_task",
            **r2,
        )
    )
    s.add(
        ev.tool_result(
            tool_id="toolu_fx_sub1", content="cli.py:12", is_error=False, parent_tool_use_id="toolu_fx_task", **r2
        )
    )
    s.add(ev.subagent_end(tool_id="toolu_fx_task", is_error=False, **r2))
    s.add(ev.tool_result(tool_id="toolu_fx_task", content="argparse lives in cli.py", is_error=False, **r2))
    s.add(
        ev.turn_usage(
            input_tokens=5000,
            output_tokens=400,
            cache_read_tokens=3000,
            cache_creation_tokens=0,
            cost_usd=0.021,
            model="claude-opus-5-5",
            **r2,
        )
    )
    s.add(ev.context_compacted(pre_tokens=90000, **r2))
    s.add(ev.turn_start(turn_number=2, model="claude-opus-5-5", **r2))
    s.add(
        ev.tool_call(
            tool_id="toolu_fx_02",
            tool_name="Bash",
            summary="curl https://example.test",
            tool_input={"command": "curl https://example.test"},
            **r2,
        )
    )
    s.add(ev.error(message="Tool denied by policy: Bash(curl *)", code="ERR_TOOL_DENIED", tool_name="Bash", **r2))
    s.add(ev.tool_result(tool_id="toolu_fx_02", content="denied", is_error=True, **r2))
    s.add(
        ev.tool_call(
            tool_id="toolu_fx_03",
            tool_name="Edit",
            summary="Edit cli.py",
            tool_input={"file_path": "cli.py", "old_string": "a", "new_string": "b"},
            **r2,
        )
    )
    s.add(ev.tool_result(tool_id="toolu_fx_03", content="ok", is_error=False, **r2))
    s.add(
        ev.turn_usage(
            input_tokens=7000,
            output_tokens=600,
            cache_read_tokens=6000,
            cache_creation_tokens=0,
            cost_usd=None,
            model="claude-opus-5-5",
            **r2,
        )
    )
    s.add(
        ev.cost_update(
            cost_usd=0.048,
            duration_ms=48000,
            duration_api_ms=41000,
            num_turns=2,
            tokens_in=12000,
            tokens_out=1000,
            cache_read_tokens=9000,
            cache_creation_tokens=0,
            **r2,
        )
    )
    s.add(
        ev.agent_completed(
            is_error=False,
            error_code=None,
            error_message="",
            output="Added the --verbose flag.",
            cost_usd=0.048,
            duration_ms=48000,
            tools_called={"Task": 1, "Grep": 1, "Bash": 1, "Edit": 1},
            policy_denials={"Bash": 1},
            input_tokens=12000,
            output_tokens=1000,
            cache_read_tokens=9000,
            cache_creation_tokens=0,
            **r2,
        )
    )
    s.add(ev.node_completed(node="developer", attempt=1, agent="developer", ok=True, deliverables=0))
    # developer again: a retried node is a second run of the same node
    s.add(ev.node_started(node="developer", attempt=2, agent="developer"))
    r3 = dict(run_id="run-fixture-3", node="developer", agent="developer")
    s.add(ev.agent_started(model="claude-opus-5-5", max_turns=50, **r3))
    s.add(ev.turn_start(turn_number=1, model="claude-opus-5-5", **r3))
    s.add(
        ev.tool_call(
            tool_id="toolu_fx_04",
            tool_name="Bash",
            summary="pytest -q",
            tool_input={"command": "pytest -q"},
            **r3,
        )
    )
    s.add(ev.tool_result(tool_id="toolu_fx_04", content="3 passed", is_error=False, **r3))
    s.add(
        ev.turn_usage(
            input_tokens=2000,
            output_tokens=150,
            cache_read_tokens=1000,
            cache_creation_tokens=0,
            cost_usd=0.0068,
            model="claude-opus-5-5",
            **r3,
        )
    )
    s.add(
        ev.cost_update(
            cost_usd=0.0068,
            duration_ms=9000,
            duration_api_ms=8000,
            num_turns=1,
            tokens_in=2000,
            tokens_out=150,
            cache_read_tokens=1000,
            cache_creation_tokens=0,
            **r3,
        )
    )
    s.add(
        ev.agent_completed(
            is_error=False,
            error_code=None,
            error_message="",
            output="The tests pass.",
            cost_usd=0.0068,
            duration_ms=9000,
            tools_called={"Bash": 1},
            policy_denials={},
            input_tokens=2000,
            output_tokens=150,
            cache_read_tokens=1000,
            cache_creation_tokens=0,
            **r3,
        )
    )
    s.add(ev.node_completed(node="developer", attempt=2, agent="developer", ok=True, deliverables=0))
    # review, then a human interrupt, then the PR
    s.add(ev.node_started(node="review", attempt=1, agent="reviewer"))
    r4 = dict(run_id="run-fixture-4", node="review", agent="reviewer")
    s.add(ev.agent_started(model="claude-sonnet-5-5", max_turns=20, **r4))
    s.add(ev.turn_start(turn_number=1, model="claude-sonnet-5-5", **r4))
    s.add(ev.text(text="LGTM with one nit.", **r4))
    s.add(
        ev.cost_update(
            cost_usd=0.003,
            duration_ms=4000,
            duration_api_ms=3800,
            num_turns=1,
            tokens_in=900,
            tokens_out=60,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            **r4,
        )
    )
    s.add(
        ev.agent_completed(
            is_error=False,
            error_code=None,
            error_message="",
            output="approve",
            cost_usd=0.003,
            duration_ms=4000,
            tools_called={},
            policy_denials={},
            input_tokens=900,
            output_tokens=60,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            **r4,
        )
    )
    s.add(ev.review_decision(decision="approve", summary="LGTM with one nit.", node="review"))
    s.add(ev.node_completed(node="review", attempt=1, agent="reviewer", action="approve", round=1))
    s.add(
        ev.interrupt(
            node="review",
            reason="awaiting_human_merge",
            summary={
                "message": "Approve to open the PR",
                "reason": "awaiting_human_merge",
                "retry_count": 0,
                "options": ["approve", "reject"],
            },
        )
    )
    s.add(
        ev.pr_created(
            pr_url="https://forge.example.test/org/repo/pull/1", branch="embry0/job-fixture", node="finalize_output"
        )
    )
    s.add(
        ev.job_finished(
            status="completed",
            error_code=None,
            error_message=None,
            pr_url="https://forge.example.test/org/repo/pull/1",
            total_cost_usd=0.062,
        )
    )
    return s.events


def onboarding() -> list[dict[str, Any]]:
    s = Stream("job-fixture-onboarding")
    s.add(ev.pipeline_graph(graph=onboard_pipeline_graph(s.job_id), node=ONBOARD_NODE_IDS[0][0]))
    for node_id, agent_type, _label in ONBOARD_NODE_IDS:
        s.add(ev.node_started(node=node_id, attempt=1, agent=agent_type))
        if node_id == "analyze":
            r = dict(run_id="run-fixture-onboard-1", node=node_id, agent=agent_type)
            s.add(ev.agent_started(model="claude-sonnet-5-5", max_turns=30, **r))
            s.add(ev.turn_start(turn_number=1, model="claude-sonnet-5-5", **r))
            s.add(
                ev.tool_call(
                    tool_id="toolu_fx_ob1",
                    tool_name="Glob",
                    summary="Glob package.json",
                    tool_input={"pattern": "**/package.json"},
                    **r,
                )
            )
            s.add(ev.tool_result(tool_id="toolu_fx_ob1", content="apps/web/package.json", is_error=False, **r))
            s.add(
                ev.cost_update(
                    cost_usd=0.009,
                    duration_ms=12000,
                    duration_api_ms=11000,
                    num_turns=1,
                    tokens_in=3000,
                    tokens_out=200,
                    cache_read_tokens=0,
                    cache_creation_tokens=0,
                    **r,
                )
            )
            s.add(
                ev.agent_completed(
                    is_error=False,
                    error_code=None,
                    error_message="",
                    output="one app: web",
                    cost_usd=0.009,
                    duration_ms=12000,
                    tools_called={"Glob": 1},
                    policy_denials={},
                    input_tokens=3000,
                    output_tokens=200,
                    cache_read_tokens=0,
                    cache_creation_tokens=0,
                    **r,
                )
            )
        if node_id == "smoke":
            s.add(ev.progress(message="Booting web", node=node_id, app="web"))
        s.add(ev.node_completed(node=node_id, attempt=1, agent=agent_type, ok=True))
    s.add(ev.job_finished(status="completed", error_code=None, error_message=None, pr_url=None, total_cost_usd=0.009))
    return s.events


def qa() -> list[dict[str, Any]]:
    """A completed standalone QA job: the orchestrator's and the executor's calls (#102).

    The orchestrator brackets its three nodes with agentless node_started /
    node_completed (embry0/workflows/qa/orchestrator.py, orchestrator_report.py);
    the qa_* rows keep the builders' default node "qa" (orchestrator_helpers.py,
    subtask_nodes.py); the sub-task's own progress rows name its nodes
    acquire_sandbox and boot_app (subtask_nodes.py); and the QA agent run nested
    inside orchestrate_qa is stamped node "qa", agent "qa" and its run_id by the
    executor's _ids (nodes.py runs it with node="qa"). None of those names is a
    graph node: the frontend has to attribute such rows to the node running at
    the time. One thing the wire carries is left out: the sub-task writer's
    `app` tag on every nested agent row, which the contract does not admit (#107).
    """
    s = Stream("job-0001")
    init_id, orchestrate_id, report_id = (n[0] for n in QA_NODE_IDS)
    s.add(ev.node_started(node=init_id))
    s.add(ev.pipeline_graph(graph=qa_pipeline_graph(s.job_id), node=init_id))
    s.add(ev.node_completed(node=init_id))
    s.add(ev.node_started(node=orchestrate_id))
    s.add(ev.progress(message="QA fan-out: 1 app(s): web", node=orchestrate_id))
    s.add(ev.qa_apps_resolved(apps=["web"]))
    s.add(ev.qa_subtask_started(app="web", target="managed"))
    s.add(ev.qa_subtask_status(app="web", status="running"))
    s.add(ev.progress(message="[web] acquiring sandbox", node="acquire_sandbox", app="web"))
    s.add(ev.progress(message="[web] booting app (target=managed)", node="boot_app", app="web"))
    r: dict[str, Any] = dict(run_id="run-0001", node="qa", agent="qa")
    s.add(ev.agent_started(model="claude-sonnet-5-5", max_turns=100, **r))
    s.add(ev.turn_start(turn_number=1, model="claude-sonnet-5-5", **r))
    s.add(
        ev.tool_call(
            tool_id="toolu_fx_qa1",
            tool_name="mcp__playwright__browser_navigate",
            summary="Navigate to http://web:3000/",
            tool_input={"url": "http://web:3000/"},
            **r,
        )
    )
    s.add(ev.tool_result(tool_id="toolu_fx_qa1", content="Page loaded: Home", is_error=False, **r))
    s.add(ev.turn_start(turn_number=2, model="claude-sonnet-5-5", **r))
    s.add(ev.text(text="Every acceptance criterion passed.", **r))
    s.add(
        ev.turn_usage(
            input_tokens=1800,
            output_tokens=600,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd=0.42,
            model="claude-sonnet-5-5",
            **r,
        )
    )
    s.add(
        ev.cost_update(
            cost_usd=0.42,
            duration_ms=160000,
            duration_api_ms=150000,
            num_turns=2,
            tokens_in=1800,
            tokens_out=600,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            **r,
        )
    )
    s.add(
        ev.agent_completed(
            is_error=False,
            error_code=None,
            error_message="",
            output="overall: passed",
            cost_usd=0.42,
            duration_ms=160000,
            tools_called={"mcp__playwright__browser_navigate": 1},
            policy_denials={},
            input_tokens=1800,
            output_tokens=600,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            **r,
        )
    )
    s.add(ev.qa_subtask_status(app="web", status="passed", duration_ms=178000))
    s.add(ev.qa_subtask_finished(app="web", status="passed", duration_ms=178000))
    s.add(ev.progress(message="QA fan-out complete: passed", node=orchestrate_id))
    s.add(ev.node_completed(node=orchestrate_id, action="passed"))
    s.add(ev.node_started(node=report_id))
    s.add(ev.node_completed(node=report_id))
    s.add(ev.job_finished(status="completed", error_code=None, error_message=None, pr_url=None, total_cost_usd=0.42))
    return s.events


def legacy_rows() -> list[dict[str, Any]]:
    """Pre-contract rows as GET /jobs/{id}/logs/events returns them (spec section 6); not contract-true by design."""
    t = EPOCH.isoformat().replace("+00:00", "Z")
    return [
        {"type": "node_started", "node": "developer", "timestamp": t, "event_seq": 10},
        {"type": "agent_started", "agent": "developer", "timestamp": t, "event_seq": 13},
        {
            "type": "tool_call",
            "tool_id": "legacy-1",
            "tool_name": "Bash",
            "input": "ls -la",
            "tool_input": {"command": "ls -la"},
            "node": "developer",
            "timestamp": t,
            "event_seq": 16,
        },
        {
            "type": "tool_result",
            "tool_use_id": "legacy-1",
            "content": "total 0",
            "is_error": False,
            "node": "developer",
            "timestamp": t,
            "event_seq": 19,
        },
        {"type": "error", "error": "legacy boom", "timestamp": t, "event_seq": 22},
        {"type": "raw", "message": "this line was never JSON", "event_seq": 25},
        {
            "type": "pipeline_stage_completed",
            "pr_url": "https://forge.example.test/org/repo/pull/9",
            "timestamp": t,
            "event_seq": 28,
        },
        {
            "type": "cost_update",
            "cost_usd": 0.01,
            "tokens_in": 10,
            "tokens_out": 5,
            "num_turns": 1,
            "timestamp": t,
            "event_seq": 31,
        },
        # The old executor stamped node = agent type on what a run emitted, so the
        # reviewer's rows say "reviewer" while its node_started says "review".
        {"type": "node_started", "node": "review", "timestamp": t, "event_seq": 34},
        {"type": "agent_started", "agent": "reviewer", "timestamp": t, "event_seq": 37},
        {
            "type": "tool_call",
            "tool_id": "legacy-2",
            "tool_name": "Read",
            "input": "cli.py",
            "tool_input": {"file_path": "cli.py"},
            "node": "reviewer",
            "timestamp": t,
            "event_seq": 40,
        },
        {
            "type": "tool_result",
            "tool_use_id": "legacy-2",
            "content": "import argparse",
            "is_error": False,
            "node": "reviewer",
            "timestamp": t,
            "event_seq": 43,
        },
        {
            "type": "cost_update",
            "cost_usd": 0.02,
            "tokens_in": 20,
            "tokens_out": 8,
            "num_turns": 1,
            "node": "reviewer",
            "timestamp": t,
            "event_seq": 46,
        },
    ]


def generate_all() -> dict[str, list[dict[str, Any]]]:
    return {"issue-to-pr": issue_to_pr(), "onboarding": onboarding(), "qa": qa(), "legacy-rows": legacy_rows()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the files on disk differ from the generator")
    args = parser.parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stale = []
    for name, events in generate_all().items():
        path = OUT_DIR / f"{name}.json"
        text = json.dumps(events, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(path.name)
        else:
            path.write_text(text)
    if stale:
        print("stale fixtures: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
