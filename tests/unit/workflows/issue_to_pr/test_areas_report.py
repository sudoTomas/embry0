"""Map areas (spec 2026-11-10): the document the area mapper reports waits for a person; nothing is pushed."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from embry0.services.area_map import map_task
from embry0.workflows.issue_to_pr.areas_report import TEMPLATE, map_outcome, report_file, report_notes

FILE = """areas:
  - name: Billing
    paths: ["services/billing/**"]
    logs: ["billing-api"]
  - name: Search
    paths: ["services/search/**"]
"""

REPORT = f"""Areas: 2

## Areas

| Area | Paths | Logs | Owners | Why |
|---|---|---|---|---|
| Billing | services/billing/** | billing-api | | its own compose service |
| Search | services/search/** | | | its own package |

## Areas file

```yaml
{FILE}```

## Not covered

- docs/: shared.
"""


def test_the_report_s_block_under_its_heading_is_the_document() -> None:
    assert report_file(REPORT) == FILE
    assert report_file(REPORT.replace("## Areas file", "## Areas")) is None, "the block must sit under its heading"
    assert report_file("Areas: 0\n\n## Areas file\n\nNo file.\n") is None
    assert report_file(None) is None
    assert report_file("## Areas file\n\n````yaml\n\n````\n") is None, "an empty block is no document"


def test_the_notes_are_the_report_without_its_document() -> None:
    notes = report_notes(REPORT)
    assert "| Billing |" in notes and "## Not covered" in notes
    assert "name: Billing" not in notes and "## Areas file" not in notes
    assert map_outcome(REPORT) == {"areas_document": FILE, "areas_notes": notes}


def test_the_task_starts_from_the_stored_document() -> None:
    task = map_task("acme/web", existing=FILE, logql='{container=~"web-.*"}')
    assert (
        task.splitlines()[0] == "Map the areas of acme/web: the parts its work is about, for embry0's areas document."
    )
    assert "embry0 keeps this areas document for the repository. Start from it:" in task and "name: Billing" in task
    assert '{container=~"web-.*"}' in task
    bare = map_task("acme/web", existing="", logql=None)
    assert "keeps no areas document for this repository yet" in bare
    assert ".embry0/" not in task + bare, "the task never names a file of the repository"


@pytest.mark.parametrize("template_key", ["pipeline_template", "pipeline_template_request"])
async def test_the_output_step_reads_a_map_s_document_and_pushes_nothing(template_key: str) -> None:
    from embry0.workflows.issue_to_pr.plan_route import finalize_output_node

    patch_delivery = AsyncMock(return_value={"branch_name": "embry0/x"})
    state = {
        "job_id": "job-1",
        "areas_proposal_id": "areas-000000000001",
        "agent_outputs": [{"agent_type": "area-mapper", "is_error": False, "output": REPORT}],
        template_key: "map-areas",
    }
    with (
        patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None),
        patch("embry0.workflows.issue_to_pr.plan_route._collect_artifacts", new=AsyncMock(return_value=[])),
        patch("embry0.workflows.issue_to_pr.audit_delivery.deliver_audit_patch", new=patch_delivery),
    ):
        out = await finalize_output_node(state, {"configurable": {}})
    patch_delivery.assert_not_awaited()
    assert out["areas_document"] == FILE and "| Search |" in out["areas_notes"]
    assert "branch_name" not in out and "pr_title" not in out, "a map opens no pull request"


async def test_a_context_audit_still_pushes_its_patch() -> None:
    from embry0.workflows.issue_to_pr.plan_route import finalize_output_node

    patch_delivery = AsyncMock(return_value={"branch_name": "embry0/audit"})
    state = {
        "job_id": "job-2",
        "open_pr": True,
        "agent_outputs": [{"agent_type": "context-auditor", "is_error": False, "output": "a report"}],
        "pipeline_template": "context-audit",
    }
    with (
        patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None),
        patch("embry0.workflows.issue_to_pr.plan_route._collect_artifacts", new=AsyncMock(return_value=[])),
        patch("embry0.workflows.issue_to_pr.audit_delivery.deliver_audit_patch", new=patch_delivery),
    ):
        out = await finalize_output_node(state, {"configurable": {}})
    patch_delivery.assert_awaited_once()
    assert out["branch_name"] == "embry0/audit" and "areas_document" not in out


async def test_a_map_s_job_is_named_on_its_proposal_before_it_is_submitted() -> None:
    """A map is finished only by its own job, so the job must be on the proposal before it can run and end."""
    from embry0.services.area_map import start_map_areas_job

    order: list[str] = []

    class _Jobs:
        async def create(self, **_kwargs: Any) -> str:
            order.append("created")
            return "job-map-1"

    class _Templates:
        async def list_all(self) -> list[dict[str, Any]]:
            return [{"name": TEMPLATE}]

    class _Executor:
        def submit_job(self, job_id: str, **_kwargs: Any) -> None:
            order.append(f"submitted {job_id}")

    async def attach(job_id: str) -> None:
        order.append(f"attached {job_id}")

    job_id = await start_map_areas_job(
        "acme/web",
        jobs=_Jobs(),
        executor=_Executor(),
        templates=_Templates(),
        task="Map the areas of acme/web",
        budget_usd=2.0,
        dispatched_by="ada",
        proposal_id="areas-1",
        attach=attach,
    )
    assert job_id == "job-map-1"
    assert order == ["created", "attached job-map-1", "submitted job-map-1"], order
