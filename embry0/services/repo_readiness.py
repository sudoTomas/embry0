"""Getting a repository ready: the checklist the repo page opens on.

``build_readiness`` decides every row with fixed rules over facts embry0
already stores: the forge account that resolves for the repository, what
discovery read of it (org_repo_facts), the settings stored for it and
globally, its test setup and the free check of it, the latest setup
proposal, and the newest job that opened a pull request. No model is called
and no forge is asked. A fact embry0 does not have reads ``unknown``, never
a guess, and an unknown row is not counted as a step left.

``readiness_inputs`` gathers those facts; ``build_readiness`` is pure, so
the same facts always give the same checklist.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from embry0.services.credentials.resolver import CredentialMissingError, CredentialUnavailableError
from embry0.services.qa_config_check import check_qa_config
from embry0.services.repo_setup import settle

FORGE_NAMES: Final = {"github": "GitHub", "gitlab": "GitLab"}

# A required row in one of these states is something a person can do now.
_ACTIONABLE: Final = frozenset({"missing", "needs_admin", "needs_fix", "failed", "review"})
# The settings row names at most this many settings with no value.
_KEYS_SHOWN: Final = 5
_ADMIN_REQUEST: Final = ("copy_admin_request", "Copy a request for your admin")
_START_SETUP: Final = ("start_setup", "Let embry0 set up testing")


@dataclass(frozen=True)
class ReadinessInputs:
    """The stored facts one repository's checklist is built from.

    ``forge_account`` is the label of the forge account that resolves, None
    when none does. ``facts`` is the discovery fact row's ``facts``, None
    when there is no row. ``env_keys`` are the setting names stored for the
    repository and globally. ``setup_check`` is the free check of the stored
    test setup (``check_qa_config``), None when none is stored. ``proposal``
    is the latest setup proposal, a ``running`` one whose job ended already
    finished as failed. ``pr_url`` is the newest pull request a job opened.
    """

    repo: str
    forge: str
    forge_account: str | None = None
    facts: Mapping[str, Any] | None = None
    facts_seen_at: str | None = None
    env_keys: frozenset[str] = frozenset()
    setup_check: Mapping[str, Any] | None = None
    proposal: Mapping[str, Any] | None = None
    pr_url: str | None = None
    trigger_label: str = "embry0"


async def readiness_inputs(
    app_state: Any, repo: str, registered: Mapping[str, Any], *, trigger_label: str
) -> ReadinessInputs:
    """Gather ``repo``'s facts from embry0's own tables. ``registered`` is its
    repos row. Reads only: the forge account resolves from the registry and
    the server's environment, and nothing asks the forge."""
    forge = str(registered["forge"])
    try:
        account: str | None = str((await app_state.credential_resolver.resolve_forge(repo.lower(), forge)).label)
    except (CredentialMissingError, CredentialUnavailableError):
        account = None
    fact = await app_state.org_repos_repo.get(repo)
    env_keys = {str(v["key"]) for v in await app_state.env_repo.get_global()}
    env_keys |= {str(v["key"]) for v in await app_state.env_repo.get_repo(repo)}
    stored = await app_state.qa_configs_repo.get(repo)
    setup_check = (
        await check_qa_config(
            stored["yaml_text"],
            profiles_repo=app_state.profiles_repo,
            env_keys=env_keys,
            docker_access=bool(registered.get("docker_access")),
        )
        if stored is not None
        else None
    )
    latest = await app_state.repo_setup_proposals_repo.latest(repo)
    proposal = (await settle(app_state, latest))[0] if latest is not None else None
    pr_url = await app_state.db.fetchval(
        "SELECT pr_url FROM jobs WHERE repo = $1 AND pr_url IS NOT NULL ORDER BY created_at DESC LIMIT 1", repo
    )
    return ReadinessInputs(
        repo=repo,
        forge=forge,
        forge_account=account,
        facts=fact["facts"] if fact is not None else None,
        facts_seen_at=fact["seen_at"] if fact is not None else None,
        env_keys=frozenset(env_keys),
        setup_check=setup_check,
        proposal=proposal,
        pr_url=str(pr_url) if pr_url else None,
        trigger_label=trigger_label,
    )


def build_readiness(inputs: ReadinessInputs) -> dict[str, Any]:
    """The checklist for one repository: its rows in order, the next step, a
    status and one summary sentence. Pure: no I/O."""
    code = _code_access(inputs)
    testing = _testing(inputs)
    first = _first_task(inputs, testing["state"])
    rows = [code, _app_values(inputs), testing, first, _work_trigger(inputs)]
    required = [r for r in rows if r["required"]]
    next_step = (
        None
        if testing["state"] == "in_progress"
        else next((r["key"] for r in required if r["state"] in _ACTIONABLE), None)
    )
    if code["state"] == "needs_admin":
        status = "needs_admin"
    elif testing["state"] == "review":
        status = "setup_to_review"
    elif code["state"] == "done" and testing["state"] in ("done", "off") and first["state"] == "done":
        status = "ready"
    else:
        status = "steps_left"
    warnings = (inputs.setup_check or {}).get("warnings") or []
    return {
        "repo": inputs.repo,
        "status": status,
        "steps_left": sum(1 for r in required if r["state"] not in ("done", "off", "unknown")),
        "summary": _summary(code["state"], testing["state"], _forge(inputs)),
        "next_step": next_step,
        "rows": rows,
        # The free check's own rule: testing will not start without the grant.
        "needs_docker": any(w["code"] == "needs_docker" for w in warnings),
        "facts_seen_at": inputs.facts_seen_at,
    }


def _row(
    key: str,
    title: str,
    state: str,
    says: str,
    action: tuple[str, str] | None = None,
    *,
    required: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": key,
        "required": required,
        "state": state,
        "title": title,
        "says": says,
        "action": {"kind": action[0], "label": action[1]} if action is not None else None,
    }
    row.update({name: value for name, value in extra.items() if value is not None})
    return row


def _forge(inputs: ReadinessInputs) -> str:
    return FORGE_NAMES.get(inputs.forge, inputs.forge)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _code_access(inputs: ReadinessInputs) -> dict[str, Any]:
    title = "Read your code and open pull requests"
    forge = _forge(inputs)
    if inputs.forge_account is not None:
        return _row("code_access", title, "done", f"Uses the {forge} account {inputs.forge_account}.")
    return _row(
        "code_access",
        title,
        "needs_admin",
        f"An admin has to connect a {forge} account that can see this repository.",
        _ADMIN_REQUEST,
    )


def _app_values(inputs: ReadinessInputs) -> dict[str, Any]:
    title = "Settings your app needs"
    facts = inputs.facts or {}
    declared = facts.get("env_keys")
    # No fact row, or discovery could not list the code: the empty list it
    # leaves then means "not read", not "no settings".
    if inputs.facts is None or not isinstance(declared, list) or facts.get("root_visible") is False:
        return _row(
            "app_values",
            title,
            "unknown",
            "embry0 has not read this repository's example settings file yet.",
            ("open_environment", "Check for settings"),
        )
    names = list(dict.fromkeys(str(k) for k in declared))
    missing = [k for k in names if k not in inputs.env_keys]
    if not missing:
        return _row("app_values", title, "done", "Every setting your app lists has a value.")
    shown = ", ".join(missing[:_KEYS_SHOWN])
    if len(missing) > _KEYS_SHOWN:
        shown += f", and {len(missing) - _KEYS_SHOWN} more"
    m = len(missing)
    return _row(
        "app_values",
        title,
        "missing",
        f"Your app lists {_plural(len(names), 'setting', 'settings')}. "
        f"{m} {'has' if m == 1 else 'have'} no value yet: {shown}.",
        ("open_environment", f"Fill in {_plural(m, 'value', 'values')}"),
        missing_keys=missing,
    )


def _testing(inputs: ReadinessInputs) -> dict[str, Any]:
    title = "Test your app before showing you a change"
    proposal = inputs.proposal
    status = proposal["status"] if proposal is not None else None
    ids: dict[str, Any] = (
        {"proposal_id": proposal["id"], "job_id": proposal.get("job_id")} if proposal is not None else {}
    )
    if status == "pending":
        return _row(
            "testing",
            title,
            "review",
            "embry0 has a test setup for you to review.",
            ("review_proposal", "Review the setup"),
            **ids,
        )
    if status == "running":
        return _row(
            "testing",
            title,
            "in_progress",
            "embry0 is reading your code and trying to start your app. You can leave this page.",
            ("view_job", "See progress") if ids.get("job_id") else None,
            **ids,
        )
    check = inputs.setup_check
    if check is None:
        if status == "failed":
            lines = str(proposal.get("failure") or "").strip().splitlines() if proposal is not None else []
            says = f"embry0 could not set up testing: {lines[0]}" if lines else "embry0 could not set up testing."
            return _row("testing", title, "failed", says, ("start_setup", "Try again"), **ids)
        return _row(
            "testing",
            title,
            "missing",
            "Testing is not set up yet. embry0 can still write code, but it cannot check that your app still works.",
            _START_SETUP,
        )
    summary = check.get("summary")
    if summary is not None and summary["when"] == "never":
        return _row(
            "testing",
            title,
            "off",
            "Testing is off for this repository. embry0 opens pull requests without testing the app.",
            _START_SETUP,
        )
    fix = ("fix_setup", "Let embry0 fix the setup")
    if not check["ok"]:
        return _row("testing", title, "needs_fix", str(check["problems"][0]["plain"]), fix)
    apps = summary["apps"] if summary is not None else []
    if not apps:
        return _row(
            "testing", title, "needs_fix", "The test setup names no apps, so nothing is tested after a change.", fix
        )
    criteria = sum(len(app["criteria"]) for app in apps)
    return _row(
        "testing",
        title,
        "done",
        f"Tests {_and([str(app['name']) for app in apps])} and checks "
        f"{_plural(criteria, 'thing', 'things')} after each change.",
    )


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _first_task(inputs: ReadinessInputs, testing: str) -> dict[str, Any]:
    title = "Finish a first task"
    # A pull request a job already opened is a fact, whatever the testing row says.
    if inputs.pr_url:
        return _row("first_task", title, "done", "First pull request opened.", pr_url=inputs.pr_url)
    if testing not in ("done", "off"):
        return _row("first_task", title, "waiting", "Comes after testing is set up or skipped.")
    return _row(
        "first_task",
        title,
        "missing",
        "Give embry0 something small and low-risk. It opens a pull request for you to review.",
        ("create_issue", "Give embry0 a first task"),
    )


def _work_trigger(inputs: ReadinessInputs) -> dict[str, Any]:
    title = "Start work from an issue"
    facts = inputs.facts or {}
    forge = _forge(inputs)
    label = inputs.trigger_label
    if facts.get("hook_present") is True:
        state = "done"
        says = (
            f"Put the label {label} on an issue here to start work. "
            f"{forge} tells embry0 about new issues in this repository."
        )
        action = None
    elif facts.get("hooks_visible") is True and facts.get("hook_present") is False:
        state = "missing"
        says = (
            f"{forge} does not tell embry0 about new issues here yet, so the label {label} does nothing. "
            "An admin can add the connection from Organizations."
        )
        action = _ADMIN_REQUEST
    else:
        state = "unknown"
        says = (
            f"Put the label {label} on an issue here to start work. "
            f"embry0 cannot tell whether {forge} tells it about new issues in this repository."
        )
        action = None
    return _row("work_trigger", title, state, says, action, required=False, trigger_label=label)


def _summary(code: str, testing: str, forge: str) -> str:
    if code != "done":
        return f"embry0 cannot read this code or open pull requests until an admin connects a {forge} account."
    if testing == "done":
        return "embry0 can read this code, open pull requests and test your app."
    can = "embry0 can read this code and open pull requests."
    then = {
        "off": "Testing is off.",
        "review": "A test setup is waiting for your review.",
        "in_progress": "It is setting up testing now.",
        "needs_fix": "Its test setup has a problem, so it cannot test your app yet.",
    }.get(testing, "It cannot test your app yet.")
    return f"{can} {then}"
