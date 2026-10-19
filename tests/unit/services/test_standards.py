"""Organization standards (ADR 0014): validation, the audit task, and reading an audit's report back."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from embry0.services import standards
from embry0.services.standards import (
    AUDIT_TEMPLATES,
    KINDS,
    MAX_DOCUMENT_CHARS,
    RepoStandardState,
    Standard,
    audit_report_text,
    audit_task,
    count_findings,
    normalize_owner,
    owner_of,
    parse_audit_report,
    parse_score,
    record_audit_completion,
    validate_standard,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "standards-audit"

SCHEMA: dict[str, Any] = {
    "roles": {
        "color.base": {"type": "color", "description": "page background"},
        "color.accent": {"type": "color", "description": "the one accent"},
        "font.body": {"type": "font-family"},
    },
    "type_scale": {"body": {"size": "14px", "weight": 400}},
    "rules": [{"id": "one-accent", "text": "One accent colour only."}],
}
VALUES: dict[str, Any] = {"color.base": "#111111", "color.accent": "#3366ff", "font.body": "system-ui"}


def _standard(**overrides: Any) -> Standard:
    fields: dict[str, Any] = {
        "id": "std-0123456789ab",
        "owner": "acme",
        "kind": "design",
        "version": 3,
        "status": "active",
        "schema": SCHEMA,
        "values": VALUES,
        "prose": "Dense tables, one accent.",
        "references": ["acme/site"],
    }
    return Standard(**{**fields, **overrides})


# ---- vocabulary ----


def test_every_kind_has_an_audit_template() -> None:
    assert KINDS == ("design",)
    assert set(AUDIT_TEMPLATES) == set(KINDS)
    assert AUDIT_TEMPLATES["design"] == "design-audit"


@pytest.mark.parametrize(
    ("raw", "owner"), [("Acme", "acme"), (" acme-labs ", "acme-labs"), ("a.b_c", "a.b_c"), ("acme\n", "acme")]
)
def test_an_owner_is_stored_lowercase(raw: str, owner: str) -> None:
    assert normalize_owner(raw) == owner


@pytest.mark.parametrize("raw", ["", "acme/widgets", "-acme", "ac me", "acme\nwidgets"])
def test_an_owner_that_is_not_one_is_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_owner(raw)


def test_owner_of_is_the_first_segment() -> None:
    assert owner_of("Acme/Widgets") == "acme"
    assert owner_of("group/sub/project") == "group"


# ---- validation ----


def test_a_whole_standard_validates() -> None:
    assert validate_standard(SCHEMA, VALUES, "prose", ["acme/site", "https://example.com/brand"]) == []
    assert validate_standard({}, {}) == [], "an empty standard is a standard with nothing in it yet"
    partial = {k: v for k, v in VALUES.items() if k != "font.body"}
    assert validate_standard(SCHEMA, partial) == [], "a role without a value is allowed"


def test_schema_and_values_must_be_objects() -> None:
    assert validate_standard([], {}) == ["schema must be an object"]
    assert validate_standard({}, "x") == ["values must be an object"]
    assert validate_standard(None, None) == ["schema must be an object", "values must be an object"]


def test_every_value_must_name_a_declared_role() -> None:
    problems = validate_standard(SCHEMA, {**VALUES, "color.accent2": "#22aa88"})
    assert problems == ["values.color.accent2 names no role the schema declares"]
    assert validate_standard({}, {"color.base": "#111111"}) == ["values.color.base names no role the schema declares"]


@pytest.mark.parametrize(
    ("schema", "values", "fragment"),
    [
        ({"role": {}}, {}, "schema has unknown keys ['role']"),
        ({"roles": ["color.base"]}, {}, "schema.roles must be an object"),
        ({"roles": {"Color Base": {}}}, {}, "'Color Base' is not a name"),
        ({"roles": {"color.base": "#111111"}}, {}, "schema.roles.color.base must be an object"),
        ({"type_scale": []}, {}, "schema.type_scale must be an object"),
        ({"type_scale": {"body": 14}}, {}, "schema.type_scale.body must be an object"),
        ({"rules": {}}, {}, "schema.rules must be a list"),
        ({"rules": ["one accent"]}, {}, "schema.rules[0] must be an object"),
        ({"rules": [{"text": "x"}]}, {}, "schema.rules[0].id must be a name"),
        ({"rules": [{"id": "a", "text": " "}]}, {}, "schema.rules[0].text must be a sentence"),
        ({"rules": [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]}, {}, "schema.rules[1].id 'a' is used twice"),
        ({"roles": {"color.base": {}}}, {"color.base": None}, "values.color.base is null"),
    ],
)
def test_a_malformed_standard_names_its_problem(schema: Any, values: Any, fragment: str) -> None:
    problems = validate_standard(schema, values)
    assert any(fragment in p for p in problems), problems


def test_sizes_and_references_are_bounded() -> None:
    big = {"roles": {f"r{i}": {"description": "x" * 400} for i in range(100)}}
    assert len(json.dumps(big)) > MAX_DOCUMENT_CHARS, "the fixture is not big enough to test the cap"
    assert any("schema is longer" in p for p in validate_standard(big, {}))
    assert any("prose is longer" in p for p in validate_standard({}, {}, "x" * (MAX_DOCUMENT_CHARS + 1)))
    assert any("more than 50" in p for p in validate_standard({}, {}, "", ["acme/site"] * 51))
    assert any("references[0]" in p for p in validate_standard({}, {}, "", [""]))
    assert any("references must be a list" in p for p in validate_standard({}, {}, "", "acme/site"))


# ---- the audit task ----


def test_the_audit_task_carries_the_whole_active_standard_and_the_contract() -> None:
    exceptions = [{"rule": "one-accent", "reason": "the charts need a second hue", "paths": ["src/charts"]}]
    task = audit_task(_standard(), "acme/widgets", exceptions)
    assert task.splitlines()[0] == "Design audit of acme/widgets against the acme design standard, version 3"
    assert json.dumps(SCHEMA, indent=2, sort_keys=True) in task
    assert json.dumps(VALUES, indent=2, sort_keys=True) in task
    assert "Dense tables, one accent." in task and "- acme/site" in task
    assert "`Score: N`" in task and "## Findings" in task
    assert "Read only: change no file, open no pull request, push nothing." in task
    assert "- `one-accent` (in src/charts): the charts need a second hue" in task


def test_a_task_without_exceptions_says_so() -> None:
    task = audit_task(_standard(prose="", references=[]), "acme/widgets", [])
    assert "## Exceptions this repository holds\n\nNone." in task
    assert "### Prose" not in task and "### References" not in task


# ---- reading the report back ----


def test_a_scored_report_fixture_parses() -> None:
    report = (FIXTURES / "scored-report.md").read_text()
    result = parse_audit_report(report)
    assert (result.score, result.findings_open) == (72, 3), (
        "the findings table is the one under ## Findings, not the measurements table"
    )


def test_a_clean_report_fixture_has_no_findings() -> None:
    result = parse_audit_report((FIXTURES / "clean-report.md").read_text())
    assert (result.score, result.findings_open) == (100, 0), (
        "the notes table after the findings heading is not findings"
    )


def test_an_unscored_report_fixture_has_no_score_and_no_findings() -> None:
    result = parse_audit_report((FIXTURES / "unscored-report.md").read_text())
    assert (result.score, result.findings_open) == (None, 0), "a row of dashes is not a finding"


@pytest.mark.parametrize(
    ("report", "score"),
    [
        ("Score: 0", 0),
        ("score: 100", 100),
        ("## Score: 64", 64),
        ("**Score: 88**", 88),
        ("Score : 41 / 100", 41),
        ("Score: 7.", 7),
        ("**Score** - 72/100, commit abc1234, standard: given", 72),
        ("Score: 72/100 (commit abc1234)", 72),
        ("Score 9", 9),
        ("Score: 1000", None),
        ("Scoreboard: 5", None),
        ("Intro\n\nScore: 55\n\nScore: 10", 55),
        ("Score: 101", None),
        ("Score: high", None),
        ("The score: 80 is good", None),
        ("", None),
    ],
)
def test_parse_score(report: str, score: int | None) -> None:
    assert parse_score(report) == score


def test_without_a_findings_heading_the_first_table_with_a_severity_column_counts() -> None:
    report = "Score: 50\n\n| Area | Count |\n|---|---|\n| a | 1 |\n\n| Severity | Location |\n|:--|--:|\n| high | a.css:1 |\n| low | b.css:2 |\n"
    assert count_findings(report) == 2
    assert count_findings("Score: 50\n\n| Area | Count |\n|---|---|\n| a | 1 |\n") == 0


def test_a_findings_heading_with_no_table_is_zero_even_with_a_table_elsewhere() -> None:
    report = "## Findings\n\nNo findings.\n\n## Appendix\n\n| Severity | Location |\n|---|---|\n| high | a.css:1 |\n"
    assert count_findings(report) == 0


def test_the_report_comes_from_the_report_deliverable_first() -> None:
    assert audit_report_text({"deliverables": [{"type": "artifact"}, {"type": "report", "content": "Score: 9"}]}) == (
        "Score: 9"
    )
    assert audit_report_text({"deliverables": [], "result_summary": "Score: 8"}) == "Score: 8"
    assert audit_report_text({}) == ""


class _Store:
    def __init__(self, state: RepoStandardState | None) -> None:
        self.state = state
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def record_audit(
        self, job_id: str, *, score: int | None, findings_open: int, finished_at: datetime
    ) -> RepoStandardState | None:
        self.calls.append((job_id, {"score": score, "findings_open": findings_open, "finished_at": finished_at}))
        return self.state


async def test_record_audit_completion_writes_the_parsed_report() -> None:
    now = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    store = _Store(RepoStandardState(repo="acme/widgets", owner="acme", kind="design", mode="advise"))
    report = (FIXTURES / "scored-report.md").read_text()
    state = await record_audit_completion(
        store, "job-0123456789ab", {"deliverables": [{"type": "report", "content": report}]}, now=now
    )
    assert state is store.state
    assert store.calls == [("job-0123456789ab", {"score": 72, "findings_open": 3, "finished_at": now})]


async def test_record_audit_completion_for_any_other_job_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    warned: list[str] = []
    monkeypatch.setattr(standards.logger, "warning", lambda event, **_: warned.append(event))
    assert await record_audit_completion(_Store(None), "job-x", {"result_summary": "no score"}) is None
    assert warned == [], "a job that is not an audit must not warn about a missing score"
