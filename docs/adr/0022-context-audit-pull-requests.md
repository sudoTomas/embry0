# ADR 0022: A context audit opens a pull request from the patch it checked, when its organization turns that on

Status: accepted (2026-10-24)

A context audit (ADR 0020) ends its report with a patch for the outdated claims it is certain of, checked with `git apply --check` against a copy of the workspace. Until now a person had to copy that patch out of the report. An organization whose `context_audit_open_prs` is `true` (off by default, written with `PATCH /orgs/{owner}` like its other settings) now gets a pull request from it.

The pull request is made inside the audit job, on the paths every job already uses. The job's output step takes the patch from the report's `diff` block under its `## Pull` heading, applies it to the workspace on the one branch the sandbox may push, commits and pushes through the git proxy, and hands the executor the branch with a title and a body (the report's findings, without the patch). The executor opens the pull request at completion as it does for any branch a job pushed and did not deliver, with the `embry0` label and the deployment's merge approvals. The orchestrator never applies a patch itself, so no second push path exists outside the sandbox.

The patch is read from the report, not from a file the prompt names: a builtin agent's prompt reaches a deployment's stored row only when the row is created, so a prompt change would not reach running deployments, and the report is what a reviewer reads.

One audit pull request is open per repository at a time: while the open-PR list the executor reads before a job names a branch an earlier audit pushed, a later audit only reports. Nothing here fails a job: a report with no patch, an audit that found nothing, or a patch that no longer applies leaves the findings in the report and opens nothing. A person always merges.

Evidence: `embry0/workflows/issue_to_pr/audit_delivery.py`; `finalize_output_node` in `embry0/workflows/issue_to_pr/plan_route.py`; `_open_fallback_pr` in `embry0/services/_executor_outcomes.py`; `context_audit_open_prs` in `embry0/services/organizations.py`.
