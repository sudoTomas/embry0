"""auth_provider — reserved env-var registry (the live surface of this module).

resolve_env was deleted in RAV-1240: it had no call sites — auth rides the
sandbox container env injected by SandboxManager, and credential availability
is validated by embry0.agents.resolver.resolve_agent_invocation.
"""

from embry0.execution.auth_provider import RESERVED_ENV_KEYS


def test_resolve_env_stays_deleted() -> None:
    # RAV-1240: the function was dead code whose docstring misdescribed the
    # auth flow. If someone reintroduces it, they must also reintroduce real
    # call sites — this guard forces that conversation.
    import embry0.execution.auth_provider as ap

    assert not hasattr(ap, "resolve_env")


def test_reserved_env_keys_export_expected_set() -> None:
    # These must NEVER be user-settable via the environment UI.
    expected = {
        "EMBRY0_GIT_PROXY_URL",
        # #166: base URL of the GitLab API proxy; orchestrator-owned.
        "EMBRY0_GITLAB_PROXY_URL",
        # EMB-45: base_url for the direct-xAI executor's proxy.
        "EMBRY0_XAI_PROXY_URL",
        # EMB-46: opt-in switch for the CLI-free direct-executor fallback.
        "EMBRY0_XAI_DIRECT_EXECUTOR",
        # RAV-1140: mediated log-query proxy URL for watcher analysis jobs.
        "EMBRY0_CONTEXT_PROXY_URL",
        # #240: re-enabling MCP tool-search deferral would strip agents of
        # their MCP tools (embry0 denies ToolSearch, so deferred = unreachable).
        "ENABLE_TOOL_SEARCH",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        # EMB-36: non-Anthropic provider keys are orchestrator-owned too.
        "XAI_API_KEY",
        "GITHUB_TOKEN",
        # #166: forge credentials reach the sandbox only via the proxies —
        # the same rule GITHUB_TOKEN already follows.
        "GITLAB_TOKEN",
        # QA-injected infrastructure (orchestrator owns these):
        "QA_JOB_ID",
        "QA_ATTEMPT_N",
        "QA_NETWORK_NAME",
        # storageState pre-authentication (EMB-40):
        "QA_STORAGE_STATE_PATH",
        "PLAYWRIGHT_MCP_STORAGE_STATE",
        "PLAYWRIGHT_MCP_ISOLATED",
        # DinD certs are mounted by the orchestrator for dind_enabled profiles:
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        # Spec 1A §3.2: the sandbox CA bundle path is orchestrator-owned —
        # a repo env override would re-point git at an attacker-chosen store.
        "GIT_SSL_CAINFO",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        # #425: git auth/config steering and curl trace flags.
        "GIT_SSH_COMMAND",
        "GIT_SSH",
        "GIT_ASKPASS",
        "SSH_ASKPASS",
        "GIT_TERMINAL_PROMPT",
        "GIT_CURL_VERBOSE",
        "GIT_TRACE_CURL",
        # #524: proxy routing, TLS, loader, binary lookup, shell and
        # interpreter startup hooks.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "FTP_PROXY",
        "NODE_TLS_REJECT_UNAUTHORIZED",
        "SSL_CERT_DIR",
        "PYTHONHTTPSVERIFY",
        "SSLKEYLOGFILE",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "PATH",
        "HOME",
        "XDG_CONFIG_HOME",
        "SHELL",
        "CURL_HOME",
        "BASH_ENV",
        "SHELLOPTS",
        "BASHOPTS",
        "PS4",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
        "PYTHONBREAKPOINT",
        "PERL5OPT",
        "PERL5LIB",
        "PERLLIB",
        "PERL5DB",
        "RUBYOPT",
        "RUBYLIB",
    }
    assert RESERVED_ENV_KEYS == expected


def test_reserved_env_prefixes_export_expected_tuple() -> None:
    from embry0.execution.auth_provider import RESERVED_ENV_PREFIXES

    # These prefixes must NEVER be user-settable via the environment UI.
    assert RESERVED_ENV_PREFIXES == ("QA_ARTIFACT_", "DOCKER_", "VIBIUM_", "GIT_CONFIG_", "EMBRY0_LLM_")


def test_hook_env_prefixes_export_expected_tuple() -> None:
    from embry0.execution.auth_provider import HOOK_ENV_PREFIXES

    # #524: git, the agent CLI and its runtime; Grok Build. Kept out of RESERVED_ENV_PREFIXES
    # so config_lint still reports CLAUDE_ typos in the orchestrator's own env.
    assert HOOK_ENV_PREFIXES == ("GIT_", "CLAUDE_", "BUN_", "GROK_")


def test_guarded_env_keys_export_expected_set() -> None:
    from embry0.execution.auth_provider import GUARDED_ENV_KEYS

    # #524: settable, but only to memory flags.
    assert set(GUARDED_ENV_KEYS) == {
        "NODE_OPTIONS",
        "JAVA_TOOL_OPTIONS",
        "_JAVA_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "JAVA_OPTS",
        "MAVEN_OPTS",
        "GRADLE_OPTS",
    }


def test_sandbox_ca_env_keys_are_all_reserved() -> None:
    """Every key SandboxManager injects for the CA bundle must be user-proof."""
    from embry0.execution.tls import sandbox_ca_env

    assert set(sandbox_ca_env()) <= RESERVED_ENV_KEYS


def test_grok_namespace_is_reserved() -> None:
    from embry0.execution.auth_provider import env_override_refusal

    # Grok Build reads GROK_* variables that redirect its traffic or load code
    # (GROK_AUTH_PROVIDER_COMMAND, GROK_CLI_CHAT_PROXY_BASE_URL, GROK_HOME, GROK_CONFIG,
    # GROK_MANAGED_CONFIG_URL); a sandbox that carries the binary must refuse them
    # from repo env vars, like CLAUDE_, GIT_ and BUN_.
    for name in ("GROK_AUTH_PROVIDER_COMMAND", "grok_home", "Grok_Config"):
        assert env_override_refusal(name, "x") is not None, f"{name} was accepted"
