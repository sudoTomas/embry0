"""Relevance-gated (conditional) acceptance criteria evaluation — EMB-39.

Pure module: no IO, no LangGraph. The orchestrator calls
:func:`evaluate_conditional_criteria` once per run after the apps-to-QA set
is decided, and appends the returned per-app criteria AFTER the job-level
override replace (see orchestrator step 4c for why append-after-replace).

Predicate semantics:

- within one list: OR (any changed file matching any glob satisfies
  ``changed_paths``; any affected app named satisfies ``affected_apps``;
  any label present satisfies ``labels``)
- between non-empty fields of one ``when``: AND
- between groups: independent — the union of fired groups' criteria is
  appended, deduped, in qa.yaml declaration order

Deployed/standalone runs arrive with ``changed_files == []`` and an empty
affected set, so ``changed_paths``/``affected_apps`` predicates can never
match there — by design (EMB-39 default-OFF rule). ``labels`` and the
force knob (``forced_group_names``) are the only paths that fire a group
on such runs. ``forced_group_names=["*"]`` forces every group.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

from embry0.workflows.qa._glob_match import compile_glob, match_any
from embry0.workflows.qa.qa_yaml_v2 import ConditionalCriteriaGroup, QAYamlConfigV2

__all__ = ["ConditionalEvaluation", "evaluate_conditional_criteria"]

FORCE_ALL_SENTINEL = "*"


@dataclass(frozen=True, slots=True)
class ConditionalEvaluation:
    """Outcome of one conditional-criteria evaluation."""

    criteria_by_app: dict[str, list[str]] = field(default_factory=dict)
    """app name -> criteria to append (deduped, qa.yaml order). Only apps in apps_to_qa."""
    matched_groups: list[str] = field(default_factory=list)
    """Groups fired via predicates."""
    forced_groups: list[str] = field(default_factory=list)
    """Groups fired via the force knob (a group both forced and matched counts here)."""
    unknown_forced_groups: list[str] = field(default_factory=list)
    """Forced names with no corresponding group — caller must fail the run."""
    group_apps: dict[str, list[str]] = field(default_factory=dict)
    """group name -> apps its criteria were appended to (may be empty if the
    group fired but its ``apps`` scope had no overlap with the run)."""

    def groups_persisted(self) -> list[dict[str, Any]]:
        """Rows for qa_run_metadata.conditional_groups (observability)."""
        return [
            {"name": name, "source": source, "apps": sorted(self.group_apps.get(name, []))}
            for source, names in (("forced", self.forced_groups), ("matched", self.matched_groups))
            for name in names
        ]


def _group_fires(
    group: ConditionalCriteriaGroup,
    *,
    changed_files: Sequence[str],
    affected_apps_from_diff: Collection[str],
    labels_lower: Collection[str],
) -> bool:
    when = group.when
    if when.changed_paths:
        compiled = [compile_glob(p) for p in when.changed_paths]
        if not any(match_any(f, compiled) for f in changed_files):
            return False
    if when.affected_apps and not any(a in affected_apps_from_diff for a in when.affected_apps):
        return False
    if when.labels and not any(lbl.lower() in labels_lower for lbl in when.labels):
        return False
    return True


def evaluate_conditional_criteria(
    cfg: QAYamlConfigV2,
    *,
    changed_files: Sequence[str],
    apps_to_qa: Sequence[str],
    affected_apps_from_diff: Collection[str],
    labels: Collection[str] = (),
    forced_group_names: Sequence[str] = (),
) -> ConditionalEvaluation:
    """Evaluate every conditional group against this run's change context.

    ``changed_files``: repo-relative POSIX paths (git diff output).
    ``affected_apps_from_diff``: diff-derived MANAGED affected set — NOT
    apps_to_qa (deployed apps always join apps_to_qa and would trivially
    satisfy the predicate every run).
    """
    groups = list(cfg.conditional_acceptance_criteria)
    known_names = {g.name for g in groups}

    force_all = FORCE_ALL_SENTINEL in forced_group_names
    explicit_forced = [n for n in forced_group_names if n != FORCE_ALL_SENTINEL]
    unknown_forced = sorted({n for n in explicit_forced if n not in known_names})

    labels_lower = {str(lbl).lower() for lbl in labels}
    apps_in_run = list(apps_to_qa)

    criteria_by_app: dict[str, list[str]] = {}
    group_apps: dict[str, list[str]] = {}
    matched: list[str] = []
    forced: list[str] = []

    for group in groups:
        is_forced = force_all or group.name in explicit_forced
        fires = is_forced or _group_fires(
            group,
            changed_files=changed_files,
            affected_apps_from_diff=affected_apps_from_diff,
            labels_lower=labels_lower,
        )
        if not fires:
            continue
        (forced if is_forced else matched).append(group.name)
        group_apps[group.name] = []
        for app in apps_in_run:
            if group.apps and app not in group.apps:
                continue
            bucket = criteria_by_app.setdefault(app, [])
            for criterion in group.criteria:
                if criterion not in bucket:
                    bucket.append(criterion)
            group_apps[group.name].append(app)

    # Drop apps that ended up with no criteria (defensive; dedup can't empty
    # a bucket, but keeps the contract "only apps with appends" explicit).
    criteria_by_app = {a: c for a, c in criteria_by_app.items() if c}

    return ConditionalEvaluation(
        criteria_by_app=criteria_by_app,
        matched_groups=matched,
        forced_groups=forced,
        unknown_forced_groups=unknown_forced,
        group_apps=group_apps,
    )
