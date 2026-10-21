"""build_readiness: the repo page's checklist, decided by fixed rules over stored facts."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from embry0.services.repo_readiness import ReadinessInputs, build_readiness

REPO = "acme/widgets"
ORDER = ["code_access", "app_values", "testing", "first_task", "work_trigger"]


def _app(name: str, criteria: int) -> dict[str, Any]:
    return {
        "name": name,
        "target": "managed",
        "start": "npm run dev",
        "address": "http://localhost:3000",
        "address_is_local": True,
        "criteria": [f"Check {n}" for n in range(criteria)],
        "guardrails": 0,
        "machine": "qa-external",
        "machine_has_browser": True,
    }


def _check(
    *,
    when: str = "auto",
    apps: list[dict[str, Any]] | None = None,
    problems: list[dict[str, str]] | None = None,
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A free-check answer (check_qa_config) for a stored setup."""
    return {
        "ok": not problems,
        "problems": problems or [],
        "warnings": warnings or [],
        "summary": {"when": when, "when_plain": "", "apps": [_app("web", 2)] if apps is None else apps},
    }


TESTED = _check(apps=[_app("web", 2), _app("api", 1)])
OFF = _check(when="never", apps=[])
NO_BROWSER = _check(
    problems=[
        {
            "where": "app web",
            "plain": "The work machine 'slim' has no browser, so every test run would stop.",
            "fix": "Use 'qa-external'.",
        }
    ]
)
UNREADABLE = {
    "ok": False,
    "problems": [{"where": "line 3", "plain": "Line 3 starts with a tab. Indent with spaces.", "fix": ""}],
    "warnings": [],
    "summary": None,
}
FACTS = {"root_visible": True, "env_keys": ["DATABASE_URL", "API_TOKEN"], "hooks_visible": True, "hook_present": True}
PR = "https://github.com/acme/widgets/pull/7"


def _proposal(status: str, *, job_id: str | None = "job-0123456789ab", failure: str = "") -> dict[str, Any]:
    return {"id": "prop-0123456789ab", "status": status, "job_id": job_id, "failure": failure}


BASE = ReadinessInputs(repo=REPO, forge="github", forge_account="acme-bot")


def _ready(**changes: Any) -> dict[str, Any]:
    return build_readiness(replace(BASE, **changes))


def _row(result: dict[str, Any], key: str) -> dict[str, Any]:
    return next(r for r in result["rows"] if r["key"] == key)


# -- every state, and the next step ---------------------------------------------


@pytest.mark.parametrize(
    ("changes", "states", "next_step", "status", "steps_left"),
    [
        pytest.param({}, ["done", "unknown", "missing", "waiting", "unknown"], "testing", "steps_left", 2, id="new"),
        pytest.param(
            {"forge_account": None},
            ["needs_admin", "unknown", "missing", "waiting", "unknown"],
            "code_access",
            "needs_admin",
            3,
            id="no-code-account",
        ),
        pytest.param(
            {"facts": FACTS, "env_keys": frozenset({"API_TOKEN"})},
            ["done", "missing", "missing", "waiting", "done"],
            "app_values",
            "steps_left",
            3,
            id="values-missing",
        ),
        pytest.param(
            {"facts": FACTS, "env_keys": frozenset({"API_TOKEN", "DATABASE_URL"})},
            ["done", "done", "missing", "waiting", "done"],
            "testing",
            "steps_left",
            2,
            id="values-done",
        ),
        pytest.param(
            {"facts": FACTS, "proposal": _proposal("running")},
            ["done", "missing", "in_progress", "waiting", "done"],
            None,
            "steps_left",
            3,
            id="setting-up",
        ),
        pytest.param(
            {"proposal": _proposal("pending")},
            ["done", "unknown", "review", "waiting", "unknown"],
            "testing",
            "setup_to_review",
            2,
            id="to-review",
        ),
        pytest.param(
            {"proposal": _proposal("failed", failure="no valid config after 3 analysis rounds")},
            ["done", "unknown", "failed", "waiting", "unknown"],
            "testing",
            "steps_left",
            2,
            id="setup-run-failed",
        ),
        pytest.param(
            {"setup_check": OFF},
            ["done", "unknown", "off", "missing", "unknown"],
            "first_task",
            "steps_left",
            1,
            id="testing-off",
        ),
        pytest.param(
            {"setup_check": NO_BROWSER},
            ["done", "unknown", "needs_fix", "waiting", "unknown"],
            "testing",
            "steps_left",
            2,
            id="needs-fix",
        ),
        pytest.param(
            {"setup_check": TESTED},
            ["done", "unknown", "done", "missing", "unknown"],
            "first_task",
            "steps_left",
            1,
            id="tested",
        ),
        pytest.param(
            {"setup_check": TESTED, "pr_url": PR, "facts": {**FACTS, "hook_present": False}},
            ["done", "missing", "done", "done", "missing"],
            "app_values",
            "ready",
            1,
            id="hook-missing",
        ),
        pytest.param(
            {"setup_check": TESTED, "pr_url": PR, "facts": FACTS, "env_keys": frozenset(FACTS["env_keys"])},
            ["done", "done", "done", "done", "done"],
            None,
            "ready",
            0,
            id="ready",
        ),
    ],
)
def test_every_row_state_and_the_next_step(changes, states, next_step, status, steps_left) -> None:
    result = _ready(**changes)
    assert [r["key"] for r in result["rows"]] == ORDER
    assert [r["state"] for r in result["rows"]] == states
    assert result["next_step"] == next_step
    assert result["status"] == status
    assert result["steps_left"] == steps_left


def test_the_next_step_is_the_first_required_row_a_person_can_act_on() -> None:
    """Whatever the mix of states, next_step names the first required row in
    order that is missing, needs an admin, needs a fix, failed or is up for
    review; the optional row never is the next step."""
    actionable = {"missing", "needs_admin", "needs_fix", "failed", "review"}
    for facts in (None, FACTS, {**FACTS, "hook_present": False}):
        for account in ("acme-bot", None):
            for proposal in (None, _proposal("pending"), _proposal("failed")):
                for check in (None, TESTED, OFF, NO_BROWSER):
                    for pr_url in (None, PR):
                        result = _ready(
                            forge_account=account, facts=facts, proposal=proposal, setup_check=check, pr_url=pr_url
                        )
                        first = next(
                            (r["key"] for r in result["rows"] if r["required"] and r["state"] in actionable), None
                        )
                        assert result["next_step"] == first


def test_the_response_has_the_shape_the_repo_page_reads() -> None:
    result = _ready(facts=FACTS, facts_seen_at="2026-10-01T12:00:00+00:00")
    assert set(result) == {
        "repo",
        "status",
        "steps_left",
        "summary",
        "next_step",
        "rows",
        "needs_docker",
        "facts_seen_at",
    }
    assert result["repo"] == REPO
    assert result["facts_seen_at"] == "2026-10-01T12:00:00+00:00"
    for row in result["rows"]:
        assert {"key", "required", "state", "title", "says", "action"} <= set(row)
    assert [r["required"] for r in result["rows"]] == [True, True, True, True, False]
    assert [r["title"] for r in result["rows"]] == [
        "Read your code and open pull requests",
        "Settings your app needs",
        "Test your app before showing you a change",
        "Finish a first task",
        "Start work from an issue",
    ]


# -- the done-when cases ------------------------------------------------------------


def test_an_unconfigured_repo_with_a_code_account_starts_with_testing() -> None:
    result = _ready()
    assert result["next_step"] == "testing"
    assert _row(result, "testing")["action"] == {"kind": "start_setup", "label": "Let embry0 set up testing"}


def test_no_fact_row_is_unknown_and_does_not_count_as_a_step() -> None:
    result = _ready(facts=None)
    assert _row(result, "app_values")["state"] == "unknown"
    assert _row(result, "work_trigger")["state"] == "unknown"
    # testing and the first task only: the unknown rows are not steps.
    assert result["steps_left"] == 2
    assert result["facts_seen_at"] is None


def test_a_pending_proposal_is_a_setup_to_review() -> None:
    assert _ready(proposal=_proposal("pending"))["status"] == "setup_to_review"
    # Even over a stored setup: the person still has to decide on it.
    assert _ready(proposal=_proposal("pending"), setup_check=TESTED)["status"] == "setup_to_review"


def test_an_admin_step_outranks_a_setup_to_review() -> None:
    assert _ready(forge_account=None, proposal=_proposal("pending"))["status"] == "needs_admin"


def test_nothing_is_the_next_step_while_embry0_sets_up_testing() -> None:
    result = _ready(forge_account=None, proposal=_proposal("running"))
    assert _row(result, "code_access")["state"] == "needs_admin"
    assert result["next_step"] is None


# -- code access -------------------------------------------------------------------


def test_code_access_names_the_account_that_resolves() -> None:
    row = _row(_ready(), "code_access")
    assert row["says"] == "Uses the GitHub account acme-bot."
    assert row["action"] is None
    assert _row(_ready(forge="gitlab", forge_account="acme-gl"), "code_access")["says"] == (
        "Uses the GitLab account acme-gl."
    )


def test_no_code_account_is_an_admin_step_with_a_request_to_copy() -> None:
    row = _row(_ready(forge="gitlab", forge_account=None), "code_access")
    assert row["state"] == "needs_admin"
    assert row["says"] == "An admin has to connect a GitLab account that can see this repository."
    assert row["action"] == {"kind": "copy_admin_request", "label": "Copy a request for your admin"}


# -- settings your app needs -------------------------------------------------------


def test_settings_with_no_value_are_named_five_at_most() -> None:
    declared = ["A", "B", "C", "D", "E", "F", "G"]
    row = _row(_ready(facts={"env_keys": declared}, env_keys=frozenset({"B"})), "app_values")
    assert row["state"] == "missing"
    assert row["says"] == "Your app lists 7 settings. 6 have no value yet: A, C, D, E, F, and 1 more."
    assert row["action"] == {"kind": "open_environment", "label": "Fill in 6 values"}
    assert row["missing_keys"] == ["A", "C", "D", "E", "F", "G"]


def test_one_setting_with_no_value_reads_in_the_singular() -> None:
    row = _row(_ready(facts={"env_keys": ["API_TOKEN"]}), "app_values")
    assert row["says"] == "Your app lists 1 setting. 1 has no value yet: API_TOKEN."
    assert row["action"] == {"kind": "open_environment", "label": "Fill in 1 value"}


def test_every_setting_with_a_value_is_done() -> None:
    row = _row(_ready(facts=FACTS, env_keys=frozenset({"API_TOKEN", "DATABASE_URL", "OTHER"})), "app_values")
    assert row["state"] == "done"
    assert row["says"] == "Every setting your app lists has a value."
    assert row["action"] is None
    assert "missing_keys" not in row


@pytest.mark.parametrize(
    "facts",
    [
        pytest.param(None, id="no-fact-row"),
        pytest.param({"registered": True, "webhook_events": 2}, id="only-webhook-deliveries"),
        pytest.param({"skipped": "archived"}, id="archived"),
        pytest.param({"env_keys": "DATABASE_URL"}, id="not-a-list"),
        pytest.param({"root_visible": False, "env_keys": []}, id="code-not-readable"),
    ],
)
def test_settings_embry0_has_not_read_are_unknown(facts) -> None:
    row = _row(_ready(facts=facts), "app_values")
    assert row["state"] == "unknown"
    assert row["says"] == "embry0 has not read this repository's example settings file yet."
    assert row["action"] == {"kind": "open_environment", "label": "Check for settings"}


# -- testing -----------------------------------------------------------------------


def test_a_proposal_waiting_for_review() -> None:
    row = _row(_ready(proposal=_proposal("pending")), "testing")
    assert row["state"] == "review"
    assert row["says"] == "embry0 has a test setup for you to review."
    assert row["action"] == {"kind": "review_proposal", "label": "Review the setup"}
    assert row["proposal_id"] == "prop-0123456789ab"


def test_a_setup_run_in_progress_links_its_job() -> None:
    row = _row(_ready(proposal=_proposal("running")), "testing")
    assert row["state"] == "in_progress"
    assert row["says"] == "embry0 is reading your code and trying to start your app. You can leave this page."
    assert row["action"] == {"kind": "view_job", "label": "See progress"}
    assert row["job_id"] == "job-0123456789ab"
    assert row["proposal_id"] == "prop-0123456789ab"


def test_a_setup_run_with_no_job_yet_has_nothing_to_open() -> None:
    row = _row(_ready(proposal=_proposal("running", job_id=None)), "testing")
    assert row["state"] == "in_progress"
    assert row["action"] is None
    assert "job_id" not in row


def test_a_failed_setup_run_says_why_in_its_first_line() -> None:
    failure = "embry0 stopped before it finished: the orchestrator restarted\nTraceback (most recent call last):"
    row = _row(_ready(proposal=_proposal("failed", failure=failure)), "testing")
    assert row["state"] == "failed"
    assert (
        row["says"] == "embry0 could not set up testing: embry0 stopped before it finished: the orchestrator restarted"
    )
    assert row["action"] == {"kind": "start_setup", "label": "Try again"}


def test_a_failed_setup_run_with_no_reason() -> None:
    row = _row(_ready(proposal=_proposal("failed", failure="")), "testing")
    assert row["says"] == "embry0 could not set up testing."


def test_a_failed_run_does_not_hide_a_stored_setup() -> None:
    row = _row(_ready(proposal=_proposal("failed", failure="boom"), setup_check=TESTED), "testing")
    assert row["state"] == "done"


@pytest.mark.parametrize("status", [None, "applied", "rejected", "stale"])
def test_no_setup_is_missing(status) -> None:
    row = _row(_ready(proposal=_proposal(status) if status else None), "testing")
    assert row["state"] == "missing"
    assert row["says"] == (
        "Testing is not set up yet. embry0 can still write code, but it cannot check that your app still works."
    )
    assert row["action"] == {"kind": "start_setup", "label": "Let embry0 set up testing"}


def test_testing_off() -> None:
    row = _row(_ready(setup_check=OFF), "testing")
    assert row["state"] == "off"
    assert row["says"] == "Testing is off for this repository. embry0 opens pull requests without testing the app."
    assert row["action"] == {"kind": "start_setup", "label": "Let embry0 set up testing"}


@pytest.mark.parametrize("check", [NO_BROWSER, UNREADABLE], ids=["no-browser", "unreadable"])
def test_a_setup_every_run_would_stop_on_needs_a_fix(check) -> None:
    row = _row(_ready(setup_check=check), "testing")
    assert row["state"] == "needs_fix"
    assert row["says"] == check["problems"][0]["plain"]
    assert row["action"] == {"kind": "fix_setup", "label": "Let embry0 fix the setup"}


def test_a_setup_that_names_no_apps_needs_a_fix() -> None:
    row = _row(_ready(setup_check=_check(apps=[])), "testing")
    assert row["state"] == "needs_fix"
    assert row["says"] == "The test setup names no apps, so nothing is tested after a change."
    assert row["action"] == {"kind": "fix_setup", "label": "Let embry0 fix the setup"}


def test_a_working_setup_says_what_it_tests() -> None:
    assert _row(_ready(setup_check=TESTED), "testing")["says"] == (
        "Tests web and api and checks 3 things after each change."
    )
    one = _check(apps=[_app("web", 1)])
    assert _row(_ready(setup_check=one), "testing")["says"] == "Tests web and checks 1 thing after each change."
    three = _check(apps=[_app("web", 1), _app("api", 0), _app("admin", 2)])
    assert _row(_ready(setup_check=three), "testing")["says"] == (
        "Tests web, api and admin and checks 3 things after each change."
    )
    assert _row(_ready(setup_check=TESTED), "testing")["action"] is None


# -- first task --------------------------------------------------------------------


def test_the_first_task_waits_for_testing() -> None:
    row = _row(_ready(), "first_task")
    assert row["state"] == "waiting"
    assert row["says"] == "Comes after testing is set up or skipped."
    assert row["action"] is None


@pytest.mark.parametrize("check", [TESTED, OFF], ids=["tested", "off"])
def test_the_first_task_is_next_once_testing_is_settled(check) -> None:
    row = _row(_ready(setup_check=check), "first_task")
    assert row["state"] == "missing"
    assert row["says"] == "Give embry0 something small and low-risk. It opens a pull request for you to review."
    assert row["action"] == {"kind": "create_issue", "label": "Give embry0 a first task"}


def test_a_pull_request_finishes_the_first_task() -> None:
    row = _row(_ready(setup_check=TESTED, pr_url=PR), "first_task")
    assert row["state"] == "done"
    assert row["says"] == "First pull request opened."
    assert row["pr_url"] == PR
    assert row["action"] is None


def test_a_pull_request_already_opened_is_not_hidden_behind_testing() -> None:
    assert _row(_ready(pr_url=PR), "first_task")["state"] == "done"


# -- work from an issue -------------------------------------------------------------


def test_work_from_an_issue_when_the_forge_tells_embry0() -> None:
    row = _row(_ready(facts=FACTS, trigger_label="ready-for-ai"), "work_trigger")
    assert row["state"] == "done"
    assert row["required"] is False
    assert row["says"] == (
        "Put the label ready-for-ai on an issue here to start work. "
        "GitHub tells embry0 about new issues in this repository."
    )
    assert row["trigger_label"] == "ready-for-ai"
    assert row["action"] is None


def test_work_from_an_issue_without_the_connection() -> None:
    row = _row(_ready(facts={**FACTS, "hook_present": False}), "work_trigger")
    assert row["state"] == "missing"
    assert row["says"] == (
        "GitHub does not tell embry0 about new issues here yet, so the label embry0 does nothing. "
        "An admin can add the connection from Organizations."
    )
    assert row["action"] == {"kind": "copy_admin_request", "label": "Copy a request for your admin"}
    assert row["trigger_label"] == "embry0"


@pytest.mark.parametrize(
    "facts",
    [None, {"hooks_visible": False, "hook_present": None}, {"hooks_visible": True, "hook_present": None}],
    ids=["no-fact-row", "hooks-not-visible", "no-public-url"],
)
def test_work_from_an_issue_when_embry0_cannot_tell(facts) -> None:
    row = _row(_ready(forge="gitlab", facts=facts), "work_trigger")
    assert row["state"] == "unknown"
    assert row["says"] == (
        "Put the label embry0 on an issue here to start work. "
        "embry0 cannot tell whether GitLab tells it about new issues in this repository."
    )
    assert row["action"] is None


# -- summary and Docker ------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "summary"),
    [
        (
            {"forge_account": None},
            "embry0 cannot read this code or open pull requests until an admin connects a GitHub account.",
        ),
        ({}, "embry0 can read this code and open pull requests. It cannot test your app yet."),
        (
            {"proposal": _proposal("failed")},
            "embry0 can read this code and open pull requests. It cannot test your app yet.",
        ),
        (
            {"proposal": _proposal("running")},
            "embry0 can read this code and open pull requests. It is setting up testing now.",
        ),
        (
            {"proposal": _proposal("pending")},
            "embry0 can read this code and open pull requests. A test setup is waiting for your review.",
        ),
        (
            {"setup_check": NO_BROWSER},
            "embry0 can read this code and open pull requests. Its test setup has a problem, so it cannot test your app yet.",
        ),
        ({"setup_check": OFF}, "embry0 can read this code and open pull requests. Testing is off."),
        ({"setup_check": TESTED}, "embry0 can read this code, open pull requests and test your app."),
    ],
)
def test_the_summary_is_one_plain_sentence(changes, summary) -> None:
    assert _ready(**changes)["summary"] == summary


def test_docker_is_needed_when_the_check_says_testing_will_not_start_without_it() -> None:
    docker = {
        "code": "needs_docker",
        "plain": "Testing will not start until an admin grants Docker access.",
        "confirm": True,
    }
    assert _ready(setup_check=_check(warnings=[docker]))["needs_docker"] is True
    assert _ready(setup_check=TESTED)["needs_docker"] is False
    assert _ready()["needs_docker"] is False
