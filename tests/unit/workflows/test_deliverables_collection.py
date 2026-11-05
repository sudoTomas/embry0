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
    def __init__(self, listing: str | bytes, files: dict[str, bytes]):
        self._listing = listing.encode() if isinstance(listing, str) else listing
        self._files = files
        self.copied: list[str] = []
        self.listing_max_bytes: int | None = None

    def build_exec_cmd(self, container, command, **kwargs):
        return ["docker", "exec", container, *command]

    async def run_cmd(self, cmd, timeout=60):
        raise AssertionError("the collector reads its listing as bytes (run_cmd strips), never through run_cmd")

    async def run_cmd_bytes(self, cmd, timeout=60, max_bytes=None):
        if "find" in cmd[-1]:  # the NUL-delimited listing, read as raw, capped bytes
            self.listing_max_bytes = max_bytes
            return self._listing
        # read_regular_file builds [..., "python3", "-I", "-c", <script>, <path>, <limit>]
        from embry0.execution.sandbox_files import SandboxReadRefused

        src_path, limit = cmd[-2], int(cmd[-1])
        data = self._files[src_path]
        if len(data) > limit:  # the in-container reader refuses a file past the limit
            raise SandboxReadRefused("too_large", f"{len(data)} > {limit}")
        self.copied.append(src_path)
        return data


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
        "/workspace/deliverables/out.csv\0/workspace/deliverables/sub/report.pdf\0",
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
async def test_a_listed_path_outside_the_deliverables_dir_is_refused():
    # A listing entry that does not stay under the deliverables directory
    # (a traversal, or a path a planted link resolved to) is dropped, never read.
    docker = _FakeDocker(
        "/workspace/deliverables/ok.txt\0/workspace/deliverables/../../root/canary\0/etc/passwd\0",
        {
            "/workspace/deliverables/ok.txt": b"ok",
            "/root/canary": b"SECRET",
            "/etc/passwd": b"root:x:0:0",
        },
    )
    minio = _FakeMinio()
    out = await finalize_output_node(_artifact_state(), {"configurable": {"docker": docker, "qa_minio": minio}})
    artifacts = [d for d in out["deliverables"] if d["type"] == "artifact"]
    assert [a["title"] for a in artifacts] == ["ok.txt"]
    assert docker.copied == ["/workspace/deliverables/ok.txt"], "only the in-dir file was read"


@pytest.mark.asyncio
async def test_total_bytes_capped_across_artifacts(monkeypatch):
    from embry0.workflows.issue_to_pr import plan_route

    monkeypatch.setattr(plan_route, "MAX_ARTIFACT_BYTES", 100)
    monkeypatch.setattr(plan_route, "MAX_ARTIFACT_TOTAL_BYTES", 150)
    listing = "".join(f"/workspace/deliverables/f{i}.bin\0" for i in range(3))
    files = {f"/workspace/deliverables/f{i}.bin": b"x" * 80 for i in range(3)}  # each fits, two exceed the total
    docker = _FakeDocker(listing, files)
    out = await finalize_output_node(_artifact_state(), {"configurable": {"docker": docker, "qa_minio": _FakeMinio()}})
    artifacts = [d for d in out["deliverables"] if d["type"] == "artifact"]
    assert len(artifacts) == 1, "the total byte cap stops after the first file"


@pytest.mark.asyncio
async def test_a_name_with_a_control_character_is_refused():
    # A newline or tab in a name would land in a storage key and a title: skipped and logged.
    docker = _FakeDocker(
        "/workspace/deliverables/ok.txt\0/workspace/deliverables/a\nb.txt\0/workspace/deliverables/t\tab.txt\0",
        {
            "/workspace/deliverables/ok.txt": b"ok",
            "/workspace/deliverables/a\nb.txt": b"x",
            "/workspace/deliverables/t\tab.txt": b"y",
        },
    )
    minio = _FakeMinio()
    out = await finalize_output_node(_artifact_state(), {"configurable": {"docker": docker, "qa_minio": minio}})
    artifacts = [d for d in out["deliverables"] if d["type"] == "artifact"]
    assert [a["title"] for a in artifacts] == ["ok.txt"]
    assert docker.copied == ["/workspace/deliverables/ok.txt"], "a refused name is never read"


@pytest.mark.asyncio
async def test_a_name_ending_in_a_space_is_kept_whole():
    # The listing is raw bytes, never stripped: a last entry without its NUL keeps its trailing space.
    docker = _FakeDocker(
        "/workspace/deliverables/notes ",
        {"/workspace/deliverables/notes ": b"n"},
    )
    out = await finalize_output_node(_artifact_state(), {"configurable": {"docker": docker, "qa_minio": _FakeMinio()}})
    assert [d["title"] for d in out["deliverables"] if d["type"] == "artifact"] == ["notes "]


@pytest.mark.asyncio
async def test_the_listing_is_read_with_a_byte_cap():
    from embry0.workflows.issue_to_pr import plan_route

    MAX_LISTING_BYTES = getattr(plan_route, "MAX_LISTING_BYTES", None)
    assert MAX_LISTING_BYTES is not None, "the deliverables listing needs a byte cap (plan_route.MAX_LISTING_BYTES)"
    docker = _FakeDocker("/workspace/deliverables/ok.txt\0", {"/workspace/deliverables/ok.txt": b"ok"})
    await finalize_output_node(_artifact_state(), {"configurable": {"docker": docker, "qa_minio": _FakeMinio()}})
    assert docker.listing_max_bytes == MAX_LISTING_BYTES, "the listing pipe is capped, not buffered whole"


@pytest.mark.asyncio
async def test_a_listing_past_its_cap_yields_no_artifacts_and_does_not_fail():
    from embry0.execution import docker_client

    OutputTooLargeError = getattr(docker_client, "OutputTooLargeError", None)
    assert OutputTooLargeError is not None, "a listing past its cap must be refused (docker_client.OutputTooLargeError)"

    class _Flooded(_FakeDocker):
        async def run_cmd_bytes(self, cmd, timeout=60, max_bytes=None):
            raise OutputTooLargeError("command output passed its cap")

    out = await finalize_output_node(
        _artifact_state(), {"configurable": {"docker": _Flooded("", {}), "qa_minio": _FakeMinio()}}
    )
    assert [d["type"] for d in out["deliverables"]] == ["report"]


@pytest.mark.asyncio
async def test_oversized_artifact_skipped():
    docker = _FakeDocker(
        "/workspace/deliverables/huge.bin\0/workspace/deliverables/ok.txt\0",
        {
            "/workspace/deliverables/huge.bin": b"x" * (MAX_ARTIFACT_BYTES + 1),
            "/workspace/deliverables/ok.txt": b"ok!",
        },
    )
    minio = _FakeMinio()
    out = await finalize_output_node(_artifact_state(), {"configurable": {"docker": docker, "qa_minio": minio}})
    artifacts = [d for d in out["deliverables"] if d["type"] == "artifact"]
    assert [a["title"] for a in artifacts] == ["ok.txt"]
    assert docker.copied == ["/workspace/deliverables/ok.txt"]


@pytest.mark.asyncio
async def test_artifact_count_capped():
    listing = "".join(f"/workspace/deliverables/f{i:03}.txt\0" for i in range(MAX_ARTIFACT_COUNT + 5))
    files = {f"/workspace/deliverables/f{i:03}.txt": b"x" for i in range(MAX_ARTIFACT_COUNT + 5)}
    docker = _FakeDocker(listing, files)
    minio = _FakeMinio()
    out = await finalize_output_node(_artifact_state(), {"configurable": {"docker": docker, "qa_minio": minio}})
    artifacts = [d for d in out["deliverables"] if d["type"] == "artifact"]
    assert len(artifacts) == MAX_ARTIFACT_COUNT


@pytest.mark.asyncio
async def test_artifact_failures_never_fail_the_node():
    class _BrokenDocker(_FakeDocker):
        async def run_cmd_bytes(self, cmd, timeout=60, max_bytes=None):
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
