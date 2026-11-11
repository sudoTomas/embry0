"""A job's status in plain words (spec 2026-10-26, job briefs): worked out from the row and its latest pause, no model."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.services.job_status_words import status_words


def _max_retries(**summary: Any) -> dict[str, Any]:
    return {"type": "interrupt", "reason": "max_retries", "summary": {"reason": "max_retries", **summary}}


@pytest.mark.parametrize(
    "job, headline",
    [
        ({"status": "pending"}, "Queued: it starts when a run slot is free."),
        ({"status": "running", "current_stage": "developer"}, "Working: the developer is writing the change."),
        ({"status": "running", "current_stage": "review"}, "Checking: the reviewer is reviewing the change."),
        ({"status": "running", "current_stage": "new_stage"}, "Running: new stage."),
        ({"status": "running"}, "Running."),
        ({"status": "cancelled"}, "Cancelled."),
        ({"status": "skipped"}, "Skipped: another open pull request already covers it."),
    ],
)
def test_each_status_reads_as_what_is_happening(job: dict[str, Any], headline: str) -> None:
    assert status_words(job)["headline"] == headline


def test_a_developer_out_of_turns_with_its_work_saved_can_be_continued_or_stopped() -> None:
    words = status_words(
        {"status": "paused", "current_stage": "dev_run_unfinished"},
        _max_retries(message="Developer hit its turn cap", options=["continue_retrying", "merge_as_is", "abandon"]),
    )
    assert words == {
        "state": "paused",
        "headline": "Paused: the developer ran out of turns; its work is saved on its branch.",
        "next": "Continue, merge it as it is or stop.",
        "actions": ["continue_retrying", "merge_as_is", "abandon"],
        "until": None,
    }


def test_a_pause_over_budget_names_the_spend_and_what_continuing_adds() -> None:
    words = status_words(
        {"status": "paused", "current_stage": "dev_run_unfinished"},
        _max_retries(options=["continue_retrying", "abandon"], budget={"spent": 12.4, "cap": 10, "continue_adds": 10}),
    )
    assert words["headline"] == "Paused: it went over its budget, $12 of $10."
    assert words["next"] == "Continue (+$10) or stop."


def test_a_pause_from_an_unknown_stage_says_the_summary_s_first_sentence() -> None:
    words = status_words(
        {"status": "paused", "current_stage": "max_retries"},
        _max_retries(message="Review failed after 3 attempts. More detail here.", options=["abandon"]),
    )
    assert words["headline"] == "Paused: review failed after 3 attempts."
    assert words["next"] == "Stop."
    assert status_words({"status": "paused"})["headline"] == "Paused."


def test_a_spent_account_wait_says_it_goes_on_by_itself() -> None:
    wait = {"provider": "anthropic", "reason": "quota_exhausted", "until": "2026-10-27T00:00:00+00:00", "chosen": True}
    words = status_words({"status": "paused", "account_wait": wait})
    assert words["state"] == "waiting" and words["until"] == "2026-10-27T00:00:00+00:00"
    assert words["headline"].startswith("Waiting: the Claude account is quota exhausted; it goes on by itself")
    assert "A person chose this model" in words["headline"]
    assert words["next"] == "Try now or cancel." and words["actions"] == ["try_now", "cancel"]
    busy = status_words({"status": "paused", "account_wait": {"reason": "local_busy", "until": "x"}})
    assert busy["headline"].startswith("Waiting: every slot of the local model is taken")


def test_a_question_waits_for_an_answer() -> None:
    interrupt = {"type": "interrupt", "questions": [{"question": "Which environment should it deploy to? It matters."}]}
    words = status_words({"status": "awaiting_input"}, interrupt)
    assert words["headline"] == "Waiting for your answer: Which environment should it deploy to? It matters"
    assert words["next"] == "Answer it below."
    assert status_words({"status": "awaiting_input"})["headline"] == "Waiting for your answer to its questions."


@pytest.mark.parametrize(
    "job, headline, nxt",
    [
        (
            {"status": "completed", "pr_url": "u", "delivery_state": "pr_open"},
            "Done: a pull request is open for review.",
            "Review the pull request.",
        ),
        (
            {"status": "pr_merged", "pr_url": "u", "delivery_state": "merged"},
            "Done: its pull request was merged.",
            None,
        ),
        (
            {"status": "completed", "delivery_state": "closed", "pr_url": "u"},
            "Done: its pull request was closed without merging.",
            None,
        ),
        ({"status": "completed", "result_summary": "Found nothing new."}, "Done: its report is ready.", None),
        ({"status": "partial"}, "Partly done: some of its checks passed and some did not.", None),
    ],
)
def test_a_finished_job_says_what_it_left(job: dict[str, Any], headline: str, nxt: str | None) -> None:
    words = status_words(job)
    assert (words["state"], words["headline"], words["next"]) == ("done", headline, nxt)


def test_a_failure_uses_the_caller_s_plain_words_else_its_own_message() -> None:
    words = status_words(
        {"status": "failed", "error_message": "boom. trace"}, failure="the LLM account it ran on refused it"
    )
    assert words["headline"] == "Failed: the LLM account it ran on refused it."
    assert words["actions"] == ["run_again"]
    assert status_words({"status": "failed", "error_message": "boom. trace"})["headline"] == "Failed: boom."
    assert status_words({"status": "failed"})["headline"] == "Failed."


def _stages_the_pause_node_names() -> set[str]:
    """Every stage ``max_retries_node`` compares its arrival stage with (``==``, ``!=``, ``in``, ``not in``)."""
    import ast
    import inspect

    from embry0.workflows.issue_to_pr.nodes import _interrupts

    tree = ast.parse(inspect.getsource(_interrupts.max_retries_node))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "arrival_stage":
            for right in node.comparators:
                values = right.elts if isinstance(right, ast.Tuple | ast.List | ast.Set) else [right]
                names.update(v.value for v in values if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return names


def test_every_stage_the_pause_node_names_has_words() -> None:
    """#436: the paused banner shows these words; a stage the pause node handles with none showed as itself."""
    from embry0.services.job_status_words import PAUSE_WORDS

    named = _stages_the_pause_node_names()
    assert "dev_run_unfinished" in named, f"the pause node's stages were not read, so this measures nothing: {named}"
    missing = sorted(named - PAUSE_WORDS.keys())
    assert missing == [], f"pause stages with no words in job_status_words.PAUSE_WORDS: {missing}"


def test_each_pause_stage_reads_as_a_sentence_not_as_itself() -> None:
    from embry0.services.job_status_words import PAUSE_WORDS

    for stage in sorted(PAUSE_WORDS):
        headline = status_words(
            {"status": "paused", "current_stage": stage}, _max_retries(message=f"Pipeline paused at stage {stage}")
        )["headline"]
        assert stage not in headline and headline.startswith("Paused: ") and headline.endswith("."), (stage, headline)


def test_a_pause_from_a_stage_with_no_words_does_not_show_the_stage() -> None:
    """A stage a later change routes to the pause without words falls to the pause's message, which names it."""
    words = status_words(
        {"status": "paused", "current_stage": "some_new_stage"},
        _max_retries(message="Pipeline paused at stage some_new_stage", options=["abandon"]),
    )
    assert words["headline"] == "Paused.", words["headline"]
