"""claude-max mode: capture session_id + session_blob; restore them as
--resume <id> + bytes-on-disk for the CLI."""

from pathlib import Path


def test_capture_session_id_and_blob_from_oauth_result(tmp_path):
    from athanor.agents.session import capture_session_after_run

    blob_path = tmp_path / "sessions" / "sess-abc.jsonl"
    blob_path.parent.mkdir(parents=True)
    blob_path.write_bytes(b"line1\nline2\n")

    result = {
        "agent_type": "developer",
        "mode": "claude_max",
        "session_id": "sess-abc",
        "session_blob_path": str(blob_path),
    }
    sess = capture_session_after_run(result, mode="claude_max", job_id="J", agent_type="developer")
    assert sess.mode == "claude_max"
    assert sess.session_id == "sess-abc"
    assert sess.session_blob == b"line1\nline2\n"


def test_restore_writes_blob_and_passes_resume_arg(tmp_path):
    from athanor.agents.session import AgentSession, restore_session_into_options

    sess = AgentSession(
        job_id="J", agent_type="developer", mode="claude_max",
        session_id="sess-abc",
        session_blob=b"line1\n",
    )
    options = {"cli_extra_args": [], "session_dir": str(tmp_path)}
    out = restore_session_into_options(sess, options)
    # Blob written to <session_dir>/<session_id>.jsonl
    written = Path(tmp_path) / "sess-abc.jsonl"
    assert written.read_bytes() == b"line1\n"
    # --resume <id> appended to CLI args
    assert "--resume" in out["cli_extra_args"]
    assert "sess-abc" in out["cli_extra_args"]
