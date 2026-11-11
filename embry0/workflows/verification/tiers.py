"""Verification tiers (#298): how much checking does this diff need?

``classify_diff`` is a pure function over the changed-file list:

- ``neutral_only`` — docs and the like; nothing to run.
- ``backend_only`` — the deterministic test gate (#299) is the verification;
  browser QA is skipped.
- ``frontend``     — browser QA, against a stack built FROM THE BRANCH.

Each path is tested frontend-first, then backend, then neutral. A path that
matches nothing is *unknown* and makes the change ``frontend`` — the stricter
tier — as does an empty (unknown) diff.

``plan_browser_qa`` turns the tier into routing. It encodes the rule that a
branch job never uses QA against a *deployed* target as a gate: the deployed
site does not contain the branch (``ERR_QA_FAILURES_UNRESOLVED``, 7 jobs).
Until the ephemeral branch stack (#302) exists, a repo whose QA config has no
branch-booted (``target: managed``) app gets its frontend PRs opened as
human-merge-only instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from embry0.workflows.qa._glob_match import Glob, match_any, read_glob
from embry0.workflows.verification.config import TierRules

Tier = Literal["neutral_only", "backend_only", "frontend"]
BrowserQAReason = Literal["neutral_only", "backend_only", "branch_stack", "no_branch_stack", "qa_not_requested"]

DEFAULT_FRONTEND_PATHS: tuple[str, ...] = (
    "frontend/**",
    "web/**",
    "ui/**",
    "client/**",
    "**/public/**",
    "**/static/**",
    "**/templates/**",
    *(f"**/*.{ext}" for ext in ("tsx", "jsx", "vue", "svelte", "css", "scss", "sass", "less", "html", "htm")),
)
# Deliberately NOT here: .ts/.js/.json/.yml/Dockerfile/shell — they are as
# often frontend or deployment as backend, so they stay unknown (strict)
# until a repo's own rules claim them.
DEFAULT_BACKEND_PATHS: tuple[str, ...] = (
    *(f"**/*.{ext}" for ext in ("py", "java", "kt", "scala", "go", "rs", "rb", "php", "cs", "sql", "properties")),
    "**/pom.xml",
    "**/build.gradle",
    "**/build.gradle.kts",
    "**/pyproject.toml",
    "**/requirements*.txt",
    "**/poetry.lock",
    "**/uv.lock",
    "**/go.mod",
    "**/go.sum",
    "**/Cargo.toml",
    "**/Cargo.lock",
)
DEFAULT_NEUTRAL_PATHS: tuple[str, ...] = ("docs/**", "**/*.md", "**/*.rst", "LICENSE*", ".gitignore")

# Paths listed per class in the stored decision (counts are always exact).
MAX_LISTED_PATHS = 20


@dataclass(frozen=True)
class TierDecision:
    tier: Tier
    reason: str
    frontend: list[str] = field(default_factory=list)
    backend: list[str] = field(default_factory=list)
    neutral: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        classes = {"frontend": self.frontend, "backend": self.backend, "neutral": self.neutral, "unknown": self.unknown}
        return {
            "tier": self.tier,
            "reason": self.reason,
            "counts": {name: len(paths) for name, paths in classes.items()},
            **{name: paths[:MAX_LISTED_PATHS] for name, paths in classes.items()},
        }


def _normalise(path: str) -> str:
    return re.sub(r"^(\./)+", "", path.strip().replace("\\", "/")).lstrip("/")


def _compiled(override: list[str] | None, default: tuple[str, ...]) -> list[Glob]:
    return [read_glob(p) for p in (default if override is None else override)]


def classify_diff(changed_files: list[str], rules: TierRules | None = None) -> TierDecision:
    rules = rules or TierRules()
    # `is None`, not falsiness: an explicit empty list means "no such paths".
    frontend_rx = _compiled(rules.frontend_paths, DEFAULT_FRONTEND_PATHS)
    backend_rx = _compiled(rules.backend_paths, DEFAULT_BACKEND_PATHS)
    neutral_rx = _compiled(rules.neutral_paths, DEFAULT_NEUTRAL_PATHS)

    paths = [p for p in (_normalise(f) for f in changed_files) if p]
    if not paths:
        return TierDecision("frontend", "the changed files are unknown, so the stricter tier applies")

    frontend: list[str] = []
    backend: list[str] = []
    neutral: list[str] = []
    unknown: list[str] = []
    for path in paths:
        if match_any(path, frontend_rx):
            frontend.append(path)
        elif match_any(path, backend_rx):
            backend.append(path)
        elif match_any(path, neutral_rx):
            neutral.append(path)
        else:
            unknown.append(path)

    tier: Tier
    if frontend:
        tier, reason = "frontend", f"{len(frontend)} changed file(s) are frontend"
    elif unknown:
        tier, reason = "frontend", f"{len(unknown)} changed file(s) match no path rule, so the stricter tier applies"
    elif backend:
        tier, reason = "backend_only", "every changed file is backend or documentation"
    else:
        tier, reason = "neutral_only", "every changed file is documentation"
    return TierDecision(tier, reason, frontend=frontend, backend=backend, neutral=neutral, unknown=unknown)


@dataclass(frozen=True)
class BrowserQAPlan:
    run_browser_qa: bool
    human_merge_only: bool
    reason_code: BrowserQAReason


def plan_browser_qa(*, tier: Tier, needs_qa: bool, managed_apps: list[str], deployed_apps: list[str]) -> BrowserQAPlan:
    """Whether browser QA gates this branch job, and if not, who merges.

    ``managed_apps`` / ``deployed_apps`` are the app names of the repo's
    stored QA config by ``target``. Deployed apps never count: they are
    available to standalone QA jobs only.
    """
    del deployed_apps  # named in the signature so the rule is visible at the call site
    if tier != "frontend":
        return BrowserQAPlan(False, False, tier)
    if not managed_apps:
        return BrowserQAPlan(False, True, "no_branch_stack")
    if not needs_qa:
        return BrowserQAPlan(False, True, "qa_not_requested")
    return BrowserQAPlan(True, False, "branch_stack")
