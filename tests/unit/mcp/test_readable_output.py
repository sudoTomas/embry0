"""Unit tests for embry0.mcp_server.server readable-job-output helpers.

Regression cover for the MCP returning raw API payloads: a job read echoed its
whole `task` prompt back (thousands of tokens) and buried status/stage/spend/PR.
"""

from embry0.mcp_server import server

JOB = {
    "job_id": "job-abc123",
    "status": "running",
    "current_stage": "developer",
    "total_cost_usd": 3.4912,
    "pipeline_config": {"max_budget_usd": 14},
    "repo": "owner/name",
    "pr_url": "https://github.com/owner/name/pull/211",
    "error_message": None,
    "task": "x" * 5000,
}


def test_summary_is_one_glanceable_line():
    out = server._compact_job(JOB)
    summary = out["summary"]
    assert "\n" not in summary
    for fragment in ("job-abc123", "running", "stage=developer", "$3.49/$14", "owner/name", "PR #211"):
        assert fragment in summary


def test_echoed_task_is_traded_for_a_bounded_preview():
    out = server._compact_job(JOB)
    assert "task" not in out
    assert out["task_chars"] == 5000
    assert len(out["task_preview"]) <= server._TASK_PREVIEW_CHARS + 1  # + ellipsis


def test_short_task_is_left_alone():
    out = server._compact_job({**JOB, "task": "fix the thing"})
    assert out["task"] == "fix the thing"
    assert "task_preview" not in out


def test_structured_fields_survive():
    out = server._compact_job(JOB)
    for key in ("job_id", "status", "current_stage", "total_cost_usd", "pr_url", "repo"):
        assert out[key] == JOB[key]


def test_error_surfaces_in_summary():
    out = server._compact_job({**JOB, "status": "paused", "error_message": "boom happened"})
    assert "ERROR: boom happened" in out["summary"]


def test_missing_budget_and_pr_degrade_quietly():
    out = server._compact_job({"job_id": "job-x", "status": "pending", "current_stage": None, "pipeline_config": {}})
    assert "stage=-" in out["summary"]
    assert "$0.00" in out["summary"]
    assert "PR" not in out["summary"]


def test_short_pr_extracts_number_else_passes_through():
    assert server._short_pr("https://github.com/o/n/pull/7") == "#7"
    assert server._short_pr(None) == "-"
    assert server._short_pr("https://example.com/thing") == "https://example.com/thing"


def test_job_list_gains_an_aligned_board():
    envelope = {"jobs": [JOB, {**JOB, "job_id": "job-z", "status": "failed", "pr_url": None}], "total": 2}
    out = server._compact_jobs(envelope)
    lines = out["board"].splitlines()
    assert lines[0].split() == ["JOB", "STATUS", "STAGE", "COST", "PR"]
    assert len(lines) == 3
    # columns aligned: every row padded to the same width as the header row
    assert len({len(line.rstrip()) > 0 for line in lines}) == 1
    assert out["total"] == 2


def test_non_job_payloads_pass_through_untouched():
    assert server._compact_job("not a dict") == "not a dict"
    assert server._compact_jobs({"unexpected": 1}) == {"unexpected": 1}


def test_a_budget_that_is_not_a_number_is_left_out_of_the_summary():
    """A stored budget past a float's range reads as its digits (#537); a config too deep to show is served as text.
    The summary leaves either budget out instead of failing the whole read, or the whole list."""
    past = "1" + "0" * 400 + ".5"
    for config in ({"max_budget_usd": past}, {"max_budget_usd": True}, "[a document nested too deep to show]"):
        out = server._compact_job({**JOB, "pipeline_config": config})
        assert "$3.49 " in out["summary"] + " " and "/$" not in out["summary"], (config, out["summary"])
    listed = server._compact_jobs({"jobs": [{**JOB, "pipeline_config": {"max_budget_usd": past}}, JOB], "total": 2})
    assert [j["job_id"] for j in listed["jobs"]] == ["job-abc123", "job-abc123"]
    assert "$3.49/$14" in listed["jobs"][1]["summary"], "a real budget is still shown"
