"""Init node — sandbox creation and git credential wiring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_extract_ask_user_events_normalizes_fields():
    """_extract_ask_user_events pulls agent_ask_user events out of the event
    stream and normalizes each to {question, category, options, asking_node,
    importance}. Non-matching events are ignored."""
    from embry0.workflows.issue_to_pr.nodes import _extract_ask_user_events

    agent_output = {
        "events": [
            {"type": "text", "text": "hello"},
            {
                "type": "agent_ask_user",
                "question": "q1",
                "category": "design",
                "options": ["a", "b"],
            },
            {"type": "agent_ask_user", "question": "q2", "category": "general"},
            {"type": "tool_call", "name": "Read"},
        ]
    }

    pending = _extract_ask_user_events(agent_output, calling_node="developer")

    assert len(pending) == 2
    # question is inlined with category prefix + options suffix so the
    # dashboard sees the context even though issue_inputs only persists
    # the question TEXT column.
    assert "q1" in pending[0]["question"]
    assert "[design]" in pending[0]["question"]
    assert "Options: a | b" in pending[0]["question"]
    assert pending[0]["category"] == "design"
    assert pending[0]["options"] == ["a", "b"]
    assert pending[0]["asking_node"] == "developer"
    assert pending[0]["importance"] == "blocking"
    # Second question has no options and 'general' category — no prefix/suffix added.
    assert pending[1]["question"] == "q2"
    assert pending[1]["category"] == "general"
    assert pending[1]["options"] == []


def test_extract_ask_user_events_calling_node_propagated():
    """calling_node is embedded in each question so triage/review questions
    are attributed correctly — not hardcoded to 'developer'."""
    from embry0.workflows.issue_to_pr.nodes import _extract_ask_user_events

    agent_output = {
        "events": [
            {"type": "agent_ask_user", "question": "Should we split?", "category": "general"},
        ]
    }

    for node in ("triage", "developer", "review"):
        pending = _extract_ask_user_events(agent_output, calling_node=node)
        assert len(pending) == 1
        assert pending[0]["asking_node"] == node, f"asking_node must be '{node}', got '{pending[0]['asking_node']}'"


@pytest.mark.asyncio
async def test_init_node_passes_git_proxy_url_to_sandbox():
    """Init node must pass EMBRY0_GIT_PROXY_URL into the sandbox env based on
    proxy_manager.git_proxy_url. Without this, git ops inside the sandbox
    can't authenticate."""
    from embry0.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value="")

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = "http://git-proxy:9101"

    import embry0.workflows.issue_to_pr.nodes as nodes_module

    def _noop_writer():
        return lambda _event: None

    orig = getattr(nodes_module, "get_stream_writer", None)
    nodes_module.get_stream_writer = _noop_writer
    try:
        await init_node(
            state={"job_id": "job-xyz", "repo": "org/repo"},
            config={
                "configurable": {
                    "sandbox_manager": sandbox_mgr,
                    "docker": docker,
                    "proxy_manager": proxy_mgr,
                }
            },
        )
    finally:
        if orig is not None:
            nodes_module.get_stream_writer = orig

    _, create_kwargs = sandbox_mgr.create.call_args
    env = create_kwargs.get("env", {}) or {}
    assert env.get("EMBRY0_GIT_PROXY_URL") == "http://git-proxy:9101", (
        f"EMBRY0_GIT_PROXY_URL must be in sandbox env. Got: {sorted(env)}"
    )
    assert "GITHUB_TOKEN" not in env, f"GITHUB_TOKEN must NOT be in sandbox env. Got: {sorted(env)}"


@pytest.mark.asyncio
async def test_init_node_makes_the_git_proxy_the_remote_and_grants_push():
    """The sandbox's git reaches the job's repository through the git-proxy (#521):
    the repo URL is rewritten to the proxy, the bearer goes to the proxy, and no
    credential helper asks for the forge token. The developer pushes from this
    sandbox, so it is created with the push right."""
    from embry0.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **_kw: ["docker", "exec", cid, *cmd])
    docker.run_cmd = AsyncMock(return_value="")

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = "http://git-proxy:9101"

    import embry0.workflows.issue_to_pr.nodes as nodes_module

    def _noop_writer():
        return lambda _e: None

    orig = getattr(nodes_module, "get_stream_writer", None)
    nodes_module.get_stream_writer = _noop_writer
    try:
        await init_node(
            state={"job_id": "job-xyz", "repo": "org/repo"},
            config={
                "configurable": {
                    "sandbox_manager": sandbox_mgr,
                    "docker": docker,
                    "proxy_manager": proxy_mgr,
                }
            },
        )
    finally:
        if orig is not None:
            nodes_module.get_stream_writer = orig

    assert sandbox_mgr.create.await_args.kwargs.get("git_push") is True, (
        "the issue-to-pr sandbox must be created with the push right, or the developer cannot push its branch"
    )
    assert sandbox_mgr.create.await_args.kwargs.get("git_push_branches") == ["embry0/unknown-task-xyz"], (
        "#559: the push right must name the job's own branch, or the git-proxy refuses every push"
    )
    setup = [" ".join(str(p) for p in c.args[0]) for c in docker.run_cmd.await_args_list if "insteadOf" in str(c.args)]
    assert setup, f"No git proxy setup exec call found. All calls: {docker.run_cmd.await_args_list}"
    cmd_str = setup[0]
    assert "url.http://git-proxy:9101/git/org/repo.git.insteadOf https://github.com/org/repo.git" in cmd_str
    assert "http.http://git-proxy:9101/git/.extraHeader" in cmd_str
    assert "a" * 43 not in cmd_str, "the bearer goes on stdin, never on the command line"
    assert "/git-credentials" not in cmd_str, f"the sandbox must not ask for the forge token: {cmd_str}"
    assert "$GITHUB_TOKEN" not in cmd_str, f"Setup must NOT reference $GITHUB_TOKEN, got: {cmd_str}"


@pytest.mark.asyncio
async def test_init_node_skips_git_setup_when_no_proxy_url():
    """If proxy_manager is missing or has no git_proxy_url, init node logs a
    warning and skips the git setup step rather than silently configuring a
    broken credential helper."""
    from embry0.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value="")

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = ""

    import embry0.workflows.issue_to_pr.nodes as nodes_module

    def _noop_writer():
        return lambda _e: None

    orig = getattr(nodes_module, "get_stream_writer", None)
    nodes_module.get_stream_writer = _noop_writer
    try:
        await init_node(
            state={"job_id": "job-xyz", "repo": "org/repo"},
            config={
                "configurable": {
                    "sandbox_manager": sandbox_mgr,
                    "docker": docker,
                    "proxy_manager": proxy_mgr,
                }
            },
        )
    finally:
        if orig is not None:
            nodes_module.get_stream_writer = orig

    sandbox_mgr.create.assert_awaited_once()
    run_cmd_calls = [c.args for c in docker.run_cmd.await_args_list]
    for args in run_cmd_calls:
        cmd_str = " ".join(str(p) for p in args[0])
        assert "credential.helper" not in cmd_str, (
            f"Must not configure credential helper without a proxy URL, got: {cmd_str}"
        )


@pytest.mark.asyncio
async def test_init_node_skips_git_setup_when_proxy_manager_is_none():
    """When proxy_manager is entirely absent from config (not just empty URL),
    init node must fall back to 'no credentials' path without crashing."""
    from embry0.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value="")

    import embry0.workflows.issue_to_pr.nodes as nodes_module

    def _noop_writer():
        return lambda _e: None

    orig = getattr(nodes_module, "get_stream_writer", None)
    nodes_module.get_stream_writer = _noop_writer
    try:
        await init_node(
            state={"job_id": "job-xyz", "repo": "org/repo"},
            config={
                "configurable": {
                    "sandbox_manager": sandbox_mgr,
                    "docker": docker,
                    # Explicitly no proxy_manager entry
                }
            },
        )
    finally:
        if orig is not None:
            nodes_module.get_stream_writer = orig

    # Sandbox was still created
    sandbox_mgr.create.assert_awaited_once()
    # No credential helper setup was attempted
    run_cmd_calls = [c.args for c in docker.run_cmd.await_args_list]
    for args in run_cmd_calls:
        cmd_str = " ".join(str(p) for p in args[0])
        assert "credential.helper" not in cmd_str, (
            f"Must not configure credential helper without proxy_manager, got: {cmd_str}"
        )


@pytest.mark.asyncio
async def test_init_node_clone_failure_raises_runtime_error():
    """If the clone command exits non-zero, init node must raise RuntimeError
    (no silent success with empty /workspace)."""
    from embry0.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **_kw: ["docker", "exec", cid, *cmd])

    # The git setup succeeds, the clone raises
    async def _run_cmd_side(*args, **kwargs):
        cmd_str = " ".join(str(p) for p in args[0])
        if "embry0_git clone" in cmd_str:
            raise RuntimeError("exit 128: fatal: not a repo")
        return ""

    docker.run_cmd = AsyncMock(side_effect=_run_cmd_side)

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = "http://git-proxy:9101"

    import embry0.workflows.issue_to_pr.nodes as nodes_module

    def _noop_writer():
        return lambda _e: None

    orig = getattr(nodes_module, "get_stream_writer", None)
    nodes_module.get_stream_writer = _noop_writer
    try:
        with pytest.raises(RuntimeError) as exc_info:
            await init_node(
                state={"job_id": "job-xyz", "repo": "org/repo"},
                config={
                    "configurable": {
                        "sandbox_manager": sandbox_mgr,
                        "docker": docker,
                        "proxy_manager": proxy_mgr,
                    }
                },
            )
    finally:
        if orig is not None:
            nodes_module.get_stream_writer = orig

    assert "clone failed" in str(exc_info.value).lower()
    # Partial-cleanup: container was destroyed
    sandbox_mgr.destroy.assert_awaited_once()


@pytest.mark.asyncio
async def test_init_node_resolves_job_sandbox_profile():
    """RAV-1058: a job-create sandbox_profile must be resolved via profiles_repo
    and passed to SandboxManager.create (previously silently ignored)."""
    from embry0.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value="")
    profiles_repo = MagicMock()
    profiles_repo.get = AsyncMock(return_value={"name": "dev-python", "base_image": "embry0-sandbox-dev-python"})

    import embry0.workflows.issue_to_pr.nodes as nodes_module

    orig = nodes_module.get_stream_writer
    nodes_module.get_stream_writer = lambda: lambda _e: None
    try:
        await init_node(
            state={"job_id": "job-prof", "repo": "org/repo", "sandbox_profile": "dev-python"},
            config={
                "configurable": {
                    "sandbox_manager": sandbox_mgr,
                    "docker": docker,
                    "proxy_manager": MagicMock(git_proxy_url=""),
                    "profiles_repo": profiles_repo,
                }
            },
        )
    finally:
        nodes_module.get_stream_writer = orig

    profiles_repo.get.assert_awaited_once_with("dev-python")
    _, create_kwargs = sandbox_mgr.create.call_args
    assert create_kwargs.get("profile") == {"name": "dev-python", "base_image": "embry0-sandbox-dev-python"}


@pytest.mark.asyncio
async def test_init_node_unknown_profile_fails_loudly():
    """An unknown job-create profile name must fail the job, not silently run
    on the default profile."""
    from embry0.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()
    profiles_repo = MagicMock()
    profiles_repo.get = AsyncMock(return_value=None)

    import embry0.workflows.issue_to_pr.nodes as nodes_module

    orig = nodes_module.get_stream_writer
    nodes_module.get_stream_writer = lambda: lambda _e: None
    try:
        with pytest.raises(RuntimeError, match="not found"):
            await init_node(
                state={"job_id": "job-bad", "repo": "org/repo", "sandbox_profile": "nope"},
                config={
                    "configurable": {
                        "sandbox_manager": sandbox_mgr,
                        "docker": MagicMock(),
                        "proxy_manager": MagicMock(git_proxy_url=""),
                        "profiles_repo": profiles_repo,
                    }
                },
            )
    finally:
        nodes_module.get_stream_writer = orig

    sandbox_mgr.create.assert_not_awaited()


# --- RAV-1140: context-proxy provisioning for watcher analysis jobs ---


#: The containers the watcher chose for an analysis job (watcher._log_scope).
_WATCHER_SCOPE = {"watcher_log_containers": ["acme-backend"]}


def _watcher_init_mocks(context_proxy_url="http://context-proxy:9107"):
    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **kw: ["docker", "exec", cid, *cmd])
    docker.run_cmd = AsyncMock(return_value="")
    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = ""
    proxy_mgr.xai_proxy_url = ""
    proxy_mgr.context_proxy_url = context_proxy_url
    proxy_mgr.enroll_context = AsyncMock()
    return sandbox_mgr, docker, proxy_mgr


async def _run_init(state, sandbox_mgr, docker, proxy_mgr):
    import embry0.workflows.issue_to_pr.nodes as nodes_module
    from embry0.workflows.issue_to_pr.nodes import init_node

    orig = nodes_module.get_stream_writer
    nodes_module.get_stream_writer = lambda: lambda _e: None
    try:
        return await init_node(
            state=state,
            config={
                "configurable": {
                    "sandbox_manager": sandbox_mgr,
                    "docker": docker,
                    "proxy_manager": proxy_mgr,
                }
            },
        )
    finally:
        nodes_module.get_stream_writer = orig


@pytest.mark.asyncio
async def test_init_node_provisions_context_proxy_for_watcher_job():
    """Flagged context + running proxy → env URL, enroll by container id with
    the sandbox token, 0600 token file delivered, watcher_log_tool on state."""
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    update = await _run_init(
        {"job_id": "job-w1", "context": {"type": "none", "watcher_log_access": True, **_WATCHER_SCOPE}},
        sandbox_mgr,
        docker,
        proxy_mgr,
    )

    _, create_kwargs = sandbox_mgr.create.call_args
    assert create_kwargs["env"].get("EMBRY0_CONTEXT_PROXY_URL") == "http://context-proxy:9107"
    proxy_mgr.enroll_context.assert_awaited_once_with("container-abc", "a" * 43, containers=["acme-backend"])
    token_writes = [
        " ".join(str(p) for p in c.args[0])
        for c in docker.run_cmd.await_args_list
        if "context_proxy_token" in " ".join(str(p) for p in c.args[0])
    ]
    assert token_writes, "context token write exec not found"
    assert "chmod 600" in token_writes[0]
    assert update.get("watcher_log_tool") is True


@pytest.mark.asyncio
async def test_init_node_enroll_failure_degrades_to_briefing_only():
    """Enrollment failure must not fail the job — no flag, no raise."""
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    proxy_mgr.enroll_context = AsyncMock(side_effect=RuntimeError("proxy exploded"))
    update = await _run_init(
        {"job_id": "job-w2", "context": {"type": "none", "watcher_log_access": True, **_WATCHER_SCOPE}},
        sandbox_mgr,
        docker,
        proxy_mgr,
    )
    assert "watcher_log_tool" not in update
    assert update.get("current_stage") == "initialized"


@pytest.mark.asyncio
async def test_init_node_unflagged_job_gets_no_context_proxy():
    """A plain type=none job (no watcher flag) gets none of the provisioning
    even with the proxy running."""
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    update = await _run_init(
        {"job_id": "job-w3", "context": {"type": "none"}},
        sandbox_mgr,
        docker,
        proxy_mgr,
    )
    _, create_kwargs = sandbox_mgr.create.call_args
    assert "EMBRY0_CONTEXT_PROXY_URL" not in create_kwargs["env"]
    proxy_mgr.enroll_context.assert_not_awaited()
    assert "watcher_log_tool" not in update


@pytest.mark.asyncio
async def test_init_node_flag_without_running_proxy_is_noop():
    """Flagged job while the proxy isn't running (empty url) degrades silently."""
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks(context_proxy_url="")
    update = await _run_init(
        {"job_id": "job-w4", "context": {"type": "none", "watcher_log_access": True, **_WATCHER_SCOPE}},
        sandbox_mgr,
        docker,
        proxy_mgr,
    )
    _, create_kwargs = sandbox_mgr.create.call_args
    assert "EMBRY0_CONTEXT_PROXY_URL" not in create_kwargs["env"]
    proxy_mgr.enroll_context.assert_not_awaited()
    assert "watcher_log_tool" not in update


# --- SH-P4.4: case evidence + scoped developer log access ---

_EVIDENCE_TASK = """[ENG-1] Fix the 502

## Evidence

acme-backend logged 4x: 502 Bad Gateway for ops@example.com

## Impact

acme-frontend spins.
"""


#: The job's repository, and the containers each repository's watched stack names as its own.
_REPO = "acme/web"
_STACKS = {_REPO: "acme-backend,acme-frontend"}


class _Stacks:
    """The watched stacks, by repository: a stack's ``context_proxy_container_allowlist``, or None for no stack."""

    def __init__(self, containers: dict[str, str | None]) -> None:
        self._containers = containers

    async def get(self, repo: str):
        from embry0.services.watcher_stacks import WatcherStack

        if repo not in self._containers:
            return None
        named = self._containers[repo]
        settings = {} if named is None else {"context_proxy_container_allowlist": named}
        return WatcherStack(repo=repo, logql='{app="x"}', settings=settings)


async def _run_init_with_config(state, sandbox_mgr, docker, proxy_mgr, **cfg):
    from types import SimpleNamespace

    import embry0.workflows.issue_to_pr.nodes as nodes_module
    from embry0.workflows.issue_to_pr.nodes import init_node

    settings = {
        "context_proxy_container_allowlist": "acme-backend,acme-frontend",
        "developer_log_tool_enabled": False,
        "workspace_local_allowlist": "",
    }
    stacks = cfg.pop("stacks", _STACKS)
    settings.update(cfg)
    orig = nodes_module.get_stream_writer
    nodes_module.get_stream_writer = lambda: lambda _e: None
    try:
        return await init_node(
            state={"repo": _REPO, **state},
            config={
                "configurable": {
                    "sandbox_manager": sandbox_mgr,
                    "docker": docker,
                    "proxy_manager": proxy_mgr,
                    "embry0_config": SimpleNamespace(**settings),
                    "watcher_stacks_repo": _Stacks(stacks),
                }
            },
        )
    finally:
        nodes_module.get_stream_writer = orig


@pytest.mark.asyncio
async def test_init_node_scopes_log_access_to_the_evidence_services():
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    proxy_mgr.enroll_context = AsyncMock(return_value=["acme-backend"])
    update = await _run_init_with_config(
        {"job_id": "job-e1", "context": {"type": "none"}, "task": _EVIDENCE_TASK},
        sandbox_mgr,
        docker,
        proxy_mgr,
        developer_log_tool_enabled=True,
    )
    _, create_kwargs = sandbox_mgr.create.call_args
    assert create_kwargs["env"].get("EMBRY0_CONTEXT_PROXY_URL") == "http://context-proxy:9107"
    # Only the service the EVIDENCE names — not acme-frontend from "Impact".
    proxy_mgr.enroll_context.assert_awaited_once_with("container-abc", "a" * 43, containers=["acme-backend"])
    assert update["case_log_containers"] == ["acme-backend"]
    assert "watcher_log_tool" not in update
    evidence = update["case_evidence"]
    assert evidence["source"] == "ticket" and evidence["services"] == ["acme-backend"]
    assert "ops@example.com" not in " ".join(evidence["samples"])


@pytest.mark.asyncio
async def test_init_node_job_without_evidence_gets_no_log_access():
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    update = await _run_init_with_config(
        {"job_id": "job-e2", "context": {"type": "none"}, "task": "Add dark mode to acme-frontend"},
        sandbox_mgr,
        docker,
        proxy_mgr,
    )
    _, create_kwargs = sandbox_mgr.create.call_args
    assert "EMBRY0_CONTEXT_PROXY_URL" not in create_kwargs["env"]
    proxy_mgr.enroll_context.assert_not_awaited()
    assert "case_log_containers" not in update and "case_evidence" not in update


@pytest.mark.asyncio
async def test_init_node_evidence_naming_no_queryable_service_gets_no_log_access():
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    update = await _run_init_with_config(
        {"job_id": "job-e3", "context": {"type": "none"}, "task": "t\n\n## Evidence\nsome-other-box: boom\n"},
        sandbox_mgr,
        docker,
        proxy_mgr,
    )
    proxy_mgr.enroll_context.assert_not_awaited()
    assert "case_log_containers" not in update
    assert update["case_evidence"]["samples"] == ["some-other-box: boom"]  # prompts still get it


@pytest.mark.asyncio
async def test_init_node_developer_log_tool_is_off_unless_enabled():
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    update = await _run_init_with_config(
        {"job_id": "job-e4", "context": {"type": "none"}, "task": _EVIDENCE_TASK},
        sandbox_mgr,
        docker,
        proxy_mgr,
    )
    proxy_mgr.enroll_context.assert_not_awaited()
    assert "case_log_containers" not in update
    assert update["case_evidence"]["services"] == ["acme-backend"]


@pytest.mark.asyncio
async def test_init_node_case_services_without_a_failure_grant_nothing():
    """deploy_services names services without describing a failure; the tool
    needs a signature or sample lines too (this path goes live with #340)."""
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()

    async def _evidence(*_a, **_kw):
        return {
            "source": "case",
            "case_id": "hc-1",
            "signature": "",
            "samples": [],
            "services": ["acme-backend"],
        }

    with patch("embry0.workflows.issue_to_pr.nodes._init._job_case_evidence", new=_evidence):
        update = await _run_init_with_config(
            {"job_id": "job-e7", "context": {"type": "none"}, "task": "fix it"},
            sandbox_mgr,
            docker,
            proxy_mgr,
            developer_log_tool_enabled=True,
        )
    _, create_kwargs = sandbox_mgr.create.call_args
    assert "EMBRY0_CONTEXT_PROXY_URL" not in create_kwargs["env"]
    proxy_mgr.enroll_context.assert_not_awaited()
    assert "case_log_containers" not in update
    # The prompts still get what there is.
    assert update["case_evidence"]["services"] == ["acme-backend"]


@pytest.mark.asyncio
async def test_init_node_reports_a_scope_it_would_have_granted(capsys):
    """The flag is off by default, so the diagnostics must not be gated on it —
    an operator deciding whether to enable it has to be able to see what would
    happen, and to tell "no evidence" from "evidence names a renamed service"."""
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    update = await _run_init_with_config(
        {"job_id": "job-e8", "context": {"type": "none"}, "task": _EVIDENCE_TASK},
        sandbox_mgr,
        docker,
        proxy_mgr,
    )
    logged = capsys.readouterr().out
    assert "developer_log_scope_available" in logged and "acme-backend" in logged
    proxy_mgr.enroll_context.assert_not_awaited()
    assert "case_log_containers" not in update


@pytest.mark.asyncio
async def test_init_node_reports_evidence_that_names_no_queryable_service(capsys):
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    await _run_init_with_config(
        {"job_id": "job-e9", "context": {"type": "none"}, "task": "t\n\n## Evidence\nacme-quoting: boom\n"},
        sandbox_mgr,
        docker,
        proxy_mgr,
        developer_log_tool_enabled=True,
    )
    assert "developer_log_scope_empty" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_init_node_grant_fails_closed_without_a_config():
    """A config object that predates the flag must not grant it: the code-level
    getattr fallback is the last line of defence, so it is pinned.

    The config carries the allowlist but NOT the flag — with neither, an empty
    allowlist would fail the job closed for an unrelated reason and hide this.
    """
    from types import SimpleNamespace

    import embry0.workflows.issue_to_pr.nodes as nodes_module
    from embry0.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    legacy_config = SimpleNamespace(context_proxy_container_allowlist="acme-backend,acme-frontend")
    assert not hasattr(legacy_config, "developer_log_tool_enabled")
    orig = nodes_module.get_stream_writer
    nodes_module.get_stream_writer = lambda: lambda _e: None
    try:
        update = await init_node(
            state={"job_id": "job-e10", "repo": _REPO, "context": {"type": "none"}, "task": _EVIDENCE_TASK},
            config={
                "configurable": {
                    "sandbox_manager": sandbox_mgr,
                    "docker": docker,
                    "proxy_manager": proxy_mgr,
                    "embry0_config": legacy_config,
                    "watcher_stacks_repo": _Stacks(_STACKS),
                }
            },
        )
    finally:
        nodes_module.get_stream_writer = orig

    # The evidence still resolves — only the grant is withheld.
    assert update["case_evidence"]["services"] == ["acme-backend"]
    proxy_mgr.enroll_context.assert_not_awaited()
    assert "case_log_containers" not in update


@pytest.mark.asyncio
async def test_init_node_scoped_enroll_failure_degrades_without_the_tool():
    """A stale proxy image (scope not confirmed) or any enroll error: no tool, job continues."""
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    proxy_mgr.enroll_context = AsyncMock(side_effect=RuntimeError("did not confirm the container scope"))
    update = await _run_init_with_config(
        {"job_id": "job-e5", "context": {"type": "none"}, "task": _EVIDENCE_TASK},
        sandbox_mgr,
        docker,
        proxy_mgr,
        developer_log_tool_enabled=True,
    )
    assert "case_log_containers" not in update
    assert update.get("current_stage") == "initialized"
    assert "case_evidence" in update


@pytest.mark.asyncio
async def test_init_node_watcher_job_is_scoped_to_its_stack_s_containers():
    """The watcher's analysis job is enrolled with the containers the watcher chose, whatever its task quotes."""
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    update = await _run_init_with_config(
        {
            "job_id": "job-e6",
            "context": {"type": "none", "watcher_log_access": True, "watcher_log_containers": ["acme-frontend"]},
            "task": _EVIDENCE_TASK,
        },
        sandbox_mgr,
        docker,
        proxy_mgr,
    )
    proxy_mgr.enroll_context.assert_awaited_once_with("container-abc", "a" * 43, containers=["acme-frontend"])
    assert update.get("watcher_log_tool") is True
    assert "case_log_containers" not in update


@pytest.mark.asyncio
async def test_init_node_watcher_job_without_its_containers_gets_no_log_tool():
    """An analysis job made without the stack's containers is not given the proxy's whole allowlist instead."""
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    update = await _run_init(
        {"job_id": "job-w5", "context": {"type": "none", "watcher_log_access": True}}, sandbox_mgr, docker, proxy_mgr
    )
    _, create_kwargs = sandbox_mgr.create.call_args
    assert "EMBRY0_CONTEXT_PROXY_URL" not in create_kwargs["env"]
    proxy_mgr.enroll_context.assert_not_awaited()
    assert "watcher_log_tool" not in update


_ELSEWHERE_TASK = """Fix the checkout error

## Evidence

globex-api logged 3x: payment refused for order 12
"""


@pytest.mark.asyncio
async def test_init_node_evidence_naming_another_repository_s_container_grants_nothing():
    """A ticket's evidence names a container the deployment allowlists, but another repository's stack owns it: the
    job's log tool is not scoped to it, and the prompt does not name it as a service."""
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    update = await _run_init_with_config(
        {"job_id": "job-e11", "context": {"type": "none"}, "task": _ELSEWHERE_TASK},
        sandbox_mgr,
        docker,
        proxy_mgr,
        developer_log_tool_enabled=True,
        context_proxy_container_allowlist="acme-backend,acme-frontend,globex-api",
        stacks={_REPO: "acme-backend,acme-frontend", "globex/gizmos": "globex-api"},
    )
    proxy_mgr.enroll_context.assert_not_awaited()
    assert "case_log_containers" not in update
    assert update["case_evidence"]["services"] == [], update["case_evidence"]


@pytest.mark.parametrize(
    ("stacks", "why"),
    [
        ({}, "a repository nobody watches"),
        ({_REPO: None}, "a stack that names no containers of its own"),
        ({_REPO: "acme-frontend"}, "a stack that names other containers"),
        ({_REPO: "acme-backend-old"}, "an entry that is not a prefix"),
    ],
)
@pytest.mark.asyncio
async def test_init_node_evidence_grants_only_the_repository_s_own_containers(stacks, why):
    """The deployment's allowlist says which containers an agent may read at all, not whose they are: a job is scoped
    only to containers its own repository's stack names."""
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    update = await _run_init_with_config(
        {"job_id": "job-e12", "context": {"type": "none"}, "task": _EVIDENCE_TASK},
        sandbox_mgr,
        docker,
        proxy_mgr,
        developer_log_tool_enabled=True,
        stacks=stacks,
    )
    proxy_mgr.enroll_context.assert_not_awaited()
    assert "case_log_containers" not in update, why


@pytest.mark.asyncio
async def test_init_node_a_stack_pattern_admits_its_slot_names_the_deployment_admits():
    """A stack naming its slots by pattern admits the slot the evidence names, when the deployment admits it too."""
    task = "t\n\n## Evidence\nacme-backend-blue logged 2x: 502 Bad Gateway\n"
    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    proxy_mgr.enroll_context = AsyncMock(return_value=["acme-backend-blue"])
    await _run_init_with_config(
        {"job_id": "job-e13", "context": {"type": "none"}, "task": task},
        sandbox_mgr,
        docker,
        proxy_mgr,
        developer_log_tool_enabled=True,
        context_proxy_container_allowlist="acme-backend*",
        stacks={_REPO: "acme-backend*"},
    )
    proxy_mgr.enroll_context.assert_awaited_once_with("container-abc", "a" * 43, containers=["acme-backend-blue"])

    sandbox_mgr, docker, proxy_mgr = _watcher_init_mocks()
    update = await _run_init_with_config(
        {"job_id": "job-e14", "context": {"type": "none"}, "task": task},
        sandbox_mgr,
        docker,
        proxy_mgr,
        developer_log_tool_enabled=True,
        context_proxy_container_allowlist="acme-frontend",
        stacks={_REPO: "acme-backend*"},
    )
    proxy_mgr.enroll_context.assert_not_awaited()
    assert update["case_evidence"]["services"] == [], "a name the deployment does not admit is no service"


@pytest.mark.asyncio
async def test_init_node_no_profile_passes_none():
    """Without a job-create profile, create() receives profile=None (its
    internal default profile applies)."""
    from embry0.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value="")

    import embry0.workflows.issue_to_pr.nodes as nodes_module

    orig = nodes_module.get_stream_writer
    nodes_module.get_stream_writer = lambda: lambda _e: None
    try:
        await init_node(
            state={"job_id": "job-noprof", "repo": "org/repo"},
            config={
                "configurable": {
                    "sandbox_manager": sandbox_mgr,
                    "docker": docker,
                    "proxy_manager": MagicMock(git_proxy_url=""),
                }
            },
        )
    finally:
        nodes_module.get_stream_writer = orig

    _, create_kwargs = sandbox_mgr.create.call_args
    assert create_kwargs.get("profile") is None
