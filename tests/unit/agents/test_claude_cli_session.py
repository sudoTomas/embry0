"""Discovery of Claude CLI session files. Pure function — no I/O outside tmp_path."""

from pathlib import Path

from embry0.agents.claude_cli_session import (
    canonical_session_path_for,
    find_session_file,
    sanitize_cwd_for_session_dir,
)


def test_sanitize_cwd_uses_leading_dash_and_replaces_slashes():
    assert sanitize_cwd_for_session_dir("/workspace/macro-lab") == "-workspace-macro-lab"
    assert sanitize_cwd_for_session_dir("/workspace") == "-workspace"
    assert sanitize_cwd_for_session_dir("/") == "-"


def test_finds_in_projects_layout(tmp_path: Path):
    """Current CLI: ~/.claude/projects/-workspace/<id>.jsonl"""
    home = tmp_path
    proj_dir = home / ".claude" / "projects" / "-workspace"
    proj_dir.mkdir(parents=True)
    target = proj_dir / "sess-abc.jsonl"
    target.write_text('{"foo":"bar"}\n')

    found = find_session_file(home_dir=home, session_id="sess-abc", project_cwd="/workspace")
    assert found == target


def test_finds_in_legacy_sessions_layout(tmp_path: Path):
    """Legacy CLI: ~/.claude/sessions/<id>.jsonl"""
    home = tmp_path
    sess_dir = home / ".claude" / "sessions"
    sess_dir.mkdir(parents=True)
    target = sess_dir / "sess-old.jsonl"
    target.write_text('{"foo":"bar"}\n')

    found = find_session_file(home_dir=home, session_id="sess-old")
    assert found == target


def test_projects_layout_wins_when_both_present(tmp_path: Path):
    """If a session id appears in BOTH layouts, prefer projects/ (newer CLI wrote it last)."""
    home = tmp_path
    sess_dir = home / ".claude" / "sessions"
    sess_dir.mkdir(parents=True)
    proj_dir = home / ".claude" / "projects" / "-workspace"
    proj_dir.mkdir(parents=True)
    legacy = sess_dir / "sess-x.jsonl"
    current = proj_dir / "sess-x.jsonl"
    legacy.write_text("legacy\n")
    current.write_text("current\n")

    found = find_session_file(home_dir=home, session_id="sess-x", project_cwd="/workspace")
    assert found == current
    assert found.read_text() == "current\n"


def test_glob_finds_session_id_under_unknown_project_dir(tmp_path: Path):
    """If we don't know the project_cwd, glob projects/*/<id>.jsonl as fallback."""
    home = tmp_path
    proj_dir = home / ".claude" / "projects" / "-some-other-cwd"
    proj_dir.mkdir(parents=True)
    target = proj_dir / "sess-glob.jsonl"
    target.write_text("found via glob\n")

    found = find_session_file(home_dir=home, session_id="sess-glob")  # no project_cwd
    assert found == target


def test_returns_none_when_session_id_does_not_exist(tmp_path: Path):
    home = tmp_path
    (home / ".claude" / "projects").mkdir(parents=True)
    found = find_session_file(home_dir=home, session_id="sess-missing")
    assert found is None


def test_canonical_path_for_writes_to_projects_layout(tmp_path: Path):
    """canonical_session_path_for returns where we should WRITE a session blob
    on resume — the path the current CLI will look in."""
    home = tmp_path
    path = canonical_session_path_for(home_dir=home, session_id="sess-new", project_cwd="/workspace")
    assert path == home / ".claude" / "projects" / "-workspace" / "sess-new.jsonl"


def test_canonical_path_for_handles_missing_project_cwd(tmp_path: Path):
    """When project_cwd is unknown, canonical falls back to legacy sessions/ layout."""
    home = tmp_path
    path = canonical_session_path_for(home_dir=home, session_id="sess-new")
    # Legacy layout when we can't compute the projects/ subdir
    assert path == home / ".claude" / "sessions" / "sess-new.jsonl"


def test_multiple_matches_picks_lexicographically_first_and_warns(tmp_path: Path):
    """When the glob fallback finds multiple session files with the same id
    across different project_cwd directories, pick the lexicographically
    first one for deterministic behavior and emit a warning."""
    import structlog.testing

    home = tmp_path
    proj_a = home / ".claude" / "projects" / "-aaa"
    proj_b = home / ".claude" / "projects" / "-bbb"
    proj_a.mkdir(parents=True)
    proj_b.mkdir(parents=True)
    (proj_a / "sess-dup.jsonl").write_text("a\n")
    (proj_b / "sess-dup.jsonl").write_text("b\n")

    with structlog.testing.capture_logs() as log_entries:
        # No project_cwd → forces the glob fallback branch
        found = find_session_file(home_dir=home, session_id="sess-dup")

    assert found == proj_a / "sess-dup.jsonl"  # -aaa sorts before -bbb
    assert any(entry.get("event") == "multiple_session_files_found_picking_first" for entry in log_entries)
