"""Map areas (spec 2026-10-25): the file the area mapper reports becomes a pull request, or nothing."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.orchestration.branch_names import job_push_branch, shared_branch_name
from embry0.services.area_map import map_task
from embry0.workflows.issue_to_pr.areas_delivery import (
    UNCHANGED,
    deliver_areas_file,
    report_file,
)
from tests.fixtures.sandbox_git_local import readable

#: What the staging fake returns as the staged (drop) path.
DROP_PATH = "/run/embry0/drop/deadbeef/areas.yml"

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


class FakeDocker:
    def __init__(self, out: str = "", fail: str | None = None) -> None:
        self.staged: list[tuple[bytes, bool]] = []  # (bytes, as_root)
        self.commands: list[list[str]] = []
        self.out, self.fail = out, fail

    def build_exec_cmd(self, container: str, command: list[str], **kw: Any) -> list[str]:
        user = ["-u", kw["user"]] if kw.get("user") else []
        return ["docker", "exec", *user, container, *command]

    async def run_cmd(self, cmd: list[str], timeout: int = 0, stdin_data: bytes | None = None) -> str:
        self.commands.append(cmd)
        if "python3" in cmd:  # a sandbox_files staging write
            self.staged.append((stdin_data or b"", cmd[:4] == ["docker", "exec", "-u", "root"]))
            return DROP_PATH
        if self.fail:
            raise RuntimeError(self.fail)
        return self.out


TASK = map_task("acme/web", existing=None, logql=None)


def _state(**extra: Any) -> dict[str, Any]:
    return {
        "job_id": "job-3f2a9c1e5b7d",
        "issue_id": None,
        "task": TASK,
        "context": {"type": "git", "repo": "acme/web"},
        "sandbox_container_id": "C1",
        **extra,
    }


def _config(docker: Any) -> dict[str, Any]:
    return {"configurable": {"docker": docker}}


def test_the_file_is_the_yaml_block_under_its_heading():
    assert report_file(REPORT) == FILE
    assert report_file("Areas: 0\n\n## Areas file\n\nNo file.\n") is None
    assert report_file("```yaml\nareas: []\n```") is None, "a yaml block elsewhere is not the file"
    assert report_file(None) is None


async def test_the_file_is_committed_on_the_job_s_branch_and_the_pr_described():
    docker = FakeDocker()
    state = _state()
    out = await deliver_areas_file(state, _config(docker), REPORT)
    branch = job_push_branch(issue_id=None, task=state["task"], job_id=state["job_id"], context=state["context"])
    assert docker.staged == [(FILE.encode(), True)], "the file is staged as root, not copied to an agent path"
    git_cmd = next(c for c in docker.commands if "bash" in c and "embry0_git_repo" in c[-1])
    script = readable(git_cmd)
    assert "embry0_git_repo /workspace" in git_cmd[-1], "the workspace is checked before embry0 runs git in it"
    assert f"git checkout -q -B {branch}" in script and f"cp {DROP_PATH} .embry0/areas.yml" in script
    assert "git diff --no-ext-diff --no-textconv --cached --quiet" in script and f"HEAD:refs/heads/{branch}" in script
    assert out["branch_name"] == branch
    assert out["pr_title"] == "chore(embry0): map this repository's areas"
    assert out["developer_summary"].startswith("Names 2 areas in `.embry0/areas.yml`: Billing, Search.")
    assert "| Billing |" in out["developer_summary"] and "```yaml" not in out["developer_summary"]
    assert ".embry0/areas.yml" in out["pr_note"]


@pytest.mark.parametrize(
    ("report", "state", "docker"),
    [
        ("Areas: 0\n\n## Areas file\n\nNo file.\n", {}, FakeDocker()),
        ("## Areas file\n\n```yaml\nareas:\n  - name: Nowhere\n```\n", {}, FakeDocker()),
        (
            REPORT,
            {"open_prs": [{"number": 9, "head_ref": shared_branch_name(issue_id=None, task=TASK) + "-0a1b2c3d"}]},
            FakeDocker(),
        ),
        (REPORT, {"sandbox_container_id": None}, FakeDocker()),
        (REPORT, {}, FakeDocker(out=UNCHANGED)),
        (REPORT, {}, FakeDocker(fail="push refused")),
    ],
    ids=["no file", "no area with a path", "earlier map open", "no sandbox", "unchanged", "push failed"],
)
async def test_nothing_opens_when_there_is_nothing_new_to_review(report, state, docker):
    assert await deliver_areas_file(_state(**state), _config(docker), report) == {}


@pytest.mark.parametrize("template_key", ["pipeline_template", "pipeline_template_request"])
async def test_the_output_step_delivers_a_map_s_file_and_no_patch(template_key):
    from unittest.mock import AsyncMock, patch

    from embry0.workflows.issue_to_pr.plan_route import finalize_output_node

    areas = AsyncMock(return_value={"branch_name": "embry0/m", "pr_title": "t", "pr_note": "n"})
    patch_delivery = AsyncMock(return_value={})
    state = _state(
        open_pr=True,
        agent_outputs=[{"agent_type": "area-mapper", "is_error": False, "output": REPORT}],
        **{template_key: "map-areas"},
    )
    with (
        patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None),
        patch("embry0.workflows.issue_to_pr.areas_delivery.deliver_areas_file", new=areas),
        patch("embry0.workflows.issue_to_pr.audit_delivery.deliver_audit_patch", new=patch_delivery),
    ):
        out = await finalize_output_node(state, {"configurable": {}})
    areas.assert_awaited_once()
    patch_delivery.assert_not_awaited()
    assert out["branch_name"] == "embry0/m"
