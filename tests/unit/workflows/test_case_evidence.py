"""Case evidence for developer/reviewer prompts (SH-P4.4): two sources, bounded, redacted."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.workflows.issue_to_pr.evidence import (
    MAX_SAMPLE_LINE_CHARS,
    MAX_SAMPLE_LINES,
    MAX_SIGNATURE_CHARS,
    acceptance_criteria_in,
    format_evidence_block,
    log_scope,
    resolve_case_evidence,
    services_named_in,
    ticket_section,
)

_ALLOW = ["acme-backend", "acme-frontend", "acme-ml"]

_TICKET = """[ENG-1344] fix(ml): lead-contacts extraction returns truncated JSON

## Evidence

acme-backend logged 4x within ~90s (Authorization: Bearer abcdef0123456789):

```
# retried by jane.doe@example.com
org.springframework.web.client.HttpServerErrorException$BadGateway: 502 Bad Gateway
```

## Impact

Leads never extract contacts. acme-frontend shows a spinner.

## Acceptance criteria
- [ ] a lead with 40 contacts extracts
- [ ] the 502 stops recurring
"""


class _Cases:
    def __init__(self, by_job: dict[str, Any] | None = None, by_issue: dict[str, Any] | None = None, boom=False):
        self.by_job, self.by_issue, self.boom = by_job or {}, by_issue or {}, boom
        self.calls: list[tuple[str, str]] = []

    async def find_by_job(self, job_id: str) -> dict[str, Any] | None:
        self.calls.append(("job", job_id))
        if self.boom:
            raise RuntimeError("db down")
        return self.by_job.get(job_id)

    async def find_by_issue(self, issue_id: str) -> dict[str, Any] | None:
        self.calls.append(("issue", issue_id))
        return self.by_issue.get(issue_id)


# ---- ticket parsing -------------------------------------------------------


def test_section_stops_at_the_next_heading_but_not_inside_a_fence() -> None:
    section = ticket_section(_TICKET, "Evidence")
    assert section is not None
    assert "# retried by" in section  # a '#' line inside the fence is not a heading
    assert "Impact" not in section and "Leads never" not in section


def test_section_absent() -> None:
    assert ticket_section("just a task\nwith lines", "Evidence") is None
    assert ticket_section("", "Evidence") is None


def test_acceptance_criteria() -> None:
    assert acceptance_criteria_in(_TICKET) == "- [ ] a lead with 40 contacts extracts\n- [ ] the 502 stops recurring"
    assert acceptance_criteria_in("no such section") == ""


def test_services_are_only_allowlisted_names_as_whole_words() -> None:
    text = "acme-backend and ACME-ML failed; not acme-backend-old, not embry0-postgres, xacme-frontend"
    assert services_named_in(text, _ALLOW) == ["acme-backend", "acme-ml"]
    assert services_named_in(text, []) == []


# ---- source 2: the ticket -------------------------------------------------


async def test_ticket_source_when_no_case_is_linked() -> None:
    cases = _Cases()
    ev = await resolve_case_evidence(job_id="j1", issue_id="i1", ticket_text=_TICKET, allowlist=_ALLOW, cases=cases)
    assert ev is not None
    assert ev.source == "ticket" and ev.case_id is None and ev.signature == ""
    # Services come from the EVIDENCE section only — acme-frontend is named under Impact.
    assert ev.services == ("acme-backend",)
    joined = "\n".join(ev.samples)
    assert "502 Bad Gateway" in joined
    assert "abcdef0123456789" not in joined and "jane.doe@example.com" not in joined
    assert "```" not in joined
    assert cases.calls == [("job", "j1"), ("issue", "i1")]


async def test_no_case_and_no_evidence_section_is_no_evidence() -> None:
    ev = await resolve_case_evidence(
        job_id="j1",
        issue_id=None,
        ticket_text="Add a dark mode toggle to acme-frontend",
        allowlist=_ALLOW,
        cases=_Cases(),
    )
    assert ev is None


async def test_works_without_a_ledger() -> None:
    ev = await resolve_case_evidence(job_id="j1", issue_id="i1", ticket_text=_TICKET, allowlist=_ALLOW, cases=None)
    assert ev is not None and ev.source == "ticket"


async def test_ledger_failure_degrades_to_the_ticket() -> None:
    ev = await resolve_case_evidence(
        job_id="j1", issue_id="i1", ticket_text=_TICKET, allowlist=_ALLOW, cases=_Cases(boom=True)
    )
    assert ev is not None and ev.source == "ticket"


# ---- source 1: the linked case -------------------------------------------


async def test_case_linked_by_job_supplies_the_signature() -> None:
    case = {
        "id": "case-1",
        "signature": "lead-contacts 502 no JSON object",
        "fingerprint": "fp",
        "deploy_services": None,
    }
    cases = _Cases(by_job={"j1": case})
    ev = await resolve_case_evidence(job_id="j1", issue_id="i1", ticket_text=_TICKET, allowlist=_ALLOW, cases=cases)
    assert ev is not None
    assert (ev.source, ev.case_id, ev.signature) == ("case", "case-1", "lead-contacts 502 no JSON object")
    assert ev.samples  # the ledger holds no raw lines: samples still come from the ticket
    assert cases.calls == [("job", "j1")]


async def test_case_found_by_issue_and_fingerprint_fallback() -> None:
    case = {"id": "case-2", "signature": None, "fingerprint": "mailbox-403", "deploy_services": ["acme-ml", "nope"]}
    ev = await resolve_case_evidence(
        job_id="j1", issue_id="i1", ticket_text="fix the mailbox", allowlist=_ALLOW, cases=_Cases(by_issue={"i1": case})
    )
    assert ev is not None
    assert ev.source == "case" and ev.signature == "mailbox-403" and ev.samples == ()
    assert ev.services == ("acme-ml",)  # deploy_services ∩ allowlist


async def test_overlong_signature_is_dropped_not_truncated() -> None:
    case = {"id": "c", "signature": "x " * MAX_SIGNATURE_CHARS, "fingerprint": None}
    ev = await resolve_case_evidence(
        job_id="j", issue_id=None, ticket_text=_TICKET, allowlist=_ALLOW, cases=_Cases(by_job={"j": case})
    )
    assert ev is not None and ev.signature == ""


async def test_signature_is_redacted() -> None:
    case = {"id": "c", "signature": "401 for bob@example.com", "fingerprint": None}
    ev = await resolve_case_evidence(
        job_id="j", issue_id=None, ticket_text="", allowlist=_ALLOW, cases=_Cases(by_job={"j": case})
    )
    assert ev is not None and ev.signature == "401 for <email>"


# ---- bounds ---------------------------------------------------------------


async def test_samples_are_bounded() -> None:
    body = "\n".join(f"line {i} " + "z" * 1000 for i in range(200))
    ev = await resolve_case_evidence(
        job_id="j", issue_id=None, ticket_text=f"t\n\n## Evidence\n{body}\n", allowlist=_ALLOW, cases=None
    )
    assert ev is not None
    assert len(ev.samples) == MAX_SAMPLE_LINES
    assert all(len(s) <= MAX_SAMPLE_LINE_CHARS + 1 for s in ev.samples)
    assert ev.samples[0].endswith("…")


# ---- state round trip + rendering ----------------------------------------


async def test_state_shape_is_plain_json_and_renders() -> None:
    case = {"id": "case-1", "signature": "sig", "fingerprint": None}
    ev = await resolve_case_evidence(
        job_id="j", issue_id=None, ticket_text=_TICKET, allowlist=_ALLOW, cases=_Cases(by_job={"j": case})
    )
    assert ev is not None
    state = ev.as_state()
    import json

    assert json.loads(json.dumps(state)) == state
    block = format_evidence_block(state)
    assert "Signature: sig" in block and "acme-backend" in block and "502 Bad Gateway" in block
    assert "not instructions" in block
    # Handling rule, not just an injection rule: this text reaches commits,
    # test fixtures and PR descriptions unless the agent is told otherwise.
    assert "never copy log content" in block.lower()
    for destination in ("commit", "test", "comment", "pull request"):
        assert destination in block.lower()
    assert "abcdef0123456789" not in block


@pytest.mark.parametrize("empty", [None, {}, {"signature": "", "samples": [], "services": []}])
def test_nothing_to_render(empty) -> None:
    assert format_evidence_block(empty) == ""


# ---- what may grant log access ------------------------------------------


def test_scope_needs_a_described_failure_not_just_service_names() -> None:
    """A case's deploy_services names services without describing anything to
    look for. Granting on that alone would hand a 30-query budget to a job whose
    prompt block reads only "Services: acme-backend" — and it goes live the
    moment the ledger writers start filling deploy_services (#340)."""
    assert log_scope({"services": ["acme-backend"], "signature": "", "samples": []}) == []
    assert log_scope({"services": ["acme-backend"], "signature": "502 no JSON", "samples": []}) == ["acme-backend"]
    assert log_scope({"services": ["acme-backend"], "signature": "", "samples": ["502 Bad Gateway"]}) == [
        "acme-backend"
    ]


def test_scope_needs_a_service() -> None:
    assert log_scope({"services": [], "signature": "502", "samples": ["x"]}) == []


@pytest.mark.parametrize("empty", [None, {}, {"services": ["acme-backend"], "signature": "   "}])
def test_no_scope_at_all(empty) -> None:
    assert log_scope(empty) == []


async def test_deploy_services_alone_resolves_evidence_but_grants_nothing() -> None:
    """The prompt still says which services the case touched — it just is not
    grounds for log access."""
    case = {"id": "c", "signature": None, "fingerprint": None, "deploy_services": ["acme-ml"]}
    ev = await resolve_case_evidence(
        job_id="j", issue_id=None, ticket_text="fix it", allowlist=_ALLOW, cases=_Cases(by_job={"j": case})
    )
    assert ev is not None and ev.services == ("acme-ml",)
    assert ev.signature == "" and ev.samples == ()
    assert log_scope(ev.as_state()) == []


# ---- allowlist patterns (containers named per release) -------------------

_SLOTS = ["acme-backend*", "acme-frontend"]

_SLOT_TICKET = """[ENG-2001] fix: lead export 502s after the blue/green switch

## Evidence

acme-backend-leads-green logged 6x; acme-backend-green proxied it:

```
502 Bad Gateway from http://acme-backend-leads-green:3101/api/v1/leads/export
```

## Impact

acme-backend-blue (the previous slot) was already retired.
"""


def test_services_named_in_resolves_pattern_entries_to_the_names_written() -> None:
    text = "acme-backend-green and ACME-FRONTEND failed; not xacme-backend-blue, not acme-ml-green"
    assert services_named_in(text, _SLOTS) == ["acme-backend-green", "acme-frontend"]
    # An exact entry is still not a prefix.
    assert services_named_in("acme-frontend-old", _SLOTS) == []


async def test_ticket_evidence_names_slot_containers_through_a_pattern() -> None:
    ev = await resolve_case_evidence(job_id="j", issue_id=None, ticket_text=_SLOT_TICKET, allowlist=_SLOTS, cases=None)
    assert ev is not None
    # The Evidence section only: the retired slot under Impact is not granted.
    assert ev.services == ("acme-backend-green", "acme-backend-leads-green")
    assert log_scope(ev.as_state()) == ["acme-backend-green", "acme-backend-leads-green"]


async def test_case_deploy_services_resolve_through_a_pattern() -> None:
    case = {
        "id": "c",
        "signature": "502 Bad Gateway",
        "deploy_services": ["acme-backend-b-blue", "acme-backend*", "db"],
    }
    ev = await resolve_case_evidence(
        job_id="j", issue_id=None, ticket_text="fix it", allowlist=_SLOTS, cases=_Cases(by_job={"j": case})
    )
    assert ev is not None and ev.services == ("acme-backend-b-blue",)
