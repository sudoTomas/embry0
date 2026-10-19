"""The wiring pin (spec §7): an optional argument nothing passes fails silently (#369)."""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path
from unittest.mock import MagicMock

import embry0.api.app as app_module
from embry0.services.credentials.resolver import CredentialResolver
from embry0.storage.repositories.credentials import CredentialsRepository


def _state():
    from embry0.api.app import _init_app_state, create_app

    app = create_app()
    app.state.secrets_provider = MagicMock()
    asyncio.run(_init_app_state(app=app, db=MagicMock(), database_url="postgresql://test/test", github_token="t" * 40))
    return app


def _src() -> str:
    return Path(inspect.getsourcefile(app_module)).read_text()


def _call_windows(src: str, pattern: str) -> list[tuple[int, str]]:
    """Every construction call matching ``pattern``, as (offset, text up to the matching close paren)."""
    windows = []
    for m in re.finditer(pattern, src):
        line_start = src.rfind("\n", 0, m.start()) + 1
        if src[line_start : m.start()].lstrip().startswith("class "):
            continue  # a class statement is not a construction
        depth, i = 0, m.end() - 1
        while i < len(src):  # find the matching close paren
            depth += src[i] == "("
            depth -= src[i] == ")"
            if depth == 0:
                break
            i += 1
        windows.append((m.start(), src[m.start() : i + 1]))
    return windows


def _top_level(window: str) -> str:
    """The call's own arguments: text nested in inner parentheses is blanked out, so a resolver
    passed to an inner ``GitHubClient(...)`` cannot satisfy the pin for the outer call."""
    out, depth = [], 0
    for ch in window:
        depth -= ch == ")"
        out.append(ch if depth <= 1 else " ")
        depth += ch == "("
    return "".join(out)


def test_resolver_and_repo_on_state_and_in_executor():
    app = _state()
    assert isinstance(app.state.credentials_repo, CredentialsRepository)
    assert isinstance(app.state.credential_resolver, CredentialResolver)
    assert app.state.issue_executor._credential_resolver is app.state.credential_resolver
    assert app.state.issue_executor._credentials_repo is app.state.credentials_repo
    assert app.state.github_sync._client._credential_resolver is app.state.credential_resolver


def test_init_app_state_reuses_objects_the_lifespan_built():
    """The lifespan builds the resolver before SandboxManager; _init_app_state must not replace it."""
    from embry0.api.app import _init_app_state, create_app

    app = create_app()
    app.state.secrets_provider = MagicMock()
    repo = CredentialsRepository(MagicMock())
    resolver = CredentialResolver(repo, app.state.secrets_provider, app.state.config)
    app.state.credentials_repo = repo
    app.state.credential_resolver = resolver
    asyncio.run(_init_app_state(app=app, db=MagicMock(), database_url="postgresql://test/test", github_token="t" * 40))
    assert app.state.credential_resolver is resolver
    assert app.state.issue_executor._credential_resolver is resolver
    assert app.state.issue_executor._credentials_repo is repo


def test_lifespan_passes_resolver_to_every_forge_consumer():
    """Source-level pin: every SandboxManager(, GitHubClient(, make_gitlab_client(, IssueExecutor(,
    GitHubSyncService(, CommentDispatchService(, WatcherService(, build_filer( and proxy_mgr.start(
    call in app.py passes the app.state resolver / has_*_credential, as its own argument. The lifespan needs
    Docker, so the construction sites are checked by reading the source, not by running it."""
    src = _src()
    resolver = "credential_resolver=app.state.credential_resolver"
    expectations = {
        # #522: without the registry no repo ever gets Docker (closed), so a
        # dropped argument breaks every granted repo's QA run, not security.
        # Spec 3.3: without both, no sandbox ever gets a skill source mounted,
        # and nothing says so: the manager constructs happily either way.
        r"SandboxManager\(": (
            resolver,
            "docker_access_registry=ReposRepository(",
            "skill_store=app.state.skill_store",
            "skill_sources_repo=SkillSourcesRepository(",
        ),
        r"GitHubClient\(": (resolver,),
        r"make_gitlab_client\(": (resolver,),
        r"IssueExecutor\(": (
            resolver,
            "credentials_repo=app.state.credentials_repo",
            "credential_health=app.state.credential_health",
        ),
        r"GitHubCommentChannel\(": ("token_resolver=",),
        r"GitHubSyncService\(": (resolver,),
        # #344: without the recorder a PR-comment follow-up is never linked
        # to its healing case, and nothing says so.
        r"CommentDispatchService\(": (resolver, "case_recorder=app.state.case_recorder"),
        r"WatcherService\(": (resolver,),
        r"build_filer\(": (resolver,),
    }
    for pattern, needles in expectations.items():
        windows = _call_windows(src, pattern)
        assert windows, f"no {pattern} construction found in app.py — the pin would measure nothing"
        for offset, window in windows:
            for needle in needles:
                assert needle in _top_level(window), f"{pattern} at offset {offset} lacks {needle}"
    [(_, start)] = _call_windows(src, r"await proxy_mgr\.start\(")
    assert "has_github_credential=has_github" in start and "has_gitlab_credential=has_gitlab" in start
    base = next(line for line in start.splitlines() if "gitlab_api_base=" in line)
    assert "if has_gitlab" in base and "gitlab_token" not in base, base


def test_gitlab_sync_is_gated_on_any_gitlab_credential():
    """A registry-only GitLab deployment must get its sync service, not only its proxy."""
    lines = _src().splitlines()
    idx = next(i for i, line in enumerate(lines) if "GitLabSyncService(" in line and "import" not in line)
    indent = len(lines[idx]) - len(lines[idx].lstrip())
    gate = next(
        line
        for line in reversed(lines[:idx])
        if line.strip().startswith("if ") and len(line) - len(line.lstrip()) < indent
    )
    assert "has_gitlab" in gate and "gitlab_token" not in gate, gate


def test_comment_channel_resolves_through_the_registry():
    (_, window) = _call_windows(_src(), r"GitHubCommentChannel\(")[0]
    assert re.search(r"forge_token\(\s*app\.state\.credential_resolver", window), window


def test_startup_registers_registry_secrets_with_the_scrubber():
    src = _src()
    call = "register_known_secrets(await app.state.credential_resolver.known_secret_values())"
    assert call in src
    assert src.index(call) > src.index("await _init_app_state("), "scrub set registered before _init_app_state"


def test_startup_registers_the_log_redaction_fields():
    """#294: the operator's PII field names reach the redactor before any job runs,
    and reach the context proxy, which redacts in a process of its own."""
    src = _src()
    call = "register_redacted_fields(config.log_redaction_fields)"
    assert call in src, "the orchestrator never registers LOG_REDACTION_FIELDS"
    assert src.index(call) > src.index("await _init_app_state("), "fields registered before _init_app_state"
    windows = [w for _, w in _call_windows(src, r"proxy_mgr\.start\(") if "enable_context_proxy" in w]
    assert windows, "no proxy_mgr.start(...) call launches the context proxy"
    for window in windows:
        assert "log_redaction_fields=config.log_redaction_fields" in _top_level(window), window


def test_orphan_sweep_stamps_credential_uses():
    """Jobs failed by the boot sweep never reach the executor's ledger stamp; the sweep must close them."""
    lines = _src().splitlines()
    idx = next(i for i, line in enumerate(lines) if "fail_orphaned(" in line)
    # 24: the call carries the recovery arguments since #307.
    following = "\n".join(lines[idx + 1 : idx + 24])
    assert "finish_uses" in following, "fail_orphaned is not followed by a credential_uses finish_uses stamp"


def test_orphan_sweep_emits_job_finished_and_no_synthetic_node_marker() -> None:
    """spec 3.5: the boot orphan path emits job_finished through the job-events core,
    not a hand-made node_completed(action="orphaned")."""
    source = _src()
    start = source.index("fail_orphaned(")
    window = source[start : start + 3000]
    assert "emit_job_finished(" in window, "the orphan sweep must emit job_finished for every orphaned job"
    assert 'action="orphaned"' not in source, "the synthetic node_completed marker is replaced by job_finished"


def test_case_close_reads_github_through_the_registry():
    """The /cases close route builds its own GitHubClient; it must carry the app's resolver."""
    from embry0.api.v1.cases import _github_issues

    resolver = object()
    state = MagicMock(spec=["config", "credential_resolver"])
    state.config = MagicMock(github_token="")
    state.credential_resolver = resolver
    request = MagicMock()
    request.app.state = state
    assert _github_issues(request)._credential_resolver is resolver


def _function_body(src: str, name: str) -> str:
    lines = src.splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.startswith(f"async def {name}(") or line.startswith(f"def {name}(")
    )
    body = [lines[start]]
    for line in lines[start + 1 :]:
        if line.startswith(("async def ", "def ", "class ")):
            break
        body.append(line)
    return "\n".join(body)


def test_xai_refresh_loop_uses_the_registry():
    """The loop must push every registered account. A file-backed ensure_fresh beside it
    rotates the same lineage and kills the row the registry just refreshed."""
    src = _src()
    loop = _function_body(src, "_xai_token_refresh_loop")
    initial = _function_body(src, "_push_initial_xai_token")
    assert "refresh_all()" in loop, "the refresh loop still drives the single file-backed token"
    assert "xai_proxy_health()" in loop, "the loop never reads per-credential 401/429 counts"
    # What the counts do is pinned by behaviour in tests/unit/api/test_xai_proxy_health.py.
    assert "_apply_xai_proxy_health(" in loop, "the loop reads the counts but never acts on them"
    assert "ensure_fresh" not in loop, "the loop must not rotate the file-backed lineage"
    assert "refresh_all()" in initial, "the initial push still drives the single file-backed token"
    assert "ensure_fresh" not in initial
    last_build = src.rindex("_build_credential_registry(app, db)")
    registry = src.index("XaiRefresherRegistry(")
    assert registry > last_build, "the registry is built before the credential repository exists"
    disabled = src.index("xai_proxy_disabled_credential_error")
    assert "xai_enabled = False" not in src[disabled : disabled + 400], (
        "a missing CLI credential file must not stop the proxy the registry accounts need"
    )


def test_the_skill_store_is_told_where_the_sandbox_daemon_sees_it():
    """Rootless: without daemon_root every skill bind names a path the host daemon does not have."""
    [(_, window)] = _call_windows(_src(), r"app\.state\.skill_store = SkillStore\(")
    assert "daemon_root=config.skills_store_daemon_path" in _top_level(window)
