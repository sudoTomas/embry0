"""A job's status in plain words, and what a person can do next (spec 2026-10-26, job briefs).

Worked out from what the job row and its latest pause already say, with no
model, so every surface (the job page, the world, an assistant) says the same
thing: ``status``, ``current_stage``, the latest ``interrupt`` event's reason
and summary (its options, and ``budget`` when a pause is over the job's
budget), ``account_wait``, ``error_code`` and ``error_message``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: A running job's stage, as what is happening.
_STAGE_WORDS: dict[str, str] = {
    "init": "Setting up: the repository is being cloned into its sandbox.",
    "triage": "Planning: the task is being read and the way to run it chosen.",
    "plan_route": "Planning: the task is being read and the way to run it chosen.",
    "developer": "Working: the developer is writing the change.",
    "developer_retry": "Working again: the developer is addressing the review.",
    "review": "Checking: the reviewer is reviewing the change.",
    "qa": "Testing: QA is trying the change.",
    "orchestrate_qa": "Testing: QA is trying the change.",
    "analysis": "Analysing: the evidence is being read.",
    "output": "Finishing: the result is being delivered.",
    "finalize_output": "Finishing: the result is being delivered.",
    "publish": "Finishing: the result is being delivered.",
}

#: Why a job paused at max retries, by the stage it arrived from.
_PAUSE_WORDS: dict[str, str] = {
    "dev_run_unfinished": "the developer ran out of turns; its work is saved on its branch.",
    "dev_branch_missing": "the developer stopped without pushing its branch, so its work was not saved.",
    "review_errored": "the review failed before it gave a verdict.",
    "review_unparseable": "the review gave no verdict embry0 can act on.",
    "review_toolchain_missing": "the sandbox has no compiler or test runner, so the review could not run the tests.",
    "review_retries_exhausted": "the reviewer still asked for changes after every retry.",
}

_OPTION_WORDS = {"continue_retrying": "continue", "merge_as_is": "merge it as it is", "abandon": "stop"}

_PROVIDERS = {"anthropic": "Claude", "xai": "Grok", "openrouter": "OpenRouter", "custom": "local model"}


def _money(value: Any) -> str:
    amount = float(value or 0)
    return f"${amount:,.0f}" if amount >= 10 else f"${amount:,.2f}"


def _sentence(text: str, limit: int = 160) -> str:
    text = " ".join(str(text or "").split())
    first = text.split(". ", 1)[0].rstrip(".")
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def _lower_first(text: str) -> str:
    """ "Review failed" reads "review failed" after a colon; "QA failed" stays."""
    return text[0].lower() + text[1:] if len(text) > 1 and text[0].isupper() and text[1].islower() else text


def _choices(options: Sequence[str], budget: Mapping[str, Any] | None) -> str | None:
    words = []
    for option in options:
        word = _OPTION_WORDS.get(option)
        if word is None:
            continue
        if option == "continue_retrying" and budget and budget.get("continue_adds"):
            word = f"continue (+{_money(budget['continue_adds'])})"
        words.append(word)
    if not words:
        return None
    phrase = words[0] if len(words) == 1 else ", ".join(words[:-1]) + f" or {words[-1]}"
    return phrase[0].upper() + phrase[1:] + "."


def _paused(job: Mapping[str, Any], interrupt: Mapping[str, Any] | None) -> dict[str, Any]:
    wait = job.get("account_wait")
    if isinstance(wait, Mapping) and wait.get("until"):
        if wait.get("reason") == "local_busy":
            headline = "Waiting: every slot of the local model is taken; it tries again in a few minutes."
        else:
            provider = _PROVIDERS.get(str(wait.get("provider") or ""), str(wait.get("provider") or "LLM"))
            why = str(wait.get("reason") or "paused").replace("_", " ")
            headline = f"Waiting: the {provider} account is {why}; it goes on by itself when the account is ready."
            if wait.get("chosen"):
                headline += " A person chose this model, so it does not move to another one."
        return {
            "state": "waiting",
            "headline": headline,
            "next": "Try now or cancel.",
            "actions": ["try_now", "cancel"],
            "until": wait.get("until"),
        }
    summary = (interrupt or {}).get("summary") if isinstance((interrupt or {}).get("summary"), Mapping) else {}
    summary = summary or {}
    options = [str(o) for o in summary.get("options") or []]
    budget = summary.get("budget") if isinstance(summary.get("budget"), Mapping) else None
    reason = str(summary.get("reason") or (interrupt or {}).get("reason") or "")
    stage = str(job.get("current_stage") or "")
    if budget:
        headline = f"Paused: it went over its budget, {_money(budget.get('spent'))} of {_money(budget.get('cap'))}."
    elif reason == "agent_questions_exhausted":
        headline = "Paused: the agents asked more questions than a job may."
    elif stage in _PAUSE_WORDS:
        headline = "Paused: " + _PAUSE_WORDS[stage]
    elif summary.get("message"):
        headline = f"Paused: {_lower_first(_sentence(str(summary['message'])))}."
    else:
        headline = "Paused."
    actions = options or ["continue_retrying", "abandon"]
    return {
        "state": "paused",
        "headline": headline,
        "next": _choices(actions, budget) or "Resume or cancel.",
        "actions": actions,
        "until": None,
    }


def _question(interrupt: Mapping[str, Any] | None) -> str | None:
    if not interrupt:
        return None
    summary = interrupt.get("summary") if isinstance(interrupt.get("summary"), Mapping) else {}
    questions = interrupt.get("questions") or (summary or {}).get("questions") or []
    for q in questions if isinstance(questions, list) else []:
        text = q.get("question") or q.get("text") if isinstance(q, Mapping) else q
        if isinstance(text, str) and text.strip():
            return _sentence(text, 200)
    return None


def status_words(
    job: Mapping[str, Any],
    interrupt: Mapping[str, Any] | None = None,
    *,
    failure: str | None = None,
) -> dict[str, Any]:
    """``{state, headline, next, actions, until}`` for ``job``.

    ``interrupt`` is its latest ``interrupt`` event (paused and awaiting-input
    jobs); ``failure`` the plain words for its error, when the caller has them
    (``embry0.services.world.failure_words``).
    """
    status = str(job.get("status") or "")
    delivery = str(job.get("delivery_state") or "none")
    if status == "pending":
        return {
            "state": "queued",
            "headline": "Queued: it starts when a run slot is free.",
            "next": None,
            "actions": ["cancel"],
            "until": None,
        }
    if status == "running":
        stage = str(job.get("current_stage") or "")
        headline = _STAGE_WORDS.get(stage) or (f"Running: {stage.replace('_', ' ')}." if stage else "Running.")
        return {"state": "running", "headline": headline, "next": None, "actions": ["cancel"], "until": None}
    if status == "paused":
        return _paused(job, interrupt)
    if status == "awaiting_input":
        question = _question(interrupt)
        headline = f"Waiting for your answer: {question}" if question else "Waiting for your answer to its questions."
        return {
            "state": "asking",
            "headline": headline,
            "next": "Answer it below.",
            "actions": ["answer"],
            "until": None,
        }
    if status in ("completed", "pr_merged", "partial", "pr_closed"):
        if status == "pr_merged" or delivery == "merged":
            headline = "Done: its pull request was merged."
        elif status == "pr_closed" or delivery == "closed":
            headline = "Done: its pull request was closed without merging."
        elif job.get("pr_url"):
            headline = "Done: a pull request is open for review."
        elif status == "partial":
            headline = "Partly done: some of its checks passed and some did not."
        elif job.get("result_summary"):
            headline = "Done: its report is ready."
        else:
            headline = "Done."
        nxt = "Review the pull request." if job.get("pr_url") and delivery not in ("merged", "closed") else None
        return {"state": "done", "headline": headline, "next": nxt, "actions": [], "until": None}
    if status == "failed":
        why = failure or (_sentence(str(job.get("error_message"))) if job.get("error_message") else None)
        headline = f"Failed: {why}." if why else "Failed."
        return {
            "state": "failed",
            "headline": headline.replace("..", "."),
            "next": "Run it again, or read the log for the cause.",
            "actions": ["run_again"],
            "until": None,
        }
    if status == "cancelled":
        return {"state": "cancelled", "headline": "Cancelled.", "next": None, "actions": [], "until": None}
    if status == "skipped":
        return {
            "state": "done",
            "headline": "Skipped: another open pull request already covers it.",
            "next": None,
            "actions": [],
            "until": None,
        }
    if status == "expired":
        return {
            "state": "failed",
            "headline": "Expired: nobody answered it in time.",
            "next": None,
            "actions": [],
            "until": None,
        }
    return {
        "state": status or "unknown",
        "headline": status.replace("_", " ").capitalize() + "." if status else "Unknown.",
        "next": None,
        "actions": [],
        "until": None,
    }
