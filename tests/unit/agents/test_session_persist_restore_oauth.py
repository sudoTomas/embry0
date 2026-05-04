"""claude-max mode: capture session_id + session_blob; restore them as
--resume <id> + bytes-on-disk for the CLI."""


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


def test_restore_writes_blob_to_canonical_projects_path(tmp_path):
    """When project_cwd is given, restore writes to projects/<sanitize(cwd)>/<id>.jsonl
    so the current CLI finds it via its first lookup branch."""
    from athanor.agents.session import AgentSession, restore_session_into_options

    sess = AgentSession(
        job_id="J",
        agent_type="developer",
        mode="claude_max",
        session_id="sess-abc",
        session_blob=b"line1\n",
    )
    options = {
        "cli_extra_args": [],
        "home_dir": str(tmp_path),
        "project_cwd": "/workspace",
    }
    out = restore_session_into_options(sess, options)

    # Blob written to ~/.claude/projects/-workspace/<id>.jsonl
    expected = tmp_path / ".claude" / "projects" / "-workspace" / "sess-abc.jsonl"
    legacy = tmp_path / ".claude" / "sessions" / "sess-abc.jsonl"
    assert expected.read_bytes() == b"line1\n"
    assert not legacy.exists(), "should not write to legacy sessions/ path when project_cwd is provided"

    assert "--resume" in out["cli_extra_args"]
    assert "sess-abc" in out["cli_extra_args"]


def test_restore_falls_back_to_legacy_sessions_path_when_no_project_cwd(tmp_path):
    """When project_cwd is absent, fall back to legacy sessions/ layout —
    canonical_session_path_for handles that case explicitly."""
    from athanor.agents.session import AgentSession, restore_session_into_options

    sess = AgentSession(
        job_id="J",
        agent_type="developer",
        mode="claude_max",
        session_id="sess-legacy",
        session_blob=b"data\n",
    )
    options = {"cli_extra_args": [], "home_dir": str(tmp_path)}  # no project_cwd
    restore_session_into_options(sess, options)

    expected = tmp_path / ".claude" / "sessions" / "sess-legacy.jsonl"
    assert expected.read_bytes() == b"data\n"
