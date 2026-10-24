"""docs/context-audit.md lists the files the context audit reads (ADR 0020), and stays in step with the code."""

import re
from pathlib import Path

from embry0.mcp_server import server
from embry0.services.instruction_files import INSTRUCTION_FILE_GLOBS, SKIPPED_DIRS

ROOT = Path(__file__).resolve().parents[3]


def test_the_doc_lists_every_instruction_file_glob_and_skipped_directory():
    text = (ROOT / "docs" / "context-audit.md").read_text()
    missing = [glob for glob in INSTRUCTION_FILE_GLOBS if glob not in text]
    assert not missing, f"docs/context-audit.md does not list {missing}"
    skipped = [name for name in sorted(SKIPPED_DIRS) if f"`{name}`" not in text]
    assert not skipped, f"docs/context-audit.md does not name the skipped directories {skipped}"


def test_adr_0020_indexed():
    assert (ROOT / "docs" / "adr" / "0020-context-audit-schedule.md").exists()
    assert "0020-context-audit-schedule.md" in (ROOT / "docs" / "adr" / "README.md").read_text()


def test_adr_0021_indexed_and_the_caps_documented():
    assert (ROOT / "docs" / "adr" / "0021-context-audit-caps-per-organization.md").exists()
    assert "0021-context-audit-caps-per-organization.md" in (ROOT / "docs" / "adr" / "README.md").read_text()
    for doc in ("context-audit.md", "api.md"):
        text = (ROOT / "docs" / doc).read_text()
        for setting in ("context_audit_budget_usd", "context_audit_max_jobs_per_tick"):
            assert f"`{setting}`" in text, f"docs/{doc} does not name {setting}"


def test_adr_0022_indexed_and_the_setting_documented():
    assert (ROOT / "docs" / "adr" / "0022-context-audit-pull-requests.md").exists()
    assert "0022-context-audit-pull-requests.md" in (ROOT / "docs" / "adr" / "README.md").read_text()
    for doc in ("context-audit.md", "api.md"):
        assert "`context_audit_open_prs`" in (ROOT / "docs" / doc).read_text(), f"docs/{doc} does not name it"


def test_the_mcp_section_calls_tools_the_server_has():
    text = (ROOT / "docs" / "context-audit.md").read_text()
    section = text.split("## From an MCP client", 1)[1].split("\n## ", 1)[0]
    called = set(re.findall(r"`([a-z_]+)\(", section))
    assert {"org_update", "context_audit_status", "context_audit_run", "job_create"} <= called
    missing = sorted(name for name in called if not callable(getattr(server, name, None)))
    assert missing == [], f"docs/context-audit.md calls MCP tools the server does not have: {missing}"
