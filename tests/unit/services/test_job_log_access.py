"""The rules of a job's log scope (embry0.services.job_log_access), apart from the paths that apply them.

The watcher's tick (tests/unit/services/test_watcher.py), init_node (test_nodes_init.py), the resume
(test_resume_reenroll.py) and the stored stacks (test_log_tool_repository_scope.py) pin where they apply.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from embry0.execution.proxy.container_allowlist import ContainerAllowlist
from embry0.services.job_log_access import (
    admitted,
    analysis_job_scope,
    analysis_scope,
    developer_job_scope,
    own_entries,
)
from embry0.services.watcher_stacks import WatcherStack

_REPO = "acme/web"
_CONFIG = SimpleNamespace(context_proxy_container_allowlist="Acme-Web, shop-*", developer_log_tool_enabled=True)


def _stack(settings: dict[str, object]) -> WatcherStack:
    return WatcherStack(repo=_REPO, logql='{app="web"}', settings=settings)


class _Stacks:
    def __init__(self, stack: WatcherStack | None = None, *, fails: bool = False) -> None:
        self._stack, self._fails = stack, fails

    async def get(self, repo: str) -> WatcherStack | None:
        if self._fails:
            raise ConnectionError("database unreachable")
        return self._stack if repo == _REPO else None


def test_a_stack_without_the_setting_has_no_list_of_its_own_and_an_empty_one_names_nothing() -> None:
    assert own_entries(_stack({})) is None
    assert own_entries(_stack({"context_proxy_container_allowlist": " , "})) == []
    assert own_entries(_stack({"context_proxy_container_allowlist": "acme-web, shop-*"})) == ["acme-web", "shop-*"]


def test_a_name_is_admitted_in_the_deployment_s_spelling_once() -> None:
    deployment = ContainerAllowlist(["Acme-Web", "shop-*"])
    assert admitted(["acme-web", "ACME-WEB", "shop-blue", "shop.blue", None, 7], deployment) == [
        "Acme-Web",
        "shop-blue",
    ]
    assert admitted(["acme-web", "shop-blue"], deployment, ContainerAllowlist(["shop-*"])) == ["shop-blue"]


def test_a_pattern_entry_is_never_a_name_the_analysis_may_query() -> None:
    deployment = ContainerAllowlist(["shop-*"])
    assert analysis_scope(["shop-*"], deployment, ["shop-blue", "elsewhere"]) == ["shop-blue"]
    assert analysis_scope(None, deployment, []) == []


@pytest.mark.asyncio
async def test_a_stack_read_that_fails_grants_nothing() -> None:
    """A lookup that does not answer is no stack, so no containers: never the deployment's allowlist instead."""
    configurable = {"watcher_stacks_repo": _Stacks(fails=True)}
    assert await analysis_job_scope(_REPO, ["acme-web"], configurable, _CONFIG) == []
    assert await developer_job_scope(_REPO, ["acme-web"], configurable, _CONFIG) == []


@pytest.mark.asyncio
async def test_a_stack_whose_own_list_is_empty_grants_its_analysis_nothing() -> None:
    configurable = {"watcher_stacks_repo": _Stacks(_stack({"context_proxy_container_allowlist": ""}))}
    assert await analysis_job_scope(_REPO, ["acme-web"], configurable, _CONFIG) == []


@pytest.mark.asyncio
async def test_a_job_of_another_repository_is_granted_nothing_by_this_stack() -> None:
    configurable = {"watcher_stacks_repo": _Stacks(_stack({"context_proxy_container_allowlist": "acme-web"}))}
    assert await analysis_job_scope("globex/gizmos", ["acme-web"], configurable, _CONFIG) == []
    assert await developer_job_scope("globex/gizmos", ["acme-web"], configurable, _CONFIG) == []
    assert await developer_job_scope(_REPO, ["acme-web"], configurable, _CONFIG) == ["Acme-Web"]
