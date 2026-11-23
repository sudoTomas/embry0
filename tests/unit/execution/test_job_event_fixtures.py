"""The job-event fixtures the frontend render tests read are contract-true and regenerable (#41)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "job-events"
CONTRACT = json.loads((ROOT / "docs" / "contracts" / "events.json").read_text())
CONTRACT_FIXTURES = ("issue-to-pr", "onboarding", "qa")


def _script():
    spec = importlib.util.spec_from_file_location("job_event_fixtures", ROOT / "scripts" / "job_event_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", CONTRACT_FIXTURES)
def test_every_fixture_event_satisfies_the_contract(name: str) -> None:
    events = json.loads((FIXTURES / f"{name}.json").read_text())
    assert events, f"{name}.json is empty"
    for event in events:
        row = CONTRACT[event["type"]]
        keys = set(event)
        required = set(row["required"])
        allowed = required | set(row["optional"])
        missing = required - keys
        extra = keys - allowed
        assert not missing, f"{name}: {event['type']} seq {event.get('event_seq')} lacks {sorted(missing)}"
        assert not extra, f"{name}: {event['type']} seq {event.get('event_seq')} carries {sorted(extra)}"


@pytest.mark.parametrize("name", CONTRACT_FIXTURES)
def test_fixture_seqs_are_increasing_and_chained(name: str) -> None:
    events = json.loads((FIXTURES / f"{name}.json").read_text())
    seqs = [e["event_seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), f"{name}: event_seq not strictly increasing"
    for prev, cur in zip(events, events[1:]):
        assert cur["prev_seq"] == prev["event_seq"], (
            f"{name}: seq {cur['event_seq']} prev_seq {cur['prev_seq']} != {prev['event_seq']}"
        )
    assert "prev_seq" not in events[0], f"{name}: the first event has no predecessor"


@pytest.mark.parametrize("name", CONTRACT_FIXTURES)
def test_every_contract_fixture_covers_its_job_kind(name: str) -> None:
    events = json.loads((FIXTURES / f"{name}.json").read_text())
    types = {e["type"] for e in events}
    assert {"pipeline_graph", "node_started", "node_completed", "job_finished"} <= types, f"{name}: {sorted(types)}"


def test_issue_to_pr_fixture_exercises_the_sandbox_vocabulary() -> None:
    types = {e["type"] for e in json.loads((FIXTURES / "issue-to-pr.json").read_text())}
    expected = {
        "agent_started",
        "turn_start",
        "turn_usage",
        "text",
        "thinking",
        "tool_call",
        "tool_result",
        "subagent_start",
        "subagent_end",
        "context_compacted",
        "cost_update",
        "agent_completed",
        "error",
        "review_decision",
        "pr_created",
        "interrupt",
    }
    assert expected <= types, f"missing {sorted(expected - types)}"


def test_qa_fixture_nests_the_agent_run_in_orchestrate_qa() -> None:
    """#102: the standalone QA stream brackets three agentless nodes, and every row
    the nested QA agent emits carries node "qa", a name the graph does not know;
    the run's own rows also carry its run_id. The emitters: orchestrator.py and
    orchestrator_report.py bracket the nodes, nodes.py runs the agent with
    node="qa" and executor._ids stamps it, the qa_* builders default to "qa"."""
    events = json.loads((FIXTURES / "qa.json").read_text())
    graph = next(e for e in events if e["type"] == "pipeline_graph")["graph"]
    node_ids = [n["node_id"] for n in graph["nodes"]]
    assert node_ids == ["init_orchestrator", "orchestrate_qa", "qa_report"], node_ids
    brackets = [(e["type"], e["node"]) for e in events if e["type"] in ("node_started", "node_completed")]
    assert brackets == [(t, n) for n in node_ids for t in ("node_started", "node_completed")], (
        f"the three graph nodes bracket themselves in order, nothing else does: {brackets}"
    )
    assert "qa" not in node_ids, "the nested agent's node name must not be a graph node"
    qa_rows = [e for e in events if e["type"].startswith("qa_")]
    assert qa_rows, "no qa_* rows"
    for e in qa_rows:
        assert e["node"] == "qa", (
            f"{e['type']} seq {e['event_seq']} carries node {e['node']!r}, the emitter writes 'qa'"
        )
        assert "run_id" not in e, f"{e['type']} seq {e['event_seq']} is the orchestrator's, it has no run_id"
    run_rows = [e for e in events if "run_id" in e]
    lifecycle = [e["type"] for e in run_rows if e["type"] in ("agent_started", "cost_update", "agent_completed")]
    assert lifecycle == ["agent_started", "cost_update", "agent_completed"], (
        f"the QA run opens, reports its cost and closes once: {lifecycle}"
    )
    assert len(run_rows) > len(lifecycle), "the run has turn rows between opening and closing"
    for e in run_rows:
        assert (e["node"], e.get("agent"), e["run_id"]) == ("qa", "qa", "run-0001"), (
            f"{e['type']} seq {e['event_seq']} carries {(e['node'], e.get('agent'), e['run_id'])}, "
            "executor._ids stamps ('qa', 'qa', 'run-0001') on every row of the run"
        )
    started = events.index(next(e for e in events if e["type"] == "node_started" and e["node"] == "orchestrate_qa"))
    completed = events.index(next(e for e in events if e["type"] == "node_completed" and e["node"] == "orchestrate_qa"))
    for e in qa_rows + run_rows:
        assert started < events.index(e) < completed, f"{e['type']} seq {e['event_seq']} is outside orchestrate_qa"
    # Inside the bracket there is nothing but the orchestrator's qa_* rows, its and
    # the sub-task's progress rows, and the one run: no second run, no stray row.
    for e in events[started + 1 : completed]:
        assert e in qa_rows or e in run_rows or e["type"] == "progress", (
            f"{e['type']} seq {e['event_seq']} inside orchestrate_qa belongs to no emitter of the standalone QA job"
        )


def test_qa_fixture_tags_the_nested_agent_rows_with_their_app() -> None:
    """#107: the QA sub-task's writer tags each row of its nested agent with the app; the fixture carries what the
    wire carries."""
    events = json.loads((FIXTURES / "qa.json").read_text())
    nested = [e for e in events if e.get("run_id") == "run-0001"]
    assert nested, "the qa fixture has no nested agent run"
    untagged = sorted({e["type"] for e in nested if e.get("app") != "web"})
    assert untagged == [], f"nested agent rows without the app their sub-task runs for: {untagged}"


@pytest.mark.parametrize("name", CONTRACT_FIXTURES)
def test_every_turn_reports_its_usage_where_the_executor_does(name: str) -> None:
    """#108: usage rows sit where the executor writes them. The generator wrote them after the turn's blocks and not
    for every turn, so a reducer that relied on the order passed against the fixtures and met another on the wire.

    What the executor guarantees (tests/unit/agents/test_sdk_executor.py pins each): one turn_usage per assistant
    message that reports usage. For a top-level message it comes right after that message's turn_start and before
    its blocks. A subagent's message has no turn_start: its turn_usage, scoped with parent_tool_use_id, comes after
    the subagent_start and before the subagent's blocks. And one cost_update per run, taken from the SDK's result
    message.

    What it does not guarantee: that a run's turn_usage rows add up to its cost_update. The two come from different
    places (each message's own usage, and the result's totals), and whether those totals count a subagent's
    messages is the SDK's business. The frontend counts a run's usage rows only until its cost_update arrives. The
    fixtures keep one convention so its tests see a consistent run: a run's top-level usage rows add up to its
    cost_update, and a subagent's scoped rows are left out of that sum.
    """
    events = json.loads((FIXTURES / f"{name}.json").read_text())
    for i, event in enumerate(events):
        if event["type"] != "turn_start":
            continue
        after = next((e for e in events[i + 1 :] if e.get("run_id") == event.get("run_id")), None)
        assert after is not None and after["type"] == "turn_usage" and "parent_tool_use_id" not in after, (
            f"{name}: turn {event['turn_number']} of {event.get('run_id')} is followed by "
            f"{after and after['type']}, not its turn_usage"
        )
    for i, event in enumerate(events):
        if event["type"] != "subagent_start":
            continue
        scoped = [e for e in events[i + 1 :] if e.get("parent_tool_use_id") == event["tool_id"]]
        assert scoped and scoped[0]["type"] == "turn_usage", (
            f"{name}: the first row of subagent {event['tool_id']} is {scoped and scoped[0]['type']}, not the "
            "turn_usage its message reports"
        )
    for run in {e["run_id"] for e in events if e["type"] == "cost_update"}:
        usage = [
            e for e in events if e["type"] == "turn_usage" and e.get("run_id") == run and "parent_tool_use_id" not in e
        ]
        total = next(e for e in events if e["type"] == "cost_update" and e.get("run_id") == run)
        assert (sum(u["input_tokens"] for u in usage), sum(u["output_tokens"] for u in usage)) == (
            total["tokens_in"],
            total["tokens_out"],
        ), f"{name}: the top-level turn_usage rows of {run} do not add up to its cost_update (a fixture convention)"


def test_issue_to_pr_fixture_reports_a_subagent_message_s_usage() -> None:
    """#632 review: the executor writes a subagent message's usage too, scoped, with no turn_start before it."""
    events = json.loads((FIXTURES / "issue-to-pr.json").read_text())
    order = [
        (e["type"], e.get("parent_tool_use_id"))
        for e in events
        if e.get("run_id") == "run-fixture-2"
        and e["type"] in ("turn_start", "turn_usage", "subagent_start", "tool_call")
    ]
    assert order[:5] == [
        ("turn_start", None),
        ("turn_usage", None),
        ("tool_call", None),
        ("subagent_start", None),
        ("turn_usage", "toolu_fx_task"),
    ], f"the developer run's subagent reports no scoped usage where the executor writes it: {order}"


def test_fixtures_equal_the_script_output() -> None:
    module = _script()
    generated = module.generate_all()
    for name, events in generated.items():
        on_disk = json.loads((FIXTURES / f"{name}.json").read_text())
        assert on_disk == events, f"{name}.json is stale: run python scripts/job_event_fixtures.py"
    assert set(generated) == set(CONTRACT_FIXTURES) | {"legacy-rows"}
