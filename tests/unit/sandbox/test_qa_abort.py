"""qa_abort — the QA agent's last-resort self-abort helper (EMB-37).

Runs inside the sandbox (where embry0.workflows is NOT shipped), so it
builds a plain dict; these tests validate that dict against the real
QAResult schema so drift is caught in CI.
"""

from __future__ import annotations

import json

from embry0.sandbox.qa_abort import build_partial_result, main
from embry0.workflows.qa.result_schema import QAResult

JOB = {
    "schema_version": 1,
    "job_id": "job-1__web",
    "attempt_n": 1,
    "acceptance_criteria": ["dashboard renders", "nav reachable"],
}


def test_build_partial_result_validates_against_qaresult_schema() -> None:
    payload = build_partial_result(JOB, "browser context unrecoverable")
    validated = QAResult.model_validate(payload)
    assert validated.overall == "inconclusive"
    assert validated.job_id == "job-1__web"
    assert [r.criterion for r in validated.acceptance_results] == JOB["acceptance_criteria"]
    assert all(r.status == "inconclusive" for r in validated.acceptance_results)
    assert "browser context unrecoverable" in (validated.acceptance_results[0].notes or "")


def test_build_partial_result_tolerates_missing_fields() -> None:
    payload = build_partial_result({}, "reason")
    validated = QAResult.model_validate(payload)
    assert validated.job_id == "unknown"
    assert validated.acceptance_results == []


def test_main_writes_result_json(tmp_path) -> None:
    job_path = tmp_path / "job.json"
    out_path = tmp_path / "result.json"
    job_path.write_text(json.dumps(JOB))

    rc = main(["--reason", "stuck on login wall", "--job-json", str(job_path), "--out", str(out_path)])
    assert rc == 0
    written = json.loads(out_path.read_text())
    QAResult.model_validate(written)
    assert written["overall"] == "inconclusive"
    assert "stuck on login wall" in written["acceptance_results"][0]["notes"]


def test_main_fails_cleanly_without_job_json(tmp_path) -> None:
    rc = main(["--reason", "r", "--job-json", str(tmp_path / "missing.json"), "--out", str(tmp_path / "o.json")])
    assert rc != 0
