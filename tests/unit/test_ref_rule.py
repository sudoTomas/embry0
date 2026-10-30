"""Every place embry0 takes a branch or ref applies the same rule.

The rule is the clone's: 1-255 characters from ``[A-Za-z0-9._/-]``, not
starting with ``-``, as git takes no branch or tag name that starts with one.
A caller with its own copy of the pattern drifts from it: a base-branch
preference of ``-dev`` was stored with a 200, and the clone then fetched
``main`` in its place.

Each caller is asked for the value it would use; a refused value comes back
as None or as something else.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from embry0.api.schemas import ContextType, JobContext
from embry0.api.schemas.repo_preferences import RepoPreferencesUpdateRequest
from embry0.execution.proxy.git_scope import normalize_push_branch
from embry0.execution.workspace_diff import diff_line_counts, resolve_base_branch
from embry0.services import base_branch
from embry0.storage.repositories.skills import SkillSourceRefused, _check_ref
from embry0.workspace_init.base import WorkspaceInitError
from embry0.workspace_init.git import GitWorkspaceInitializer


def _or_none(build: Callable[[], str | None], *refusals: type[Exception]) -> str | None:
    try:
        return build()
    except refusals:
        return None


def _docker(output: str) -> Mock:
    docker = Mock()
    docker.build_exec_cmd = Mock(side_effect=lambda cid, cmd: ["exec", cid, *cmd])
    docker.run_cmd = AsyncMock(return_value=output)
    return docker


def _diff_base(value: str) -> str | None:
    counts = asyncio.run(diff_line_counts(docker=_docker("1\t2\tsrc/a.py\n"), container_id="c1", base_branch=value))
    return None if counts is None else value


def _init_ref(value: str) -> str | None:
    GitWorkspaceInitializer().validate({"repo": "acme/widgets", "ref": value}, None)
    return value


CALLERS: dict[str, Callable[[str], str | None]] = {
    "job context.ref (API)": lambda v: _or_none(
        lambda: JobContext(type=ContextType.git, repo="acme/widgets", ref=v).ref, ValidationError
    ),
    "base_branch preference (API)": lambda v: _or_none(
        lambda: RepoPreferencesUpdateRequest(base_branch=v).base_branch, ValidationError
    ),
    "stored base branch": lambda v: base_branch._safe(v),
    "skill source ref": lambda v: _or_none(lambda: _check_ref("git", v), SkillSourceRefused),
    "workspace init ref": lambda v: _or_none(lambda: _init_ref(v), WorkspaceInitError),
    "git-proxy push branch": normalize_push_branch,
    "diff base": _diff_base,
    "resolved origin HEAD": lambda v: asyncio.run(resolve_base_branch(docker=_docker(v), container_id="c1")),
}


@pytest.mark.parametrize("value", ["dev", "release/2026.09", "fix-x", "a" * 40, "x" * 255])
def test_every_caller_takes_a_ref_the_rule_accepts(value: str) -> None:
    refused = [name for name, use in CALLERS.items() if use(value) != value]
    assert not refused, f"these refuse {value!r}, which the ref rule accepts: {refused}"


@pytest.mark.parametrize(
    "value", ["-dev", "-", "main\n", "feat branch", "a;b", pytest.param("x" * 256, id="256 chars")]
)
def test_every_caller_refuses_a_ref_the_rule_refuses(value: str) -> None:
    accepted = [name for name, use in CALLERS.items() if use(value) == value]
    assert not accepted, f"these take {value!r}, which the ref rule refuses: {accepted}"
