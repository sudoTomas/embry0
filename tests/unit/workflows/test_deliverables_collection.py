"""RAV-603 — deliverable collection (finalize_output) + executor persistence."""

from unittest.mock import AsyncMock, patch

import pytest

from embry0.services.issue_executor import IssueExecutor
from embry0.workflows.issue_to_pr.plan_route import (
    DELIVERABLES_BUCKET,
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_COUNT,
    finalize_output_node,
)


@pytest.fixture(autouse=True)
def _no_stream_writer():
    with patch(
        "embry0.workflows.issue_to_pr.plan_route.get_stream_writer",
        return_value=lambda _e: None,
    ):
        yield


# ---- finalize_output: report deliverable -----------------------------------


@pytest.mark.asyncio
async def test_report_deliverable_from_last_output():
    out = await finalize_output_node(
        {
            "job_id": "job-1",
            "job_kind": "research",
            "agent_outputs": [{"agent_type": "research", "is_error": False, "output": "findings text"}],
        },
        {"configurable": {}},
    )
    assert out["result_summary"] == "findings text"
    assert out["deliverables"] == [{"type": "report", "title": "research report", "content": "findings text"}]


@pytest.mark.asyncio
async def test_no_output_means_no_deliverables():
    out = await finalize_output_node({"job_id": "job-1", "agent_outputs": []}, {"configurable": {}})
    assert out["result_summary"] == ""
    assert out["deliverables"] == []


# ---- finalize_output: artifact collection ----------------------------------


class _FakeDocker:
    def __init__(self, listing: str, files: dict[str, bytes]):
        self._listing = listing
        self._files = files
        self.copied: list[str] = []

    def build_exec_cmd(self, container, command, **kwargs):
        return ["docker", "exec", container, *command]

    async def run_cmd(self, cmd, timeout=60):
        return self._listing

    async def copy_bytes_from(self, container, src_path):
        self.copied.append(src_path)
        return self._files[src_path]


class _FakeMinio:
    def __init__(self):
        self.buckets: list[str] = []
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def ensure_bucket(self, bucket):
        self.buckets.append(bucket)

    async def put_object(self, bucket, key, data, *, content_type="application/octet-stream"):
        self.objects[f"{bucket}/{key}"] = (data, content_type)


def _artifact_state():
    return {
        "job_id": "job-9",
        "job_kind": "ops",
        "sandbox_container_id": "cont-1",
        "agent_outputs": [{"is_error": False, "output": "done"}],
    }


@pytest.mark.asyncio
async def test_artifacts_uploaded_with_relative_keys():
    docker = _FakeDocker(
        "42\t/workspace/deliverables/out.csv\n1024\t/workspace/deliverables/sub/report.pdf\n",
        {
            "/workspace/deliverables/out.csv": b"a,b\n",
            "/workspace/deliverables/sub/report.pdf": b"%PDF",
        },
    )
    minio = _FakeMinio()
    out = await finalize_output_node(_artifact_state(), {"configurable": {"docker": docker, "qa_minio": minio}})

    artifacts = [d for d in out["deliverables"] if d["type"] == "artifact"]
    assert {a["title"] for a in artifacts} == {"out.csv", "sub/report.pdf"}
    assert {a["storage_key"] for a in artifacts} == {"job-9/out.csv", "job-9/sub/report.pdf"}
    assert all(a["storage_bucket"] == DELIVERABLES_BUCKET for a in artifacts)
    assert f"{DELIVERABLES_BUCKET}/job-9/out.csv" in minio.objects
    csv = next(a for a in artifacts if a["title"] == "out.csv")
    assert csv["media_type"] == "text/csv"
    assert csv["size_bytes"] == 4
    # The report deliverable still leads the list.
    assert out["deliverables"][0]["type"] == "report"


@pytest.mark.asyncio
async def test_oversized_artifact_skipped():
    big = MAX_ARTIFACT_BYTES + 1
    docker = _FakeDocker(
        f"{big}\t/workspace/deliverables/huge.bin\n3\t/workspace/deliverables/ok.txt\n",
        {"/workspace/deliverables/ok.txt": b"ok!"},
    )
    minio = _FakeMinio()
    out = await finalize_output_node(_artifact_state(), {"configurable": {"docker": docker, "qa_minio": minio}})
    artifacts = [d for d in out["deliverables"] if d["type"] == "artifact"]
    assert [a["title"] for a in artifacts] == ["ok.txt"]
    assert docker.copied == ["/workspace/deliverables/ok.txt"]


@pytest.mark.asyncio
async def test_artifact_count_capped():
    listing = "".join(f"1\t/workspace/deliverables/f{i:03}.txt\n" for i in range(MAX_ARTIFACT_COUNT + 5))
    files = {f"/workspace/deliverables/f{i:03}.txt": b"x" for i in range(MAX_ARTIFACT_COUNT + 5)}
    docker = _FakeDocker(listing, files)
    minio = _FakeMinio()
    out = await finalize_output_node(_artifact_state(), {"configurable": {"docker": docker, "qa_minio": minio}})
    artifacts = [d for d in out["deliverables"] if d["type"] == "artifact"]
    assert len(artifacts) == MAX_ARTIFACT_COUNT


@pytest.mark.asyncio
async def test_artifact_failures_never_fail_the_node():
    class _BrokenDocker(_FakeDocker):
        async def run_cmd(self, cmd, timeout=60):
            raise RuntimeError("docker down")

    out = await finalize_output_node(
        _artifact_state(),
        {"configurable": {"docker": _BrokenDocker("", {}), "qa_minio": _FakeMinio()}},
    )
    assert out["current_stage"] == "output_finalized"
    assert [d["type"] for d in out["deliverables"]] == ["report"]


# ---- executor persistence ---------------------------------------------------


def _executor():
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._db = object()  # non-None; DeliverablesRepository is patched below
    return ex


@pytest.mark.asyncio
async def test_persist_state_deliverables_plus_pr_synthesis():
    ex = _executor()
    created = []
    with patch(
        "embry0.storage.repositories.deliverables.DeliverablesRepository.create",
        new=AsyncMock(side_effect=lambda **kw: created.append(kw) or "del-x"),
    ):
        await ex._persist_deliverables(
            "job-1",
            {
                "pr_url": "https://github.com/o/r/pull/7",
                "deliverables": [{"type": "report", "title": "t", "content": "c"}],
            },
        )
    assert [c["type"] for c in created] == ["pr", "report"]
    assert created[0]["url"] == "https://github.com/o/r/pull/7"
    assert created[0]["job_id"] == "job-1"


@pytest.mark.asyncio
async def test_persist_synthesizes_report_from_result_summary():
    ex = _executor()
    created = []
    with patch(
        "embry0.storage.repositories.deliverables.DeliverablesRepository.create",
        new=AsyncMock(side_effect=lambda **kw: created.append(kw) or "del-x"),
    ):
        await ex._persist_deliverables("job-2", {"result_summary": "the answer", "job_kind": "research"})
    assert created == [{"job_id": "job-2", "type": "report", "title": "research report", "content": "the answer"}]


@pytest.mark.asyncio
async def test_persist_nothing_when_no_outputs():
    ex = _executor()
    create = AsyncMock()
    with patch("embry0.storage.repositories.deliverables.DeliverablesRepository.create", new=create):
        await ex._persist_deliverables("job-3", {})
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_row_failure_isolated():
    ex = _executor()
    created = []

    async def _create(**kw):
        if kw["type"] == "report":
            raise RuntimeError("db hiccup")
        created.append(kw)
        return "del-x"

    with patch(
        "embry0.storage.repositories.deliverables.DeliverablesRepository.create",
        new=AsyncMock(side_effect=_create),
    ):
        await ex._persist_deliverables(
            "job-4",
            {
                "deliverables": [
                    {"type": "report", "content": "c"},
                    {"type": "artifact", "title": "f.txt", "storage_bucket": "b", "storage_key": "k"},
                ]
            },
        )
    assert [c["type"] for c in created] == ["artifact"]


@pytest.mark.asyncio
async def test_persist_strips_unknown_keys():
    ex = _executor()
    created = []
    with patch(
        "embry0.storage.repositories.deliverables.DeliverablesRepository.create",
        new=AsyncMock(side_effect=lambda **kw: created.append(kw) or "del-x"),
    ):
        await ex._persist_deliverables(
            "job-5",
            {"deliverables": [{"type": "report", "content": "c", "stray_state_key": True}]},
        )
    assert "stray_state_key" not in created[0]
