"""Per-repo verification config — ``repo_preferences.verification`` (#298, #299).

Stored as one JSONB object and validated here, at the API boundary and again
when a job reads it. A job never fails because the stored object is unusable:
``load_verification_config`` returns ``(None, reason)`` and the gate reports
``not_configured`` on the PR instead.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator

from embry0.workflows.qa._glob_match import NEGATION, GlobPatternError, check_glob, warn_once

# A test command is executable text: it is refused when too long, never cut.
COMMAND_MAX_CHARS = 2000
DEFAULT_TARGET_TIMEOUT_SECONDS = 1200
MAX_TARGET_TIMEOUT_SECONDS = 7200
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")


#: The validation context a stored config is read with (:func:`load_verification_config`): a glob this grammar no
#: longer writes, which the release before read, is read as it read it. Without it (a request) a glob must be one
#: it writes.
STORED: dict[str, bool] = {"stored": True}


def _validated_globs(patterns: list[str], info: ValidationInfo) -> list[str]:
    cleaned = [p.strip() for p in patterns]
    stored = bool((info.context or {}).get("stored"))
    for pattern in cleaned:
        if pattern.startswith("!") and not stored:
            raise ValueError(f"{NEGATION} (glob {pattern!r})")
        try:
            check_glob(pattern, stored=stored)
        except GlobPatternError as exc:
            raise ValueError(str(exc)) from exc
    return cleaned


class TestTarget(BaseModel):
    """One test command the gate runs on base and on head.

    ``paths`` are repo-relative globs (same grammar as qa.yaml
    ``changed_paths``); the target runs when any changed file matches. An
    empty list means "always". ``command`` runs with ``bash -c`` from the
    root of the checkout under test, so it must not depend on an absolute
    ``/workspace`` path: each run happens in a fresh checkout of its commit,
    and ``/workspace`` is the job's working copy, which no run tests. It
    installs its own dependencies into that tree.
    """

    __test__ = False  # not a pytest class, despite the name
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    paths: list[str] = Field(default_factory=list, max_length=20)
    command: str = Field(min_length=1, max_length=COMMAND_MAX_CHARS)
    timeout_seconds: int = Field(default=DEFAULT_TARGET_TIMEOUT_SECONDS, ge=30, le=MAX_TARGET_TIMEOUT_SECONDS)

    @field_validator("name")
    @classmethod
    def _name_shape(cls, v: str) -> str:
        # The name becomes a log file name inside the sandbox.
        if not _NAME_RE.fullmatch(v):
            raise ValueError("name must match [A-Za-z0-9][A-Za-z0-9._-]{0,59}")
        return v

    @field_validator("paths")
    @classmethod
    def _paths_are_globs(cls, v: list[str], info: ValidationInfo) -> list[str]:
        return _validated_globs(v, info)

    @field_validator("command")
    @classmethod
    def _no_nul(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("command must not contain NUL bytes")
        return v


class TierRules(BaseModel):
    """Per-repo overrides for the diff classifier (``tiers.classify_diff``).

    Each list, when given, REPLACES the built-in default for that class;
    ``None`` keeps the default. A path that matches no list is *unknown* and
    classifies as the stricter (frontend) tier.
    """

    model_config = ConfigDict(extra="forbid")

    frontend_paths: list[str] | None = Field(default=None, max_length=50)
    backend_paths: list[str] | None = Field(default=None, max_length=50)
    neutral_paths: list[str] | None = Field(default=None, max_length=50)

    @field_validator("frontend_paths", "backend_paths", "neutral_paths")
    @classmethod
    def _lists_are_globs(cls, v: list[str] | None, info: ValidationInfo) -> list[str] | None:
        return None if v is None else _validated_globs(v, info)


class VerificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_targets: list[TestTarget] = Field(default_factory=list, max_length=10)
    # "always" runs every selected target on base too (needed to tell which
    # tests a change FIXED, and by the reproduction contract, #300).
    # "on_head_failure" skips the base run for targets that pass on head —
    # the verdict is identical, the job is faster.
    base_run: Literal["always", "on_head_failure"] = "always"
    # A target that looks newly broken is re-run on head this many times; a
    # passing re-run records the failure as flaky instead of blocking.
    head_reruns: int = Field(default=1, ge=0, le=2)
    # Wall-clock budget for the gate's runs, each with its own checkout; it
    # starts after the prepare step. Runs that do not fit are recorded as
    # not run (verdict "error": never blocking, never a silent pass).
    total_timeout_seconds: int = Field(default=3600, ge=60, le=14400)
    tiers: TierRules = Field(default_factory=TierRules)

    @field_validator("test_targets")
    @classmethod
    def _unique_names(cls, v: list[TestTarget]) -> list[TestTarget]:
        names = [t.name for t in v]
        if len(names) != len(set(names)):
            raise ValueError("test_targets names must be unique")
        return v


def negated_globs(config: VerificationConfig) -> list[str]:
    """Every glob of ``config`` (its targets' paths and its tier rules) that starts with ``!``, in order."""
    globs = [p for target in config.test_targets for p in target.paths]
    tiers = config.tiers
    globs += [p for rule in (tiers.frontend_paths, tiers.backend_paths, tiers.neutral_paths) for p in rule or ()]
    return [g for g in globs if g.startswith("!")]


def negation_warning(config: VerificationConfig | None) -> str | None:
    """What a person is told of a config holding a glob that starts with ``!``: it reads, and matches only paths
    that start with ``!``. None when it holds none."""
    negated = negated_globs(config) if config is not None else []
    if not negated:
        return None
    listed = ", ".join(repr(g) for g in negated[:5]) + (f" and {len(negated) - 5} more" if len(negated) > 5 else "")
    return (
        f"This verification config holds globs that start with '!' ({listed}): negation is not supported, so each "
        "matches only paths that start with '!'. It is kept as it was; a new save refuses them."
    )


def load_verification_config(raw: Any, *, repo: str = "") -> tuple[VerificationConfig | None, str | None]:
    """Parse a stored ``repo_preferences.verification`` value.

    Returns ``(config, None)``, or ``(None, reason)`` when nothing usable is
    stored. Never raises: a bad row degrades the gate to ``not_configured``.
    A config holding a glob that starts with ``!`` reads, as it did, and
    logs one warning naming them once per ``repo`` (the repository it is
    read for) and config; a request is refused one.
    """
    if raw is None or raw == {}:
        return None, None
    if not isinstance(raw, dict):
        return None, "stored verification config is not an object"
    try:
        config = VerificationConfig.model_validate(raw, context=STORED)
    except ValidationError as exc:
        errors = exc.errors()
        where = ".".join(str(p) for p in errors[0]["loc"]) if errors else ""
        message = errors[0]["msg"] if errors else "invalid"
        return None, f"stored verification config is invalid at {where or '<root>'}: {message}"
    negated = negated_globs(config)
    if negated:
        warn_once(
            "verification_config_negated_globs",
            repo=repo,
            text=json.dumps(raw, sort_keys=True, default=str),
            globs=[g[:100] for g in negated[:10]],
            count=len(negated),
        )
    return config, None
