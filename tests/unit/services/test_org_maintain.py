"""What it takes to maintain each repository, from facts by rules (ADR 0015)."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.services.org_maintain import (
    RULES,
    STEP_ORDER,
    Reading,
    current_runs,
    hint_for,
    maintain_hint,
)
from embry0.services.org_needs import ACTIONS, derive_needs

HOOKS_OK = {"hooks": True}

# A repository embry0 maintains fully: every rule that asks for a step is quiet.
MAINTAINED: dict[str, Any] = {
    "root_visible": True,
    "hooks_visible": True,
    "hook_present": True,
    "registered": True,
    "active": True,
    "qa_config": True,
    "watcher_stack": True,
    "deploy_marker": True,
    "boot_hint": "npm run dev",
    "frontend": True,
    "env_keys": ["TOKEN"],
    "env_keys_set": ["TOKEN"],
}

# A repository discovery read and embry0 does not know: visible, hooked elsewhere, nothing to run.
UNKNOWN: dict[str, Any] = {
    "root_visible": True,
    "hooks_visible": True,
    "hook_present": True,
    "registered": False,
    "qa_config": False,
    "watcher_stack": False,
    "boot_hint": None,
    "frontend": False,
    "env_keys": [],
}


def _row(
    repo: str = "acme/web", *, run_id: str | None = "odr-1", archived: bool = False, **facts: Any
) -> dict[str, Any]:
    return {"owner": "acme", "repo": repo, "run_id": run_id, "archived": archived, "facts": facts}


# One fact fixture per rule: (row, access, current runs, the step it asks for, a clause it says).
CASES: dict[str, tuple[dict[str, Any], dict[str, Any], frozenset[str] | None, str | None, str]] = {
    "archived": (_row(archived=True, skipped="archived"), HOOKS_OK, None, None, "Archived"),
    "not_listed": (
        _row(run_id="odr-1", **MAINTAINED),
        HOOKS_OK,
        frozenset({"odr-2"}),
        None,
        "Not listed by the latest discovery run",
    ),
    "delivered_unregistered": (
        _row(run_id=None, registered=False, webhook_events=2),
        HOOKS_OK,
        frozenset({"odr-1"}),
        "register",
        "known only from its webhook deliveries",
    ),
    "delivered_registered": (
        _row(run_id=None, registered=True, active=True, webhook_events=1),
        HOOKS_OK,
        frozenset({"odr-1"}),
        None,
        "Registered from a webhook delivery",
    ),
    "registered_watched": (_row(**MAINTAINED), HOOKS_OK, None, None, "Registered and watched"),
    "registered": (_row(**{**MAINTAINED, "watcher_stack": False}), HOOKS_OK, None, None, "Registered"),
    "not_registered": (_row(**UNKNOWN), HOOKS_OK, None, "register", "Not registered"),
    "qa_configured": (_row(**MAINTAINED), HOOKS_OK, None, None, "QA configured"),
    "onboarding_candidate": (
        _row(**{**UNKNOWN, "boot_hint": "docker compose -f compose.yml up"}),
        HOOKS_OK,
        None,
        "onboard",
        "a service with a compose file",
    ),
    "no_qa_config": (
        _row(**{**MAINTAINED, "qa_config": False, "boot_hint": None, "frontend": False}),
        HOOKS_OK,
        None,
        "setting",
        "no QA config and nothing to boot or serve",
    ),
    "no_webhook": (
        _row(**{**MAINTAINED, "hook_present": False}),
        HOOKS_OK,
        None,
        "hook",
        "no webhook (the token can manage hooks)",
    ),
    "no_webhook_without_hook_admin": (
        _row(**{**MAINTAINED, "hook_present": False}),
        {"hooks": False},
        None,
        "credential",
        "no webhook (the probe found the token cannot manage hooks)",
    ),
    "hook_origin_unknown": (
        _row(**{**MAINTAINED, "hook_present": None}),
        HOOKS_OK,
        None,
        "setting",
        "webhooks to an unknown address (WEBHOOK_PUBLIC_URL is not set)",
    ),
    "not_watched": (_row(**{**MAINTAINED, "watcher_stack": False}), HOOKS_OK, None, "watch", "not watched"),
    "no_log_source": (
        _row(**{**MAINTAINED, "watcher_stack": False, "boot_hint": None}),
        HOOKS_OK,
        None,
        "setting",
        "no log source found",
    ),
    "no_deploy_marker": (
        _row(**{**MAINTAINED, "deploy_marker": False}),
        HOOKS_OK,
        None,
        "setting",
        "no deploy marker",
    ),
    "access_limited": (
        _row(**{**MAINTAINED, "hooks_visible": False, "hook_present": None}),
        HOOKS_OK,
        None,
        "credential",
        "the credential cannot read its hooks",
    ),
    "env_values_needed": (
        _row(**{**MAINTAINED, "env_keys": ["TOKEN", "API_URL", "SECRET"]}),
        HOOKS_OK,
        None,
        "setting",
        "2 environment values unset",
    ),
}


def test_every_rule_has_a_fixture() -> None:
    assert set(CASES) == {rule.name for rule in RULES}
    assert len({rule.name for rule in RULES}) == len(RULES), "rule names are unique"


@pytest.mark.parametrize("name", sorted(CASES))
def test_each_rule_reads_its_fixture(name: str) -> None:
    row, access, current, step, clause = CASES[name]
    hint = maintain_hint(row, access, current=current)
    assert name in hint.rules, f"{name} did not hold on its fixture; held: {hint.rules}"
    assert clause in hint.hint, hint.hint
    if step is None:
        rule = next(r for r in RULES if r.name == name)
        assert rule.step is None
    else:
        assert step in hint.steps, hint.steps
    assert hint.hint.endswith(".") and hint.hint[0].isupper()


def test_every_step_is_an_action_of_the_needs_vocabulary_and_none_is_never_one() -> None:
    assert set(STEP_ORDER) <= set(ACTIONS) and "none" not in STEP_ORDER
    assert all(rule.step is None or rule.step in STEP_ORDER for rule in RULES)
    assert all(bool(rule.step) <= bool(rule.does) for rule in RULES), "a rule with a step says how to take it"


def test_the_three_shapes_read_as_plain_sentences() -> None:
    assert maintain_hint(_row(**MAINTAINED), HOOKS_OK).hint == "Registered and watched; QA configured; nothing to do."

    ui = maintain_hint(_row(**{**UNKNOWN, "frontend": True, "boot_hint": "npm run dev"}), HOOKS_OK)
    assert ui.hint == "Not registered; a UI with a dev script, so register, then onboard to draft QA."
    assert ui.steps == ("register", "onboard")

    hookless = maintain_hint(
        _row(**{**MAINTAINED, "hook_present": False, "watcher_stack": False, "boot_hint": None}), HOOKS_OK
    )
    assert hookless.hint == (
        "Registered; QA configured; no webhook (the token can manage hooks); no log source found, "
        "so add the webhook, then name the logs a watcher stack should read."
    )
    assert hookless.steps == ("hook", "setting")


def test_steps_go_register_then_hook_then_onboard_then_watch() -> None:
    assert STEP_ORDER.index("register") < STEP_ORDER.index("hook") < STEP_ORDER.index("onboard")
    assert STEP_ORDER.index("onboard") < STEP_ORDER.index("watch")

    new = maintain_hint(_row(**{**UNKNOWN, "hook_present": False, "boot_hint": "npm run start"}), HOOKS_OK)
    assert new.steps == ("register", "hook", "onboard")
    assert new.hint.endswith("so register, then add the webhook, then onboard to draft QA.")

    known = maintain_hint(
        _row(**{**MAINTAINED, "hook_present": False, "qa_config": False, "watcher_stack": False}), HOOKS_OK
    )
    assert known.steps == ("hook", "onboard", "watch")


def test_the_order_of_the_rule_table_does_not_decide_the_order_of_steps() -> None:
    reading = Reading(facts={**UNKNOWN, "hook_present": False, "boot_hint": "npm run dev"}, hooks_allowed=True)
    assert hint_for(reading, tuple(reversed(RULES))).steps == hint_for(reading).steps == ("register", "hook", "onboard")


def test_hook_administration_comes_from_the_probe() -> None:
    row = _row(**{**MAINTAINED, "hook_present": False})
    assert maintain_hint(row, {"hooks": True}).steps == ("hook",)
    unprobed = maintain_hint(row, {})
    assert unprobed.steps == ("credential",)
    assert "no webhook (the probe has not checked hook administration)" in unprobed.hint
    assert maintain_hint(row, {"hooks": "yes"}).steps == ("credential",), "only a boolean is an answer"


def test_what_runs_is_named_from_the_boot_hint() -> None:
    def said(**facts: Any) -> str:
        return maintain_hint(_row(**{**UNKNOWN, **facts}), HOOKS_OK).hint

    assert "a service with a start script" in said(boot_hint="npm run start")
    assert "a UI with nothing to start" in said(frontend=True)
    assert "a service with a boot command" in said(boot_hint="make run")


def test_an_unseen_fact_is_neither_yes_nor_no() -> None:
    """Contents and hooks the credential could not read ask for a wider
    credential, and nothing is concluded from the blanks they leave."""
    blind = _row(root_visible=False, hooks_visible=False, hook_present=None, registered=False, boot_hint=None)
    hint = maintain_hint(blind, HOOKS_OK)
    assert hint.steps == ("register", "credential")
    assert "the credential cannot read its contents and hooks" in hint.hint
    assert "webhook" not in hint.hint


def test_final_rules_stop_the_reading() -> None:
    archived = maintain_hint(_row(archived=True, registered=False, hooks_visible=True, hook_present=False), HOOKS_OK)
    assert archived.hint == "Archived; nothing to do." and archived.steps == ()
    stale = maintain_hint(_row(run_id="odr-1", **UNKNOWN), HOOKS_OK, current=frozenset({"odr-2"}))
    assert stale.rules == ("not_listed",) and stale.steps == ()


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([], None),
        ([{"id": "odr-3", "status": "running"}, {"id": "odr-2", "status": "error"}], None),
        ([{"id": "odr-2", "status": "done"}, {"id": "odr-1", "status": "done"}], frozenset({"odr-2"})),
        (
            [
                {"id": "odr-4", "status": "running"},
                {"id": "odr-3", "status": "error"},
                {"id": "odr-2", "status": "done"},
            ],
            frozenset({"odr-4", "odr-3", "odr-2"}),
        ),
    ],
)
def test_current_runs_are_the_newest_finished_one_and_every_later_one(
    runs: list[dict[str, Any]], expected: frozenset[str] | None
) -> None:
    assert current_runs(runs) == expected


def test_a_row_of_a_run_still_going_or_failed_is_current() -> None:
    """A run in progress, or one that failed partway, leaves rows of the last
    finished run and rows of its own; both are what the owner looks like now."""
    current = current_runs([{"id": "odr-3", "status": "running"}, {"id": "odr-2", "status": "done"}])
    for run_id in ("odr-3", "odr-2"):
        assert "not_listed" not in maintain_hint(_row(run_id=run_id, **UNKNOWN), HOOKS_OK, current=current).rules
    assert maintain_hint(_row(run_id="odr-1", **UNKNOWN), HOOKS_OK, current=current).rules == ("not_listed",)


def test_steps_are_the_actions_of_the_needs_that_cover_the_repository() -> None:
    """With hook administration, a repository's steps are exactly the actions
    of the needs the needs list files for it, in step order: the sentence and
    the list read the same facts the same way."""
    rows = [
        _row(f"acme/{name}", **facts)
        for name, facts in [
            ("maintained", MAINTAINED),
            ("unknown", UNKNOWN),
            ("fresh", {**UNKNOWN, "env_keys": ["TOKEN"]}),
            ("ui", {**UNKNOWN, "frontend": True, "hook_present": False}),
            ("api", {**UNKNOWN, "boot_hint": "docker compose -f compose.yml up", "hook_present": None}),
            ("lib", {**MAINTAINED, "qa_config": False, "boot_hint": None, "frontend": False, "watcher_stack": False}),
            ("worker", {**MAINTAINED, "deploy_marker": False, "hooks_visible": False, "hook_present": None}),
            ("svc", {**MAINTAINED, "watcher_stack": False, "env_keys": ["A", "B"], "env_keys_set": []}),
            ("blind", {"root_visible": False, "hooks_visible": False, "hook_present": None, "registered": True}),
        ]
    ]
    rows.append(_row("acme/old", archived=True, skipped="archived"))
    delivered = _row("acme/new", run_id=None, registered=False, webhook_events=1)
    needs = derive_needs("acme", rows, delivered=[delivered["repo"]])
    for row in [*rows, delivered]:
        wanted = {n.action["type"] for n in needs if row["repo"] in n.repos} - {"none"}
        steps = maintain_hint(row, HOOKS_OK).steps
        assert set(steps) == wanted, f"{row['repo']}: steps {steps}, needs ask for {sorted(wanted)}"
        assert list(steps) == sorted(steps, key=STEP_ORDER.index)
