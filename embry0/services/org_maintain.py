"""What it takes to maintain each repository, in one sentence, from facts (ADR 0015).

The needs list (ADR 0011) reads an owner's discovery run per kind: these
repositories are not registered, those have no webhook. This module reads the
same facts per repository: one plain sentence a person can act on, and the
ordered steps that sentence names. No model is called. Every clause comes from
a rule in :data:`RULES` over the repository's fact row and the organization's
access probe, so the same facts always give the same sentence, and a fact the
credential could not see (``None``) never counts as a yes or a no.

The rules mirror the needs kinds of :mod:`embry0.services.org_needs`, so a
repository's steps are the actions of the needs that cover it, with one
refinement the probe allows: a missing webhook is the ``hook`` step only when
the probe found hook administration, and ``credential`` otherwise, because the
hook action is skipped without it.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: The order steps are taken in, a subset of the needs actions
#: (``org_needs.ACTIONS``; ``none`` is never a step). A repository is
#: registered first, because the onboard action skips an unregistered one; the
#: credential is widened before the webhook it has to create; the webhook goes
#: on before onboarding, onboarding (which drafts the QA config) before
#: watching; settings a person writes by hand come last.
STEP_ORDER = ("register", "credential", "hook", "onboard", "watch", "setting")


@dataclass(frozen=True)
class Reading:
    """One repository's fact row as the rules read it.

    ``listed`` is whether the latest discovery listed the repository;
    ``delivered_only`` is a row only the webhook receiver wrote (ADR 0012), so
    discovery never read the repository; ``hooks_allowed`` is the probe's
    answer on hook administration for the owner (``None``: not probed).
    """

    facts: Mapping[str, Any]
    archived: bool = False
    listed: bool = True
    delivered_only: bool = False
    hooks_allowed: bool | None = None

    def has(self, key: str) -> bool:
        return bool(self.facts.get(key))

    @property
    def registered(self) -> bool:
        return self.has("registered")

    @property
    def watched(self) -> bool:
        return self.has("watcher_stack")

    @property
    def runnable(self) -> bool:
        return self.has("boot_hint")

    @property
    def servable(self) -> bool:
        """Something the onboard pipeline can boot, or a UI it can serve."""
        return self.runnable or self.has("frontend")

    @property
    def hook_missing(self) -> bool:
        """The hooks are visible and none points at this deployment."""
        return self.facts.get("hooks_visible") is True and self.facts.get("hook_present") is False

    @property
    def hook_target_unknown(self) -> bool:
        """The hooks are visible and WEBHOOK_PUBLIC_URL is unset, so whether one points here is unknown."""
        return self.facts.get("hooks_visible") is True and self.facts.get("hook_present") is None

    @property
    def unreadable(self) -> list[str]:
        """What the credential could not read, in the needs list's words."""
        parts = (("contents", self.facts.get("root_visible")), ("hooks", self.facts.get("hooks_visible")))
        return [name for name, visible in parts if visible is False]

    @property
    def env_unset(self) -> list[str]:
        """Variables the env example declares that embry0 holds no value for."""
        declared = self.facts.get("env_keys")
        if not isinstance(declared, list):
            return []
        have = {str(k) for k in self.facts.get("env_keys_set") or []}
        return [str(k) for k in declared if str(k) not in have]


@dataclass(frozen=True)
class Rule:
    """One clause of the sentence: when it holds, what it says, the step it asks for.

    ``does`` is the phrase after "so" for the step; a rule without one only
    states a fact. A ``final`` rule ends the reading: what follows it in
    :data:`RULES` would read facts this row does not have.
    """

    name: str
    when: Callable[[Reading], bool]
    says: str | Callable[[Reading], str]
    step: str | None = None
    does: str = ""
    final: bool = False

    def say(self, reading: Reading) -> str:
        return self.says if isinstance(self.says, str) else self.says(reading)


def _what_runs(r: Reading) -> str:
    thing = "a UI" if r.has("frontend") else "a service"
    hint = str(r.facts.get("boot_hint") or "")
    if not hint:
        return f"{thing} with nothing to start"
    if hint.startswith("npm run "):
        return f"{thing} with a {hint.removeprefix('npm run ')} script"
    if hint.startswith("docker compose"):
        return f"{thing} with a compose file"
    return f"{thing} with a boot command"


def _no_hook_admin(r: Reading) -> str:
    probe = (
        "the probe found the token cannot manage hooks"
        if r.hooks_allowed is False
        else "the probe has not checked hook administration"
    )
    return f"no webhook ({probe})"


def _env_unset(r: Reading) -> str:
    count = len(r.env_unset)
    return f"{count} environment {'value' if count == 1 else 'values'} unset"


#: The rules, in the order their clauses are read. The steps they ask for are
#: then put in :data:`STEP_ORDER`, whatever the order here.
RULES: tuple[Rule, ...] = (
    Rule("archived", lambda r: r.archived, "Archived", final=True),
    Rule(
        "not_listed",
        lambda r: not r.listed and not r.delivered_only,
        "Not listed by the latest discovery run (removed, renamed or out of the credential's sight)",
        final=True,
    ),
    Rule(
        "delivered_unregistered",
        lambda r: r.delivered_only and not r.registered,
        "Not registered, and known only from its webhook deliveries",
        step="register",
        does="register, then run discovery to read it",
        final=True,
    ),
    Rule(
        "delivered_registered",
        lambda r: r.delivered_only and r.registered,
        "Registered from a webhook delivery and not read by discovery yet",
        does="run discovery to read it",
        final=True,
    ),
    Rule("registered_watched", lambda r: r.registered and r.watched, "Registered and watched"),
    Rule("registered", lambda r: r.registered and not r.watched, "Registered"),
    Rule("not_registered", lambda r: not r.registered, "Not registered", step="register", does="register"),
    Rule("qa_configured", lambda r: r.has("qa_config"), "QA configured"),
    Rule(
        "onboarding_candidate",
        lambda r: not r.has("qa_config") and r.servable,
        _what_runs,
        step="onboard",
        does="onboard to draft QA",
    ),
    Rule(
        "no_qa_config",
        lambda r: r.registered and not r.has("qa_config") and not r.servable,
        "no QA config and nothing to boot or serve",
        step="setting",
        does="write the QA config by hand",
    ),
    Rule(
        "no_webhook",
        lambda r: r.hook_missing and r.hooks_allowed is True,
        "no webhook (the token can manage hooks)",
        step="hook",
        does="add the webhook",
    ),
    Rule(
        "no_webhook_without_hook_admin",
        lambda r: r.hook_missing and r.hooks_allowed is not True,
        _no_hook_admin,
        step="credential",
        does="give the credential hook administration and probe the owner again",
    ),
    Rule(
        "hook_origin_unknown",
        lambda r: r.hook_target_unknown,
        "webhooks to an unknown address (WEBHOOK_PUBLIC_URL is not set)",
        step="setting",
        does="set WEBHOOK_PUBLIC_URL and run discovery again",
    ),
    Rule(
        "not_watched",
        lambda r: r.registered and not r.watched and r.runnable,
        "not watched",
        step="watch",
        does="add a watcher stack",
    ),
    Rule(
        "no_log_source",
        lambda r: r.registered and not r.watched and not r.runnable,
        "no log source found",
        step="setting",
        does="name the logs a watcher stack should read",
    ),
    Rule(
        "no_deploy_marker",
        lambda r: r.watched and r.facts.get("deploy_marker") is False,
        "no deploy marker",
        step="setting",
        does="name a deploy query",
    ),
    Rule(
        "access_limited",
        lambda r: bool(r.unreadable),
        lambda r: f"the credential cannot read its {' and '.join(r.unreadable)}",
        step="credential",
        does="widen the credential",
    ),
    Rule(
        "env_values_needed",
        lambda r: r.registered and bool(r.env_unset),
        _env_unset,
        step="setting",
        does="provide the unset environment values",
    ),
)


@dataclass(frozen=True)
class MaintainHint:
    hint: str
    steps: tuple[str, ...]
    rules: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"maintain_hint": self.hint, "maintain_steps": list(self.steps)}


def _rank(rule: Rule) -> int:
    return STEP_ORDER.index(rule.step) if rule.step else len(STEP_ORDER)


def hint_for(reading: Reading, rules: Sequence[Rule] = RULES) -> MaintainHint:
    """The sentence and steps for one reading: every rule that holds, in order, up to a final one.

    The clauses are joined with semicolons; the steps' phrases follow "so" in
    :data:`STEP_ORDER` and are joined with "then"; with no step the sentence
    ends "nothing to do".
    """
    matched: list[Rule] = []
    for rule in rules:
        if rule.when(reading):
            matched.append(rule)
            if rule.final:
                break
    said = "; ".join(rule.say(reading) for rule in matched)
    said = said[:1].upper() + said[1:]
    doing = sorted((rule for rule in matched if rule.does), key=_rank)
    tail = (", so " + ", then ".join(rule.does for rule in doing) + ".") if doing else "; nothing to do."
    steps = tuple(dict.fromkeys(rule.step for rule in doing if rule.step))
    return MaintainHint(hint=said + tail, steps=steps, rules=tuple(rule.name for rule in matched))


def current_runs(runs: Sequence[Mapping[str, Any]]) -> frozenset[str] | None:
    """The discovery runs whose rows are current, from runs listed newest first.

    That is the newest finished run and every run started after it: a finished
    run stamped every repository it listed, so a row an older run stamped is
    one the latest discovery did not list. None when no run in ``runs``
    finished, and then no row can be called stale.
    """
    seen: list[str] = []
    for run in runs:
        seen.append(str(run["id"]))
        if run.get("status") == "done":
            return frozenset(seen)
    return None


def maintain_hint(
    row: Mapping[str, Any], access: Mapping[str, Any], *, current: Collection[str] | None = None
) -> MaintainHint:
    """What it takes to maintain the repository of one fact row.

    ``row`` is an :meth:`OrgReposRepository.list_for_owner` row, ``access`` the
    organization's stored probe, ``current`` the result of :func:`current_runs`.
    """
    run_id = row.get("run_id")
    hooks = access.get("hooks")
    return hint_for(
        Reading(
            facts=dict(row.get("facts") or {}),
            archived=bool(row.get("archived")),
            listed=run_id is not None and (current is None or str(run_id) in current),
            delivered_only=run_id is None,
            hooks_allowed=hooks if isinstance(hooks, bool) else None,
        )
    )
