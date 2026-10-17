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


def test_fixtures_equal_the_script_output() -> None:
    module = _script()
    generated = module.generate_all()
    for name, events in generated.items():
        on_disk = json.loads((FIXTURES / f"{name}.json").read_text())
        assert on_disk == events, f"{name}.json is stale: run python scripts/job_event_fixtures.py"
    assert set(generated) == set(CONTRACT_FIXTURES) | {"legacy-rows"}
