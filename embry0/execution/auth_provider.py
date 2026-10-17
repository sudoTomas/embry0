"""Reserved credential env-var registry + auth configuration errors.

Single source of truth for which env vars are RESERVED (never
user-settable) per CLAUDE.md. ``env_override_refusal`` is the one check
behind both enforcement points: the EnvVarInput validator at the API and
the sandbox env injection filters.

Auth material itself does NOT flow through this module: SandboxManager
injects CLAUDE_CODE_OAUTH_TOKEN (and provider keys like XAI_API_KEY)
into the sandbox container env at create, the CLI subprocess inherits
it, and provider-routed agents get a per-agent env overlay inside the
executor. Credential *availability* is validated up front by
embry0.agents.resolver.resolve_agent_invocation, which raises the
AuthConfigError defined here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from embry0.safety.error_codes import ErrorCode

# Keys whose values are infrastructure-owned and must not be overridable
# by user repo-environment settings. See CLAUDE.md for rationale.
RESERVED_ENV_KEYS: Final[frozenset[str]] = frozenset(
    {
        "EMBRY0_GIT_PROXY_URL",
        # #166: base URL of the GitLab API proxy; orchestrator-owned.
        "EMBRY0_GITLAB_PROXY_URL",
        # EMB-45: base_url the direct-xAI executor points its Anthropic SDK client at;
        # orchestrator-owned, never user-settable.
        "EMBRY0_XAI_PROXY_URL",
        # EMB-46: opt-in switch routing grok to the CLI-free DirectXaiExecutor
        # fallback instead of the default SDK-over-proxy path. Executor choice
        # is infrastructure policy — never user-settable.
        "EMBRY0_XAI_DIRECT_EXECUTOR",
        # RAV-1140: base URL of the mediated log-query proxy; injected by
        # init_node for watcher analysis jobs only — never user-settable.
        "EMBRY0_CONTEXT_PROXY_URL",
        # #166: forge credentials are injected by the proxies, never by the
        # user's environment — the same rule GITHUB_TOKEN already follows.
        "GITLAB_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        # EMB-36: non-Anthropic provider keys are orchestrator-owned too.
        "XAI_API_KEY",
        "GITHUB_TOKEN",
        # #240: CLI harness behavior — a user value could re-enable MCP
        # tool-search deferral, silently stripping agents of their MCP
        # tools (embry0 denies ToolSearch, so deferred = unreachable).
        "ENABLE_TOOL_SEARCH",
        # QA-injected infrastructure (orchestrator owns these):
        "QA_JOB_ID",
        "QA_ATTEMPT_N",
        "QA_NETWORK_NAME",
        # storageState pre-authentication (EMB-40): both point the sandbox
        # (login command + playwright-mcp) at the orchestrator-chosen path.
        "QA_STORAGE_STATE_PATH",
        "PLAYWRIGHT_MCP_STORAGE_STATE",
        "PLAYWRIGHT_MCP_ISOLATED",
        # DinD certs are mounted by the orchestrator for dind_enabled profiles:
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        # Spec 1A §3.2: these point every in-sandbox TLS client at the merged
        # CA bundle SandboxManager installs. Orchestrator-owned — a repo env
        # override would silently re-point git at an attacker-chosen store,
        # or (more likely) break clones by pointing at a file that is not there.
        "GIT_SSL_CAINFO",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        # #425: the variables git reads to change HOW it authenticates or
        # which config it loads. A repo env var could detach the sandbox from
        # the credential proxy (GIT_CONFIG_GLOBAL=/dev/null), run an arbitrary
        # binary on every fetch (GIT_SSH_COMMAND), or dump the injected forge
        # token into stderr (GIT_TRACE_CURL=1).
        "GIT_SSH_COMMAND",
        "GIT_SSH",
        "GIT_ASKPASS",
        "SSH_ASKPASS",
        "GIT_TERMINAL_PROMPT",
        "GIT_CURL_VERBOSE",
        "GIT_TRACE_CURL",
        # #524: every process in a sandbox inherits the agent's LLM credential,
        # and git sends the sandbox's proxy bearer over plain http to
        # git-proxy. The variables below redirect that traffic or run code
        # inside those processes, outside the command blocklist.
        #
        # Proxy routing: git and curl honour http_proxy and ALL_PROXY, so that
        # plaintext bearer would go through the chosen proxy.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "FTP_PROXY",
        # TLS: siblings of the CA variables above. They turn verification off
        # or log session keys instead of re-pointing the store.
        "NODE_TLS_REJECT_UNAUTHORIZED",
        "SSL_CERT_DIR",
        "PYTHONHTTPSVERIFY",
        "SSLKEYLOGFILE",
        # Dynamic loader: a library loaded into every dynamically linked
        # process, the agent CLI included.
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        # Which binary runs, and which per-user config it reads. The runner,
        # git and the CLI are found on PATH; HOME and XDG_CONFIG_HOME hold
        # git's proxy config (~/.gitconfig) and the CLI's settings; the CLI
        # runs its Bash tool through SHELL; curl reads CURL_HOME/.curlrc.
        "PATH",
        "HOME",
        "XDG_CONFIG_HOME",
        "SHELL",
        "CURL_HOME",
        # Shell startup: bash sources BASH_ENV before every non-interactive
        # script; SHELLOPTS=xtrace prints each expanded command, tokens
        # included; PS4 is evaluated on every traced line.
        "BASH_ENV",
        "SHELLOPTS",
        "BASHOPTS",
        "PS4",
        # Interpreter startup: module search paths and startup hooks. The
        # sandbox runner is `python -m embry0.sandbox.runner`, found through
        # the image's PYTHONPATH, so a repo value would replace or shadow it.
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
)


# Reserved prefixes — every key starting with these is server-controlled.
# Used by EnvVarInput key validator and by the sandbox env injection filter.
RESERVED_ENV_PREFIXES: Final[tuple[str, ...]] = (
    "QA_ARTIFACT_",  # presigned URLs minted per-attempt by init_qa
    "DOCKER_",  # broad — covers DOCKER_HOST etc. without enumerating
    # #233: the vibium browser backend's runtime knobs (CHROME_ARGS,
    # CACHE_DIR, SESSION, CONNECT_URL/_API_KEY, ENGINE_*) are
    # orchestrator/image-owned — a user value could weaken the browser
    # sandbox or point the MCP server at an attacker's remote browser.
    "VIBIUM_",
    # #425: GIT_CONFIG_GLOBAL/SYSTEM/COUNT/KEY_n/VALUE_n all override ~/.gitconfig,
    # where the sandbox's git proxy config lives.
    "GIT_CONFIG_",
)


# #524: whole namespaces of the tools that hold or fetch credentials in a
# sandbox. Listing their hooks one at a time has already missed some twice
# (#425, #524), so the namespace is reserved instead:
#   GIT_    - GIT_DIR, GIT_EXEC_PATH, GIT_TEMPLATE_DIR (hooks run on clone),
#             GIT_EXTERNAL_DIFF, GIT_PROXY_COMMAND, GIT_SSL_NO_VERIFY, GIT_TRACE*.
#   CLAUDE_ - the agent CLI: CLAUDE_CODE_*, CLAUDE_CONFIG_DIR, and CLAUDE_ENV_FILE
#             (a file the CLI loads into its Bash tool's environment).
#   BUN_    - the agent CLI's runtime: BUN_OPTIONS, and BUN_CONFIG_VERBOSE_FETCH,
#             which logs the CLI's requests.
#   GROK_   - Grok Build: auth provider command, endpoint overrides, home/config paths.
# Kept apart from RESERVED_ENV_PREFIXES: config_lint reads that tuple as "the
# orchestrator injects this family, so skip typo checks", and a CLAUDE_ typo
# in the orchestrator's own env must still be reported.
HOOK_ENV_PREFIXES: Final[tuple[str, ...]] = ("GIT_", "CLAUDE_", "BUN_", "GROK_")


@dataclass(frozen=True)
class EnvValueGuard:
    """A variable a repo may set, but only to whitespace-separated flags from a fixed set."""

    flag: re.Pattern[str]
    allowed: str  # human-readable list of the flags `flag` accepts, for the refusal message

    def accepts(self, value: str) -> bool:
        return all(self.flag.fullmatch(token) for token in value.split())


_JVM_SIZE = r"\d+[kKmMgGtT]?"
_JVM_MEMORY_FLAGS = EnvValueGuard(
    flag=re.compile(
        rf"-X(?:mx|ms|ss|mn){_JVM_SIZE}"
        r"|-XX:(?:Max|Initial|Min)RAMPercentage=\d+(?:\.\d+)?"
        rf"|-XX:(?:MaxMetaspaceSize|MetaspaceSize|ReservedCodeCacheSize|MaxDirectMemorySize)={_JVM_SIZE}"
    ),
    allowed=(
        "JVM memory flags (-Xmx, -Xms, -Xss, -Xmn, -XX:Max/Initial/MinRAMPercentage, "
        "-XX:MaxMetaspaceSize, -XX:MetaspaceSize, -XX:ReservedCodeCacheSize, -XX:MaxDirectMemorySize)"
    ),
)

# #524: variables with a real repo use (heap sizing for large builds) whose
# other flags load code into every process that reads them: --require and
# --import in NODE_OPTIONS; -javaagent and -XX:OnOutOfMemoryError in the JVM
# option variables (JAVA_OPTS, MAVEN_OPTS and GRADLE_OPTS reach the JVM
# through the mvnw/gradlew launch scripts). Only memory flags pass.
GUARDED_ENV_KEYS: Final[Mapping[str, EnvValueGuard]] = MappingProxyType(
    {
        "NODE_OPTIONS": EnvValueGuard(
            flag=re.compile(r"--max[-_](?:old|semi)[-_]space[-_]size=\d+"),
            allowed="Node heap flags (--max-old-space-size=<MiB>, --max-semi-space-size=<MiB>)",
        ),
        "JAVA_TOOL_OPTIONS": _JVM_MEMORY_FLAGS,
        "_JAVA_OPTIONS": _JVM_MEMORY_FLAGS,
        "JDK_JAVA_OPTIONS": _JVM_MEMORY_FLAGS,
        "JAVA_OPTS": _JVM_MEMORY_FLAGS,
        "MAVEN_OPTS": _JVM_MEMORY_FLAGS,
        "GRADLE_OPTS": _JVM_MEMORY_FLAGS,
    }
)


def env_key_refusal(key: str) -> str | None:
    """Why ``key`` can never be user-set whatever its value, or None.

    Case-insensitive: ``http_proxy`` is the spelling curl honours, and
    ``github_token`` must not slip past an exact-match check. The message
    names the key, never the reserved set (RAV-1070: a refusal is not an
    inventory).
    """
    normalized = key.strip().upper()
    if normalized in RESERVED_ENV_KEYS:
        return f"Key {key!r} is reserved for embry0 infrastructure."
    for prefix in RESERVED_ENV_PREFIXES + HOOK_ENV_PREFIXES:
        if normalized.startswith(prefix):
            return f"Key {key!r} starts with reserved prefix {prefix!r}; that family is controlled by embry0."
    return None


def env_value_refusal(key: str, value: str) -> str | None:
    """Why ``value`` is not allowed for a guarded ``key``, or None.

    The message never contains the value, which may be a secret.
    """
    guard = GUARDED_ENV_KEYS.get(key.strip().upper())
    if guard is not None and not guard.accepts(value):
        return f"Key {key!r} may only carry {guard.allowed}."
    return None


def env_override_refusal(key: str, value: str) -> str | None:
    """Why a user-supplied ``key=value`` must not reach a sandbox, or None if it may.

    The one check behind every enforcement point, so they cannot drift: the
    sandbox injection filters call it, and the EnvVarInput validators call
    its two halves, one per field.
    """
    return env_key_refusal(key) or env_value_refusal(key, value)


class AuthConfigError(ValueError):
    """Raised when the requested auth configuration is incoherent or missing
    required credentials. The .error_code attribute carries the canonical code.
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.error_code = code
