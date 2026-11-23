"""Near-miss detection for misspelled embry0 environment variables (#248).

``Embry0Config`` sets ``extra="ignore"`` because the process env is shared with
Docker, the shell and the compose stack — ``extra="forbid"`` would refuse to
start on any unrelated variable. The cost is that ``AUTH_DEV_MOD=true`` or
``IMAGE_REGISTY=registry:5000`` is dropped silently and only surfaces as a
downstream symptom. This module recovers that lost signal as a warning.

A key is a candidate when it sits under a prefix one of the declared names
uses (everything up to the first underscore), so adding a config field widens
the check for free. Two rules keep the heuristic quiet:

* Keys the orchestrator owns but does not declare as fields — the reserved
  registry in :mod:`embry0.execution.auth_provider` (``DOCKER_*``, ``QA_JOB_ID``,
  …) and the per-owner forge tokens (``GITHUB_TOKEN__<OWNER>``,
  docs/credentials.md) — are never reported.
* In a prefix embry0 *shares* with another tool (``GITHUB_`` under Actions,
  ``CLAUDE_`` under the Claude Code CLI) only a near-miss of a declared name is
  reported; an unrecognised key there belongs to that tool. Under a prefix
  embry0 owns outright, any unknown key is reported.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from pydantic import AliasChoices
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings

from embry0.execution.auth_provider import RESERVED_ENV_KEYS, RESERVED_ENV_PREFIXES

# Per-owner GitHub / per-namespace GitLab tokens (see execution/github_tokens.py
# and execution/gitlab_tokens.py) — an open-ended family, never a typo. Both
# join the server-controlled families auth_provider already registers.
_IGNORED_PREFIXES: Final[tuple[str, ...]] = RESERVED_ENV_PREFIXES + ("GITHUB_TOKEN__", "GITLAB_TOKEN__")

# Prefixes embry0 shares with tools that populate the same env: CI runners
# (GITHUB_*/GITLAB_*), the Claude Code CLI (CLAUDE_CODE_*, ANTHROPIC_MODEL,
# MAX_THINKING_TOKENS), the proxy env (HTTP_PROXY), desktop sessions
# (SESSION_MANAGER).
_SHARED_PREFIXES: Final[tuple[str, ...]] = (
    "GITHUB_",
    "GITLAB_",
    "CLAUDE_",
    "ANTHROPIC_",
    "MAX_",
    "HTTP_",
    "SESSION_",
)

# Orchestrator-env switches read straight off os.environ instead of being
# Embry0Config fields (EMB-46, execution/sandbox_manager.py), plus the CLI's
# own base-URL vars: EMBRY0_API_URL (cli/__main__.py, embry0/mcp_server/client.py)
# and EMBRY0_URL (the `health` subcommand, services/linear_dispatch.py). The
# orchestrator image sets EMBRY0_API_URL so the in-container CLI reaches :8000
# (#334) - without this entry every boot would log an unknown-env warning
# about a variable embry0 itself set.
_UNDECLARED_KEYS: Final[frozenset[str]] = frozenset({"XAI_DIRECT_EXECUTOR", "EMBRY0_API_URL", "EMBRY0_URL"})

_SUGGESTION_CUTOFF: Final[float] = 0.85


@dataclass(frozen=True)
class UnknownEnvVar:
    """An env var in an embry0-owned prefix that no config field declares."""

    name: str
    did_you_mean: str | None


def declared_env_names(model: type[BaseSettings]) -> set[str]:
    """Env var names a settings model reads, honouring env_prefix and aliases."""
    prefix = str(model.model_config.get("env_prefix") or "")
    names: set[str] = set()
    for field_name, field in model.model_fields.items():
        aliases = _aliases(field)
        if aliases:
            names.update(a.upper() for a in aliases)
        else:
            names.add(f"{prefix}{field_name}".upper())
    return names


def find_unknown_env_vars(environ: Mapping[str, str], declared: set[str]) -> list[UnknownEnvVar]:
    """Env keys that look like a misspelled ``declared`` name, sorted by key."""
    prefixes = tuple({name.split("_", 1)[0] + "_" for name in declared if "_" in name})
    findings: list[UnknownEnvVar] = []
    for name in sorted(environ):
        if name in declared or name in RESERVED_ENV_KEYS or name in _UNDECLARED_KEYS:
            continue
        if name.startswith(_IGNORED_PREFIXES) or not name.startswith(prefixes):
            continue
        close = difflib.get_close_matches(name, declared, n=1, cutoff=_SUGGESTION_CUTOFF)
        if not close and name.startswith(_SHARED_PREFIXES):
            continue
        findings.append(UnknownEnvVar(name=name, did_you_mean=close[0] if close else None))
    return findings


def _aliases(field: FieldInfo) -> list[str]:
    """Explicit env aliases on a field; env_prefix does not apply to these."""
    out: list[str] = []
    for alias in (field.validation_alias, field.alias):
        if isinstance(alias, str):
            out.append(alias)
        elif isinstance(alias, AliasChoices):
            out.extend(c for c in alias.choices if isinstance(c, str))
    return out
