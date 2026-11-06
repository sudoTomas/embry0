"""The watcher reads its stack's ``slop_policy`` (#169): what its analysis is told, its kinds, its ticket heading.

Each analysis is told the stack's repository's ``classifier_guide`` and its
verdict keeps the kinds that policy lists; a slop finding's draft ticket
opens with the policy's ``ticket_heading``. The production watcher is built
with a reader of its own stack's policy, read on every analysis, so a change
needs no restart.
"""

from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace
from typing import Any

import pytest

import embry0.api.app as app_module
from embry0.settings.sections import CATALOG
from embry0.settings.store import build_snapshot
from tests.unit.services.test_watcher import _service

LINES = ["[c] e1", "[c] e2", "[c] e3"]
SLOP_PROPOSAL = (
    "TITLE: Clean up the outbox retry wrappers\nSEVERITY: medium\nNATURE: slop\nSLOP_SCORE: 72\n"
    "SLOP_KINDS: naming, symptom_patch\nSLOP_EVIDENCE: svc/outbox.py | three retry wrappers\n"
    "BODY:\n## Evidence\nboom"
)


def _policy(**values: Any) -> Any:
    section = CATALOG.get("slop_policy")
    if section is None:
        pytest.fail("the settings document has no slop_policy section: the watcher's words about slop are code")
    model = section.validate(values)

    async def read() -> Any:
        return model

    return read


@pytest.mark.asyncio
async def test_an_analysis_is_told_the_stack_s_guide_and_its_verdict_keeps_the_stack_s_kinds() -> None:
    job = {"job_id": "job-w1", "status": "completed", "result_summary": SLOP_PROPOSAL}
    issue = {"identifier": "ENG-1", "url": "https://tracker.example/ENG-1"}
    svc = _service(lines=LINES, job_result=job, linear_issue=issue)
    svc._slop_policy = _policy(
        classifier_guide="Acme's slop: names that lie.\nKinds: naming.",
        kinds=["naming"],
        ticket_heading="Shape, not behaviour",
    )
    out = await svc.run_once()
    assert out["action"] == "proposed", out
    task = svc._jobs.create.await_args.kwargs["task"]
    assert "Acme's slop: names that lie.\nKinds: naming." in task, "the analysis is told the stack's guide"
    assert "Tell slop apart from bugs" not in task
    description = svc._filer.file_draft.await_args.kwargs["description"]
    assert description.startswith("## Shape, not behaviour\n"), "the ticket opens with the stack's heading"
    assert "slop score 72/100: naming)" in description, "the verdict keeps the stack's kinds, and only those"


@pytest.mark.asyncio
async def test_a_watcher_without_a_reader_reads_as_before() -> None:
    job = {"job_id": "job-w1", "status": "completed", "result_summary": SLOP_PROPOSAL}
    issue = {"identifier": "ENG-1", "url": "https://tracker.example/ENG-1"}
    svc = _service(lines=LINES, job_result=job, linear_issue=issue)
    out = await svc.run_once()
    assert out["action"] == "proposed", out
    assert "Tell slop apart from bugs" in svc._jobs.create.await_args.kwargs["task"]
    description = svc._filer.file_draft.await_args.kwargs["description"]
    assert description.startswith("## Slop, not a bug\n")
    assert "slop score 72/100: symptom patch)" in description, "naming is no default kind"


@pytest.mark.asyncio
async def test_a_reader_that_fails_reads_the_defaults() -> None:
    job = {"job_id": "job-w1", "status": "completed", "result_summary": SLOP_PROPOSAL}
    svc = _service(lines=LINES, job_result=job, linear_issue={"identifier": "ENG-1"})

    async def broken() -> Any:
        raise RuntimeError("database is down")

    svc._slop_policy = broken
    assert (await svc.run_once())["action"] == "proposed", "a policy that cannot be read never stops a tick"


def _watcher_service_call() -> ast.Call:
    source = pathlib.Path(app_module.__file__).read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "WatcherService":
            return node
    pytest.fail("no WatcherService(...) construction found in embry0/api/app.py")


def test_each_watcher_reads_its_own_stack_s_policy() -> None:
    """The reader is optional in the signature: a watcher built without it reads the defaults for good."""
    passed = {kw.arg: ast.unparse(kw.value) for kw in _watcher_service_call().keywords if kw.arg}
    assert passed.get("slop_policy") == "_watcher_slop_policy(app, stack.repo)", (
        f"each watcher must read its own stack's slop_policy, not {passed.get('slop_policy')!r}"
    )


@pytest.mark.asyncio
async def test_the_reader_reads_the_stack_s_repository_s_layers_now() -> None:
    reader_for = getattr(app_module, "_watcher_slop_policy", None)
    assert reader_for is not None, "app.py has no reader for a stack's slop_policy"
    rows = [
        {"scope": "deployment", "data": {}, "generation": 1, "updated_at": None},
        {"scope": "organization:acme", "data": {"slop_policy": {"ticket_heading": "Acme"}}, "generation": 1},
    ]

    class Store:
        async def snapshot(self) -> Any:
            return build_snapshot(CATALOG, rows, token=(), log=False)

    app = SimpleNamespace(state=SimpleNamespace(settings=Store()))
    assert (await reader_for(app, "acme/widgets")()).ticket_heading == "Acme"
    assert (await reader_for(app, "globex/api")()).ticket_heading == "Slop, not a bug", "another owner's layer"
    rows[1] = {"scope": "organization:acme", "data": {"slop_policy": {"ticket_heading": "Later"}}, "generation": 2}
    assert (await reader_for(app, "acme/widgets")()).ticket_heading == "Later", "read on every call"
