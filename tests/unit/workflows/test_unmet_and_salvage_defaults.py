"""With nothing written, done_when.on_unmet and salvage.enabled leave every job as it was (#182).

The two keys are new behaviour behind defaults, ``pause`` and ``true``,
which must reproduce what a coding job did before them byte for byte. The
values this file compares against are not written by hand: they are what
the tree before the keys produced (``origin/main`` at ``b573c569``), stored
in ``tests/fixtures/golden/done_when_and_salvage_defaults.json``. Never
regenerate that file from a later tree: it would then pin whatever that tree
does.

:func:`capture` drives the nodes the keys touch, through their state alone,
so it runs unchanged on both trees: the developer node (its contract, the
done-when check through it, and every salvage path through a fake sandbox
git), the check itself, the pause, and the next run's prompt. Each runs for
a job that read no settings, one that read the defaults when it started,
one that kept the sections as the release before the keys stored them, and
one that set the old keys to other values (retries 0 and 2, a commit
subject of its own). Timestamps are taken out; nothing else is.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import embry0.workflows.issue_to_pr.nodes as nodes_module
from embry0.execution import sandbox_git
from embry0.services.done_when import parse_done_when
from embry0.workflows.issue_to_pr.nodes._developer import _branch_tip_command
from tests.fixtures.sandbox_git_local import readable
from tests.unit.workflows.test_developer_salvage import _BRANCH, _finished, _Sandbox, _stopped

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "golden" / "done_when_and_salvage_defaults.json"

LIST = parse_done_when(
    {
        "items": [
            {"done": "The boot failure's cause is logged in full", "shown_by": "a test with a bad config"},
            {"done": "The cause is written down", "shown_by": "a PR section"},
        ],
        "out_of_scope": ["Deploy tooling"],
    }
)
OLD_SUBJECT = "wip: unfinished — {agent} stopped ({reason})"
UNMET_REPORT = [
    {"id": "d1", "met": True, "evidence": "test_bad_config passes"},
    {"id": "d2", "met": False, "evidence": "not written yet"},
]
MET_REPORT = [
    {"id": "d1", "met": True, "evidence": "test_bad_config passes"},
    {"id": "d2", "met": True, "evidence": "PR 'Cause'"},
]


def _variants() -> dict[str, dict[str, Any]]:
    """What a job keeps on its state, by how it started."""
    from embry0.services.job_settings import read_for_job

    return {
        "read nothing": {},
        "read the defaults": {"job_settings": asyncio.run(read_for_job(None, "acme/widgets"))},
        "kept by the release before": {
            "job_settings": {
                "done_when": {"retries": 1, "ticket_sections": ["Acceptance criteria"]},
                "salvage": {"commit_subject": OLD_SUBJECT},
            }
        },
        "retries 0": {"job_settings": {"done_when": {"retries": 0}}},
        "retries 2, a subject of its own": {
            "job_settings": {
                "done_when": {"retries": 2},
                "salvage": {"commit_subject": "chore(wip): {agent} stopped, {reason}"},
            }
        },
    }


def _clean(value: Any) -> Any:
    """``value`` as JSON, without the timestamps every run writes anew."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k not in {"timestamp", "checked_at"}}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    return value


def _traced(argv: list[str]) -> str:
    """``argv`` as a developer run's ``git`` list records it."""
    return readable(["docker", "exec", "C", *argv])


#: #435 changed the developer's push check on purpose: it reads the sandbox's own tip of the job's branch, and reads
#: the forge at the URL embry0 cloned the repository from instead of through ``origin``. Only those two commands, to
#: the letter, are put back as the tree before #182 ran them; the route, the updates, the events, the commits and
#: every other command are compared as they are.
_BRANCH_TIP_READ = _traced(_branch_tip_command(_BRANCH))
_FORGE_READ = _traced(sandbox_git.command("ls-remote", "--heads", "https://github.com/o/r.git", _BRANCH))
_ORIGIN_READ = _traced(sandbox_git.command("ls-remote", "--heads", "origin", _BRANCH))


def _push_check_as_before_435(git: list[str]) -> list[str]:
    return [_ORIGIN_READ if c == _FORGE_READ else c for c in git if c != _BRANCH_TIP_READ]


def _output(report: Any, *, is_error: bool = False) -> dict[str, Any]:
    final: dict[str, Any] = {"pr_url": None, "branch": _BRANCH, "summary": "s"}
    if report is not None:
        final["done_when"] = report
    out = {"agent_type": "developer", "is_error": is_error, "output": "done\n" + json.dumps(final), "messages": []}
    return {"agent_outputs": [out], "events": [], "total_cost_usd": 0.05}


def _state(**over: Any) -> dict[str, Any]:
    return {
        "job_id": "J",
        "issue_id": "iss-abc12345",
        "repo": "o/r",
        "task": "test task",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "pipeline_config": {"agent_skills": {"developer": []}, "budget_usd": 50.0},
        "branch_name": _BRANCH,
        **over,
    }


_KEPT_UPDATES = (
    "current_stage",
    "error_code",
    "errors",
    "unfinished_work",
    "done_when_retries",
    "done_when_followup",
    "done_when",
    "route_cursor",
    "branch_name",
)


async def _developer(result: dict[str, Any], sandbox: Any, state: dict[str, Any]) -> dict[str, Any]:
    """One developer run: its prompt's finish line, where it goes, what it writes, and the sandbox git it ran."""
    from embry0.workflows.issue_to_pr.nodes import developer_node

    run = AsyncMock(return_value=result)
    seen: list[dict[str, Any]] = []
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=run),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=seen.append),
    ):
        cmd = await developer_node(
            state, {"configurable": {"agent_runner": object(), "credentials": {}, "docker": sandbox}}
        )
    prompt = str(run.await_args.kwargs["prompt"])
    at = prompt.find("## Done when (your finish line)")
    return _clean(
        {
            "goto": cmd.goto,
            "update": {key: cmd.update[key] for key in _KEPT_UPDATES if key in cmd.update},
            "contract": prompt[at:] if at >= 0 else None,
            "events": [e for e in seen if e.get("type") == "progress"],
            "git": _push_check_as_before_435([readable(c) for c in getattr(sandbox, "calls", [])]),
            "commits": list(getattr(sandbox, "messages", [])),
        }
    )


async def _check(state: dict[str, Any], report: Any) -> dict[str, Any]:
    from embry0.workflows.issue_to_pr.nodes._done_when import check_done_when

    seen: list[dict[str, Any]] = []
    updates, goto = await check_done_when(state, {}, _output(report), LIST, writer=seen.append)
    return _clean({"goto": goto, "updates": updates, "events": seen})


async def _pause(state: dict[str, Any]) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _interrupt(summary: dict[str, Any]) -> str:
        captured.update(summary)
        return "abandon"

    with (
        patch.object(nodes_module, "get_stream_writer", return_value=lambda _: None),
        patch.object(nodes_module, "interrupt", new=_interrupt),
    ):
        after = await nodes_module.max_retries_node(state, config=None)
    return _clean({"summary": captured, "after": after})


_PLANS = {
    "developer, reviewer, qa": [
        {"agent_type": "developer", "node_id": "developer", "config": {}},
        {"agent_type": "reviewer", "node_id": "reviewer", "config": {}},
        {"agent_type": "qa", "node_id": "qa", "config": {}},
    ],
    "developer, qa": [
        {"agent_type": "developer", "node_id": "developer", "config": {}},
        {"agent_type": "qa", "node_id": "qa", "config": {}},
    ],
    "developer": [{"agent_type": "developer", "node_id": "developer", "config": {}}],
}


async def _scenarios(kept: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    allowed = int(((kept.get("job_settings") or {}).get("done_when") or {}).get("retries", 1))
    record = {"branch": _BRANCH, "agent": "developer", "reason": "turn cap", "committed": True}

    # The done-when check: a first unmet report, the report once the retries are spent, all met.
    for label, done, report in (
        ("first unmet report", 0, UNMET_REPORT),
        ("retries spent, unmet", allowed, UNMET_REPORT),
        ("retries spent, unreported", allowed, None),
        ("all met", allowed, MET_REPORT),
    ):
        out[f"check: {label}"] = await _check(_state(done_when_retries=done, **kept), report)

    # Through the developer node, on each route: the contract, the check and where the route goes.
    for plan_name, plan in [("no plan", None), *_PLANS.items()]:
        routed = {"route_plan": plan, "route_cursor": 0, "qa": {"needs_qa": True}} if plan else {}
        for label, done, report in (
            ("first unmet report", 0, UNMET_REPORT),
            ("retries spent, unmet", allowed, UNMET_REPORT),
            ("all met", allowed, MET_REPORT),
        ):
            state = _state(done_when=LIST, done_when_retries=done, **routed, **kept)
            out[f"developer on {plan_name}: {label}"] = await _developer(
                _output(report), _Sandbox(dirty=False, on_origin=True), state
            )

    # The pause the check leads to.
    listed = {**LIST, "report": {"items": [], "unmet": ["d2"], "reported": True}}
    out["pause: done_when_unmet"] = await _pause(
        _state(current_stage="done_when_unmet", done_when=listed, retry_count=0, **kept)
    )

    # Salvage, through the developer node and a fake sandbox git.
    salvage_cases: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]] = [
        (
            "turn cap, dirty, never pushed",
            {},
            _stopped("reached max_turns (120)", error_code="ERR_AGENT_MAX_TURNS"),
            {},
        ),
        (
            "error, dirty, pushed earlier",
            {"on_origin": True},
            _stopped("Agent timed out", error_code="ERR_AGENT_TIMEOUT"),
            {},
        ),
        ("error, clean, pushed", {"dirty": False, "on_origin": True}, _stopped("Agent timed out"), {}),
        ("turn cap, clean, own commits unpushed", {"dirty": False}, _stopped("reached max_turns (120)"), {}),
        ("turn cap, clean, nothing", {"dirty": False}, _stopped("reached max_turns (120)"), {}),
        ("turn cap, dirty, on the base branch", {"head": "main"}, _stopped("reached max_turns (120)"), {}),
        ("turn cap, dirty, commit fails", {"fail_commit": True}, _stopped("reached max_turns (120)"), {}),
        ("budget, dirty", {}, _stopped("reached max_turns (120)", cost=60.0), {}),
        ("questions used up, dirty", {}, _finished(), {"agent_questions_exhausted": True}),
        ("clean finish, dirty, pushed", {"on_origin": True}, _finished(), {}),
        ("clean finish, own commits unpushed", {"dirty": False}, _finished(), {}),
    ]
    for label, sandbox_kwargs, result, over in salvage_cases:
        sandbox = _Sandbox(**sandbox_kwargs)
        if label.endswith("own commits unpushed"):
            sandbox.local_commits = 2
        out[f"salvage: {label}"] = await _developer(result, sandbox, _state(**over, **kept))

    # The pauses salvage leads to, and the next run's prompt.
    for label, over in (
        ("dev_run_unfinished", {"current_stage": "dev_run_unfinished", "unfinished_work": record}),
        ("budget", {"current_stage": "developer_failed", "total_cost_usd": 60.0, "retry_count": 1}),
        ("budget, kept", {"current_stage": "developer_failed", "total_cost_usd": 60.0, "unfinished_work": record}),
        ("questions used up, kept", {"agent_questions_exhausted": True, "unfinished_work": record}),
        ("dev_branch_missing", {"current_stage": "dev_branch_missing"}),
    ):
        out[f"pause: {label}"] = await _pause(_state(pr_url="", **over, **kept))

    from embry0.workflows.issue_to_pr.nodes import _developer_delta_sections

    for label, unfinished in (
        ("salvage commit", record),
        ("pushed, nothing committed", {**record, "committed": False}),
        ("none", None),
    ):
        out[f"next run: {label}"] = _developer_delta_sections(_state(unfinished_work=unfinished, **kept))
    return out


def capture() -> dict[str, Any]:
    """Every scenario for every variant, keyed ``<variant> / <scenario>``."""
    found: dict[str, Any] = {}
    for variant, kept in _variants().items():
        for name, value in asyncio.run(_scenarios(kept)).items():
            found[f"{variant} / {name}"] = value
    return json.loads(json.dumps(found, sort_keys=True, ensure_ascii=False))


@pytest.fixture(scope="module")
def captured() -> dict[str, Any]:
    return capture()


def _golden() -> dict[str, Any]:
    return dict(json.loads(GOLDEN.read_text(encoding="utf-8")))


def _golden_names() -> list[str]:
    """The scenarios pinned; one that fails when the file is missing, so a lost file never passes as no test."""
    return sorted(_golden()) if GOLDEN.is_file() else ["the values captured before #182 are missing"]


def test_the_golden_values_cover_every_scenario(captured: dict[str, Any]) -> None:
    assert sorted(_golden()) == sorted(captured), "the scenarios and the values captured before #182 must match"
    assert len(captured) >= 150, f"too few scenarios to pin the defaults: {len(captured)}"


@pytest.mark.parametrize("name", _golden_names())
def test_with_nothing_new_written_a_job_does_what_it_did_before(name: str, captured: dict[str, Any]) -> None:
    assert GOLDEN.is_file(), f"{GOLDEN} is missing: nothing pins what a job did before #182's two keys"
    assert captured.get(name) == _golden()[name], (
        f"{name!r} changed with done_when.on_unmet and salvage.enabled unset: it must be what the tree before "
        f"#182's two keys produced.\nnow:    {json.dumps(captured.get(name), ensure_ascii=False)[:1500]}\n"
        f"before: {json.dumps(_golden()[name], ensure_ascii=False)[:1500]}"
    )


if __name__ == "__main__":
    import sys

    # Writes the captured values to the file named. Only ever run on the tree BEFORE a change, to pin it
    # (see the module's docstring).
    Path(sys.argv[1]).write_text(json.dumps(capture(), sort_keys=True, ensure_ascii=False, indent=1) + "\n")
