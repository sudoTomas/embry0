"""Done when (spec 2026-10-26): the list's shape, its sources, the developer's contract and the strict report check."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from embry0.services import done_when as dw
from embry0.settings.sections.done_when import DONE_WHEN

#: The headings and retries a job reads with nothing written.
HEADINGS = DONE_WHEN.defaults().ticket_sections
RETRIES = DONE_WHEN.defaults().retries

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)

TRIAGE = {
    "items": [
        {"done": "The boot failure's cause is logged in full", "shown_by": "a test that boots with a bad config"},
        {"done": "The cause is written down", "shown_by": "a section in the pull request description"},
    ],
    "out_of_scope": ["Changing the deploy tooling"],
}


def test_triage_s_list_is_kept_with_ids_and_bounded() -> None:
    parsed = dw.parse_done_when(TRIAGE)
    assert parsed is not None
    assert [i["id"] for i in parsed["items"]] == ["d1", "d2"]
    assert parsed["items"][0]["shown_by"] == "a test that boots with a bad config"
    assert parsed["out_of_scope"] == ["Changing the deploy tooling"]
    assert parsed["source"] == "triage" and parsed["report"] is None
    many = dw.parse_done_when({"items": [{"done": f"item {n}"} for n in range(10)]})
    assert many is not None and len(many["items"]) == dw.MAX_ITEMS
    long = dw.parse_done_when({"items": [{"done": "x" * 1000}]})
    assert long is not None and len(long["items"][0]["done"]) <= dw.ITEM_CHARS


def test_a_list_with_no_usable_item_is_none() -> None:
    assert dw.parse_done_when(None) is None
    assert dw.parse_done_when({"items": [{"done": "  "}, 7]}) is None
    assert dw.parse_done_when("investigate") is None


def test_lines_split_into_condition_and_how_it_is_shown() -> None:
    parsed = dw.parse_done_when(["The page loads — the smoke test passes", "No new warnings"])
    assert parsed is not None
    assert parsed["items"][0] == {"id": "d1", "done": "The page loads", "shown_by": "the smoke test passes"}
    assert parsed["items"][1] == {"id": "d2", "done": "No new warnings", "shown_by": ""}


def test_the_ticket_s_acceptance_criteria_are_the_fallback() -> None:
    task = "Fix the export\n\n## Acceptance criteria\n- [ ] CSV has a header row\n- Totals match the screen\n\n## Notes\n- not this"
    assert dw.resolve(None, task, ticket_sections=HEADINGS) == {
        "items": [
            {"id": "d1", "done": "CSV has a header row", "shown_by": ""},
            {"id": "d2", "done": "Totals match the screen", "shown_by": ""},
        ],
        "out_of_scope": [],
        "source": "ticket",
        "edited_by": None,
        "edited_at": None,
        "report": None,
    }
    assert dw.resolve(TRIAGE, task, ticket_sections=HEADINGS)["source"] == "triage"
    assert dw.resolve(None, "Fix the export", ticket_sections=HEADINGS) is None


def test_a_person_s_edit_drops_the_old_report_and_says_who() -> None:
    list_ = dw.edited({"items": [{"done": "Only this"}]}, by="ops@example.com", now=NOW)
    assert list_ is not None
    assert list_["source"] == "person" and list_["edited_by"] == "ops@example.com"
    assert list_["edited_at"] == NOW.isoformat() and list_["report"] is None


def test_the_contract_names_each_item_the_pacing_and_the_report() -> None:
    text = dw.contract(dw.parse_done_when(TRIAGE), retries=RETRIES)
    assert "[d1] The boot failure's cause is logged in full (shown by: a test that boots with a bad config)" in text
    assert "Out of scope" in text and "Changing the deploy tooling" in text
    assert "by half of it commit" in text and "three quarters" in text
    assert '"done_when"' in text
    assert dw.contract(None, retries=RETRIES) == ""
    assert "Finish d2" in dw.contract(dw.parse_done_when(TRIAGE), retries=RETRIES, followup="Finish d2")
    assert dw.pacing_line(40).startswith("Pacing (done when): by turn 20 commit") and "by turn 30" in dw.pacing_line(40)


def _output(report: object) -> str:
    return "Narration...\n```json\n" + json.dumps({"pr_url": None, "summary": "s", "done_when": report}) + "\n```"


def test_only_a_named_item_reported_met_with_evidence_counts() -> None:
    list_ = dw.parse_done_when(TRIAGE)
    report = dw.check_report(
        list_,
        _output(
            [
                {"id": "d1", "met": True, "evidence": "tests/test_boot.py::test_bad_config passes"},
                {"id": "d2", "met": True, "evidence": ""},
            ]
        ),
        now=NOW,
    )
    assert report["unmet"] == ["d2"] and report["reported"] is True
    assert report["items"][1]["evidence"] == "reported met with no evidence"


def test_no_report_leaves_every_item_unmet() -> None:
    list_ = dw.parse_done_when(TRIAGE)
    report = dw.check_report(list_, 'I pushed it.\n{"pr_url": null, "summary": "done"}')
    assert report["unmet"] == ["d1", "d2"] and report["reported"] is False
    assert report["items"][0]["evidence"] == "not reported"
    followup = dw.followup_text(list_, report)
    assert "no done_when report" in followup and "[d1]" in followup and "[d2]" in followup


def test_the_last_report_wins_and_strings_are_not_true() -> None:
    list_ = dw.parse_done_when(TRIAGE)
    output = _output([{"id": "d1", "met": True, "evidence": "old"}]) + _output(
        [{"id": "d1", "met": "yes", "evidence": "x"}, {"id": "d2", "met": True, "evidence": "PR section 'Cause'"}]
    )
    report = dw.check_report(list_, output)
    assert report["unmet"] == ["d1"]


def test_review_and_qa_read_the_same_list() -> None:
    list_ = dw.parse_done_when(TRIAGE)
    list_["report"] = dw.check_report(list_, _output([{"id": "d1", "met": True, "evidence": "a test"}]))
    block = dw.review_block(list_)
    assert "Done when (the job's finish line" in block and "[d2]" in block
    assert "[d1] met: a test" in block and "[d2] NOT met: not reported" in block
    assert dw.qa_criteria(list_) == [
        "The boot failure's cause is logged in full (shown by: a test that boots with a bad config)",
        "The cause is written down (shown by: a section in the pull request description)",
    ]
    assert dw.unmet_summary(list_, list_["report"]) == "The cause is written down"


# --- #182 review: which ticket section a job's headings read -------------------------------------

_PROSE_THEN_BULLETS = (
    "Fix the export\n\n## Definition of done\nWhen the export works.\n\n"
    "## Acceptance criteria\n- CSV has a header\n- Totals match\n"
)


def _ticket_items(task: str, headings: tuple[str, ...]) -> list[str]:
    listed = dw.from_ticket(task, ticket_sections=headings)
    return [item["done"] for item in (listed or {}).get("items", [])]


def test_the_first_heading_whose_section_lists_bullets_is_the_ticket_s_list() -> None:
    assert _ticket_items(_PROSE_THEN_BULLETS, ("Definition of done", "Acceptance criteria")) == [
        "CSV has a header",
        "Totals match",
    ], "a filled-in section with no bullets must not hide the next heading's list"


def test_a_heading_that_ends_in_a_hash_reads_its_section() -> None:
    task = "Port the tool\n\n## Done in C#\n- builds with dotnet\n"
    assert _ticket_items(task, ("Done in C#",)) == ["builds with dotnet"], (
        "a ticket's trailing # marks are dropped from its heading, so the named heading must drop them too"
    )


def test_a_heading_matches_whole_words_only() -> None:
    assert _ticket_items("Fix\n\n## Approach\n- rewrite everything\n", ("A",)) == [], "A must not read Approach"
    assert _ticket_items("Fix\n\n## Done whenever\n- not this\n", ("Done when",)) == [], "nor Done when Done whenever"
    for heading in (
        "Acceptance criteria",
        "Acceptance criteria (must)",
        "acceptance CRITERIA:",
        "Acceptance criteria ##",
    ):
        task = f"Fix\n\n### {heading}\n- the item\n"
        assert _ticket_items(task, ("Acceptance criteria",)) == ["the item"], f"{heading!r} starts with the words"
