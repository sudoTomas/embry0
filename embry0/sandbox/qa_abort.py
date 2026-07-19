"""Self-abort helper for the QA agent (EMB-37).

When a QA run is stuck (unrecoverable browser state, unreachable target,
repeated tool failures) the agent runs this INSIDE the sandbox via Bash:

    python -m embry0.sandbox.qa_abort --reason "<one line why>"

It reads /workspace/.qa/job.json and writes a schema-valid, all-inconclusive
/workspace/.qa/result.json carrying the reason — so collect_artifacts reports
a real QA verdict (INCONCLUSIVE with per-criterion notes) instead of the run
thrashing to the turn cap and landing with no result at all.

NOTE: the sandbox image ships embry0/sandbox/ but NOT embry0/workflows/, so
this module builds a plain dict rather than importing the QAResult pydantic
model. tests/unit/sandbox/test_qa_abort.py validates the dict against the
real schema in CI, catching drift.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

JOB_JSON_PATH = "/workspace/.qa/job.json"
RESULT_JSON_PATH = "/workspace/.qa/result.json"


def build_partial_result(job: dict[str, Any], reason: str) -> dict[str, Any]:
    """Map job.json onto an all-inconclusive result.json payload."""
    note = f"aborted by agent: {reason}"
    return {
        "schema_version": 1,
        "job_id": str(job.get("job_id", "unknown")),
        "attempt_n": int(job.get("attempt_n", 1)),
        "phase_reached": "exploratory",
        "overall": "inconclusive",
        "boot": None,
        "seed": None,
        "e2e": None,
        "acceptance_results": [
            {
                "criterion": criterion,
                "status": "inconclusive",
                "evidence": [],
                "notes": note,
                "console_errors": [],
                "network_failures": [],
                "log_excerpts": [],
            }
            for criterion in job.get("acceptance_criteria", [])
        ],
        "anomalies": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write an all-inconclusive result.json and give up gracefully.")
    parser.add_argument("--reason", required=True, help="one line: why the run is being aborted")
    parser.add_argument("--job-json", default=JOB_JSON_PATH)
    parser.add_argument("--out", default=RESULT_JSON_PATH)
    args = parser.parse_args(argv)

    try:
        with open(args.job_json, encoding="utf-8") as f:
            job = json.load(f)
    except OSError as exc:
        print(f"qa_abort: cannot read {args.job_json}: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"qa_abort: {args.job_json} is not valid JSON: {exc}", file=sys.stderr)
        return 1

    payload = build_partial_result(job, args.reason)
    try:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except OSError as exc:
        print(f"qa_abort: cannot write {args.out}: {exc}", file=sys.stderr)
        return 1

    print(
        f"qa_abort: wrote {args.out} ({len(payload['acceptance_results'])} criteria inconclusive). "
        "End the run now: reply with a short summary and stop calling tools."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
