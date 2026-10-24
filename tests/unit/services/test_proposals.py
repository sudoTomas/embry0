"""Owner proposals (spec 2026-10-24): findings read one at a time, owners from CODEOWNERS, the feed's rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embry0.services.proposals import (
    Decision,
    case_proposal,
    codeowners_for_all,
    finding_id,
    finding_proposals,
    finding_task,
    for_owner,
    issue_proposal,
    parse_findings,
    still_open,
    with_owners,
)

T0 = datetime(2026, 10, 24, 12, tzinfo=UTC)

REPORT = """I'll audit the files first.
Stale: 3
Files: 2

## Findings

| Severity | Location | Claim | Evidence | Fix |
|---|---|---|---|---|
| misleads | `AGENTS.md:12` | Tests run with `make check` | the Makefile has no check target | say `make test` |
| dead | docs/setup.md:40-44 | Run `./setup.sh` first | setup.sh was removed in 2025 | drop the step |
| drift | not a location | something | evidence | fix |

## Pull

```diff
--- a/AGENTS.md
+++ b/AGENTS.md
```
"""


# ---- findings ----


def test_each_row_of_the_findings_table_is_a_finding() -> None:
    findings = parse_findings(REPORT)
    assert [(f.severity, f.path, f.line) for f in findings] == [
        ("misleads", "AGENTS.md", 12),
        ("dead", "docs/setup.md", 40),
    ], "a row whose Location is no file is skipped; the table ends at the next section"
    assert findings[0].claim == "Tests run with `make check`" and findings[0].fix == "say `make test`"


def test_a_report_without_the_table_has_no_findings() -> None:
    assert parse_findings(None) == [] and parse_findings("Stale: 0\nFiles: 3\n") == []
    assert parse_findings("## Findings\n\nNone found.\n") == []


def test_a_finding_keeps_its_id_across_audits() -> None:
    a = finding_id("Acme/App", "AGENTS.md", "Tests run with  `make check`")
    b = finding_id("acme/app", "AGENTS.md", "tests run with `make check`")
    assert a == b and a.startswith("finding:"), "case and spacing do not make a new finding"
    assert finding_id("acme/app", "README.md", "tests run with `make check`") != a


def test_findings_become_proposals_that_ask_the_owner() -> None:
    proposals = finding_proposals("acme/app", "job-1", T0, REPORT)
    first = proposals[0]
    assert first.kind == "finding" and first.id == finding_id("acme/app", "AGENTS.md", "Tests run with `make check`")
    assert (
        first.summary
        == 'We found this in AGENTS.md:12: "Tests run with `make check`". Is that what you want it to say?'
    )
    assert "Proposed fix: say `make test`" in first.details
    assert first.extra["audit_job_id"] == "job-1" and first.action == "Open a pull request fixing AGENTS.md"


def test_the_fix_task_names_one_file_and_one_claim() -> None:
    task = finding_task("acme/app", "abc123", finding_proposals("acme/app", "job-1", T0, REPORT)[0])
    assert "Instruction files (1):\n- AGENTS.md" in task
    assert "The claim at AGENTS.md:12: Tests run with `make check`" in task
    assert "Change only this claim" in task and "abc123" in task


# ---- owners ----


def test_codeowners_names_the_users_of_the_last_rule_for_every_file() -> None:
    text = """
# Everything
*       @first-owner
/docs/  @docs-team-lead
*       @alice @acme/platform bob@example.com @carol   # the newer rule wins
"""
    assert codeowners_for_all(text) == ["alice", "carol"], "teams and emails are skipped"
    assert codeowners_for_all("/src/ @alice\n") == [] and codeowners_for_all(None) == []


def test_owners_are_stamped_and_filter_the_feed() -> None:
    issue = issue_proposal({"id": "i-1", "repo": "Acme/App", "title": "Fix login", "created_at": T0})
    stamped = with_owners([issue], {"acme/app": ["Octo"]})
    assert stamped[0].owners == ("Octo",)
    assert for_owner(stamped, "@octo") == stamped and for_owner(stamped, "someone") == []
    assert for_owner(stamped, None) == stamped


# ---- the kinds and the answers ----


def test_a_case_proposal_says_what_the_watcher_keeps_seeing() -> None:
    case = case_proposal(
        {
            "id": "hc-1",
            "repo": "acme/app",
            "title": "Timeout calling the rates service",
            "severity": "high",
            "occurrence_count": 14,
            "signature": '{app="api"} |= "timeout"',
            "tracker_url": "https://forge/issues/9",
            "detected_at": T0,
            "tracked_at": T0,
            "created_at": T0,
        }
    )
    assert case.id == "case:hc-1"
    assert case.summary == "The watcher keeps seeing this in production (14x, high): Timeout calling the rates service"
    assert "Draft ticket: https://forge/issues/9" in case.details and case.url == "https://forge/issues/9"


def test_an_answer_closes_a_proposal_and_a_snooze_until_it_ends() -> None:
    def decided(decision: str, until: datetime | None = None) -> dict[str, Decision]:
        return {"issue:i-1": Decision("issue:i-1", decision, "octo", T0, until)}

    assert still_open("issue:i-1", {}, T0)
    assert not still_open("issue:i-1", decided("accept"), T0)
    assert not still_open("issue:i-1", decided("decline"), T0)
    assert not still_open("issue:i-1", decided("snooze", T0 + timedelta(days=1)), T0)
    assert still_open("issue:i-1", decided("snooze", T0 + timedelta(days=1)), T0 + timedelta(days=2))
