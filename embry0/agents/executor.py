"""AgentExecutor — the protocol both SDK and CLI executors implement."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from embry0.agents._common import make_scrubber
from embry0.agents.claude_cli_session import find_session_file, restored_cost_usd
from embry0.agents.config_builder import build_sdk_options
from embry0.agents.invocation import AgentInvocation
from embry0.agents.output_text import FinalText, tail_on_line_boundary
from embry0.agents.provider_limits import (
    ProviderFailure,
    failure_from_rate_limit_info,
    failure_from_text,
)
from embry0.agents.provider_limits import merge as merge_provider_failure
from embry0.agents.providers import price_usage
from embry0.agents.sdk_support import SdkSupportError, assert_sdk_supports_hooks
from embry0.agents.session import render_transcript_block
from embry0.execution import events
from embry0.execution.agent_runner import AgentOutput
from embry0.safety.error_codes import ACCOUNT_FAILURE_CODES, ErrorCode
from embry0.safety.policy import SafetyPolicy, evaluate_policy, render_settings_json

if TYPE_CHECKING:
    from langchain_core.runnables.config import RunnableConfig

logger = structlog.get_logger(__name__)


class AgentExecutor(Protocol):
    async def run(
        self,
        invocation: AgentInvocation,
        config: RunnableConfig,
        *,
        resume_session_id: str | None = None,
        resume_messages: list[dict[str, Any]] | None = None,
    ) -> AgentOutput: ...


_ASK_USER_MARKER = '{"type": "agent_ask_user"'
_MAX_EMBEDDED_ASK_EVENTS = 5


def _extract_embedded_ask_user_events(text: str) -> list[dict[str, Any]]:
    """Recover agent_ask_user events embedded in a tool result (EMB-44).

    The in-sandbox ask_user helper prints one compact-JSON event per call to
    the Bash tool's stdout; by the time it reaches us it is a substring of
    the tool-result content (possibly inside a repr'd block list), so decode
    from each marker with a raw JSON decoder rather than splitting lines.
    Capped defensively — one tool call asking dozens of questions is noise,
    and the ask-user round cap governs the real limit downstream.
    """
    events: list[dict[str, Any]] = []
    idx = 0
    decoder = json.JSONDecoder()
    while len(events) < _MAX_EMBEDDED_ASK_EVENTS:
        pos = text.find(_ASK_USER_MARKER, idx)
        if pos == -1:
            break
        try:
            obj, consumed = decoder.raw_decode(text[pos:])
        except ValueError:
            idx = pos + 1
            continue
        if isinstance(obj, dict) and obj.get("question"):
            events.append(obj)
        idx = pos + consumed
    return events


_ASSISTANT_ERROR_CODES = {
    "rate_limit": ErrorCode.PROVIDER_RATE_LIMITED,
    "billing_error": ErrorCode.PROVIDER_QUOTA_EXHAUSTED,
    "authentication_failed": ErrorCode.AUTH_REJECTED,
}
_API_STATUS_CODES = {
    429: ErrorCode.PROVIDER_RATE_LIMITED,
    401: ErrorCode.AUTH_REJECTED,
    403: ErrorCode.AUTH_REJECTED,
}


def _message_text(message: Any) -> str:
    """The text blocks of an assistant message, joined. Duck-typed like the
    stream loop, so test fakes and every SDK version read the same."""
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return ""
    parts = [block.text for block in content if isinstance(getattr(block, "text", None), str)]
    return "\n".join(part for part in parts if part)


def _sdk_provider_failure(message: Any, *, now: datetime) -> ProviderFailure | None:
    """The provider failure one SDK message reports, or None.

    Reads fields that SDK <0.2 may lack, every one with ``getattr``. A message
    without them is not a failure, even when its result text mentions a rate
    limit. The provider's own text is kept: on a spent usage window it is the
    only place that says so, and says until when (#466).
    """
    info = getattr(message, "rate_limit_info", None)
    if info is not None:
        return failure_from_rate_limit_info(info)
    assistant_error = getattr(message, "error", None)
    if isinstance(assistant_error, str) and assistant_error in _ASSISTANT_ERROR_CODES:
        return failure_from_text(
            _message_text(message),
            code=_ASSISTANT_ERROR_CODES[assistant_error].value,
            now=now,
            fallback_text=assistant_error,
        )
    if not getattr(message, "is_error", False):
        return None
    parts: list[str] = []
    errors = getattr(message, "errors", None)
    if isinstance(errors, list):
        parts.extend(str(item) for item in errors if item)
    result_text = getattr(message, "result", None)
    if isinstance(result_text, str) and result_text:
        parts.append(result_text)
    api_status = getattr(message, "api_error_status", None)
    status_code = _API_STATUS_CODES.get(api_status) if api_status is not None else None
    return failure_from_text(
        "\n".join(parts),
        code=status_code.value if status_code is not None else None,
        now=now,
    )


# The Python SDK (sandbox pin and host query.py) builds its closing exception
# from errors[] and, when that list is empty, from the result subtype. An API
# failure is subtype "success" with the text in ``result`` (and often
# ``api_error_status``) — the TypeScript SDK reports that text; the Python SDK
# reports the subtype, which reads as "error result: success".
_SDK_ERROR_RESULT_PREFIX = "Claude Code returned an error result:"
# SDK 0.2 raises that text as a ResultError, a ProcessError subclass, which
# appends " (exit code: N)". Its text is errors[], else result, else a
# non-success subtype, else "API error (HTTP n)", else "unknown error", so an
# error_during_execution with no text still names only its subtype (#501).
_SDK_EXIT_CODE_SUFFIX = re.compile(r"\s*\(exit code: -?\d+\)\Z")


@dataclass(frozen=True)
class _CliErrorResult:
    """The CLI's own payload on an ``is_error`` result, distinct from the subtype label."""

    subtype: str | None
    diagnostic: str
    api_error_status: int | None = None


def _is_subtype_only_sdk_error(error_message: str, subtype: str | None) -> bool:
    """True when the SDK's closing exception names only the result subtype.

    That label is not the failure. ``success`` is the subtype of a turn that
    completed on an API error; it must not be read as a successful run.
    """
    if not error_message.startswith(_SDK_ERROR_RESULT_PREFIX):
        return False
    suffix = _SDK_EXIT_CODE_SUFFIX.sub("", error_message[len(_SDK_ERROR_RESULT_PREFIX) :]).strip()
    if subtype and suffix == subtype:
        return True
    return suffix in {"success", "unknown error"}


def _cli_error_result(message: Any) -> _CliErrorResult | None:
    """The result/errors/api_error_status text of an is_error result, or None.

    A message without ``is_error`` is not a failure, even when its subtype is
    ``success``. Duck-typed like the stream loop so test fakes and every SDK
    version read the same. The subtype itself is never the diagnostic: that is
    the Python SDK's empty-errors fallback.
    """
    if not getattr(message, "is_error", False):
        return None
    if not (
        hasattr(message, "subtype")
        or hasattr(message, "total_cost_usd")
        or hasattr(message, "num_turns")
        or hasattr(message, "api_error_status")
    ):
        return None
    raw_subtype = getattr(message, "subtype", None)
    subtype = raw_subtype.strip() if isinstance(raw_subtype, str) and raw_subtype.strip() else None
    errors = getattr(message, "errors", None)
    error_items = (
        [str(item).strip() for item in errors if item and str(item).strip()] if isinstance(errors, list) else []
    )
    raw_result = getattr(message, "result", None)
    result_text = raw_result.strip() if isinstance(raw_result, str) else ""
    status = getattr(message, "api_error_status", None)
    status_code = status if isinstance(status, int) and not isinstance(status, bool) else None

    # TS SDK: subtype success carries the error text in result; other subtypes
    # carry it in errors[]. Fall back to whichever side actually has text.
    ordered = [result_text, *error_items] if subtype == "success" else [*error_items, result_text]
    chunks: list[str] = []
    for item in ordered:
        if item and item != subtype and item not in chunks:
            chunks.append(item)
    body = "; ".join(chunks)
    if status_code is not None and str(status_code) not in body:
        note = f"api_error_status={status_code}"
        body = f"{body}; {note}" if body else note
    return _CliErrorResult(subtype, body, status_code)


def _resumed_session_cost(session_id: str, cwd: str | Path | None) -> float:
    """The session cost the CLI will restore when it resumes ``session_id``."""
    session_file = find_session_file(
        home_dir=Path(os.path.expanduser("~")),
        session_id=session_id,
        project_cwd=str(cwd) if cwd else None,
    )
    return restored_cost_usd(session_file) if session_file is not None else 0.0


def _workspace_root() -> Path:
    """Return the workspace root. Respects EMBRY0_WORKSPACE_ROOT for tests."""
    return Path(os.environ.get("EMBRY0_WORKSPACE_ROOT", "/workspace"))


def _resolve_writer(config: dict[str, Any] | None) -> Callable[[dict[str, Any]], None]:
    """Extract the stream writer from a RunnableConfig.

    Prefers langgraph.config.get_stream_writer() when present; falls back to
    a _test_writer key for unit tests; falls back to a no-op otherwise.
    """
    if config and "_test_writer" in config:
        writer_fn: Callable[[dict[str, Any]], None] = config["_test_writer"]
        return writer_fn
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        return writer
    except Exception:
        return lambda _e: None


def _ids(invocation: AgentInvocation) -> dict[str, str]:
    """The correlation fields every executor event carries."""
    return {
        "run_id": invocation.run_id,
        "node": invocation.node or invocation.agent_type,
        "agent": invocation.agent_type,
    }


def _render_tool_result(content: Any) -> str:
    """Tool result content as text. String stays; a block list joins its text
    parts with newlines and replaces an image part by ``[image]``."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if kind == "image":
                parts.append("[image]")
            else:
                text_value = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
                parts.append(str(text_value if text_value is not None else block))
        return "\n".join(parts)
    return str(content)


def _summarize_tool_input(tool_name: str, tool_input: dict[str, Any]) -> str:
    if not isinstance(tool_input, dict):
        return str(tool_input)[:200]
    if tool_name in ("Read", "Glob", "Grep"):
        result = tool_input.get("file_path", "") or tool_input.get("path", "") or tool_input.get("pattern", "")
        return str(result)
    if tool_name in ("Write", "Edit"):
        return str(tool_input.get("file_path", ""))
    if tool_name == "Bash":
        return str(tool_input.get("command", ""))[:200]
    return str(tool_input)[:200]


def _extract_hook_call(hook_input: Any) -> tuple[str, dict[str, Any]]:
    """Pull (tool_name, tool_input) out of a PreToolUse hook payload.

    The SDK's PreToolUseHookInput is a TypedDict — a plain dict at runtime —
    so the previous getattr()-based extraction ALWAYS returned ''/{}: the
    tool-scoped content rules never matched, and once EMB-37's name check
    landed, every call was denied with "tool '' is not in this agent's
    allowlist" (job-616393868ba6). Handle dict first; keep the attribute
    path for any object-shaped input a future SDK might pass. Malformed
    input degrades to ''/{} — with a non-empty allowlist that fails CLOSED
    (the name check denies ''), which is the correct failure direction for
    a safety hook.
    """
    if isinstance(hook_input, dict):
        raw_name = hook_input.get("tool_name")
        raw_input = hook_input.get("tool_input")
    else:
        raw_name = getattr(hook_input, "tool_name", None)
        raw_input = getattr(hook_input, "tool_input", None)
    name = raw_name if isinstance(raw_name, str) else ""
    tool_input = raw_input if isinstance(raw_input, dict) else {}
    return name, tool_input


def _denial_guidance(reason: str, attempts: int) -> str:
    """Turn a bare policy reason into something the agent can act on.

    The hook already returns ``verdict.reason``, but it reads like a transient
    error: nothing in "Blocked by safety policy: dangerous bash pattern" tells
    the agent the block is a fixed property of the sandbox. So it re-tries with
    different quoting, a wrapper script, a different flag — each one denied,
    each one costing a turn — until the cap kills the run with the work
    unfinished. Both jobs behind #197 died exactly that way.

    Say plainly that retrying cannot work, and escalate once it is clearly not
    landing. ``attempts`` is this reason's running count including the current
    denial.
    """
    if attempts <= 1:
        return (
            f"{reason}\n\n"
            "This is a fixed restriction of this sandbox, not a transient failure. "
            "Retrying the same tool — with different arguments, quoting, or via a wrapper — "
            "will be denied again. Use another approach, or continue without this step and "
            "note it in your report."
        )
    return (
        f"{reason}\n\n"
        f"You have now been denied this {attempts} times; it will not succeed on any attempt. "
        "Stop retrying it. Pick a different approach, and if the task genuinely cannot be "
        "completed without it, finish everything you can, preserve your work, and report the "
        "blocker rather than spending more turns on it."
    )


async def _evaluate_hook(
    policy: SafetyPolicy,
    tool_name: str,
    tool_input: dict[str, Any],
    tools_called: dict[str, int],  # noqa: ARG001 — reserved for future per-tool counters
    writer: Callable[[dict[str, Any]], None],
    policy_denials: dict[str, int] | None = None,
    *,
    ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Ring-3 PreToolUse hook — delegates to evaluate_policy, fail-closed.

    Returns the SDK's PreToolUseHookSpecificOutput shape:
    ``{"hookSpecificOutput": {"hookEventName": "PreToolUse",
    "permissionDecision": "allow"|"deny", "permissionDecisionReason": "..."}}``.

    The legacy top-level ``{"decision": "allow"|"deny"}`` shape that earlier
    code returned is REJECTED by the Claude CLI's stdin Zod validator
    (the schema only accepts ``decision: "block"`` at top level — observed
    on CLI 2.1.92 with claude-agent-sdk 0.1.5x). Using the newer
    ``hookSpecificOutput.permissionDecision`` form is forward-compatible.

    Exposed at module level so unit tests can exercise the deny path directly
    (a fake query() mock doesn't invoke SDK hooks, so the only way to test this
    path is to call this function directly).
    """
    if not isinstance(tool_input, dict):
        tool_input = {}
    verdict = evaluate_policy(policy, tool_name, tool_input)
    if not verdict.allowed:
        # Count it as well as emit it. A denial that only exists as an event is
        # invisible to every consumer that reads state — including the pause
        # summary an operator actually sees. An agent can spend its whole turn
        # budget retrying one permanently-blocked tool and the run looks, from
        # the outside, like it simply ran long.
        attempts = 1
        if policy_denials is not None and verdict.reason:
            attempts = policy_denials.get(verdict.reason, 0) + 1
            policy_denials[verdict.reason] = attempts
        # The counter and the event stay keyed on the raw reason so counts do
        # not fragment and existing consumers are unaffected; only the text the
        # agent reads gains the guidance.
        writer(
            events.error(
                message=verdict.reason or "",
                code=ErrorCode.TOOL_DENIED.value,
                tool_name=tool_name,
                **(ids or {}),
            )
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": _denial_guidance(verdict.reason or "", attempts),
            }
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


class SdkAgentExecutor:
    """SDK-based agent executor.

    Responsibilities:
    - Render invocation → ClaudeAgentOptions via config_builder.
    - Write Ring-2 settings.json into <workspace>/.claude/settings.json.
    - Register Ring-3 PreToolUse hook that delegates to evaluate_policy.
    - Stream SDK messages through the writer as embry0 events.
    - Aggregate into AgentOutput.

    Does NOT manage the sandbox container — that is the caller's concern.
    """

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        # The "now" a provider's reset ("resets 12:50am (UTC)") is read
        # against. Injectable so the parse is deterministic under test.
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))

    async def run(
        self,
        invocation: AgentInvocation,
        config: RunnableConfig | dict[str, Any] | None = None,
        *,
        resume_session_id: str | None = None,
        resume_messages: list[dict[str, Any]] | None = None,
    ) -> AgentOutput:
        # #497: the Ring-3 hook is enforced here, by the sandbox's SDK, so its
        # support is checked here and not by the orchestrator. Fails closed:
        # an SDK that cannot carry the hook never reaches query().
        try:
            assert_sdk_supports_hooks()
        except SdkSupportError as exc:
            return AgentOutput(
                agent_type=invocation.agent_type,
                is_error=True,
                error_message=str(exc),
                error_code=ErrorCode.SANDBOX_INIT.value,
            )

        from claude_agent_sdk import (  # local import; SDK is optional in some contexts
            AssistantMessage,
            HookContext,
            HookMatcher,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
            query,
        )
        from claude_agent_sdk.types import HookInput, HookJSONOutput

        try:
            from claude_agent_sdk import ThinkingBlock
        except ImportError:
            ThinkingBlock = None  # type: ignore[assignment,misc]

        # Duck-type message/block detection so tests can swap in fakes with
        # equivalent structure. Real SDK classes satisfy the same shape.
        def _is_assistant(msg: Any) -> bool:
            if isinstance(msg, AssistantMessage):
                return True
            return hasattr(msg, "content") and hasattr(msg, "model") and not hasattr(msg, "total_cost_usd")

        def _is_result(msg: Any) -> bool:
            if isinstance(msg, ResultMessage):
                return True
            return hasattr(msg, "total_cost_usd") or (hasattr(msg, "result") and hasattr(msg, "num_turns"))

        def _is_text_block(blk: Any) -> bool:
            if isinstance(blk, TextBlock):
                return True
            return hasattr(blk, "text") and not hasattr(blk, "name") and not hasattr(blk, "input")

        def _is_tool_use_block(blk: Any) -> bool:
            if isinstance(blk, ToolUseBlock):
                return True
            # Duck-type: has name, id, and input — but not the text attr of a TextBlock.
            return hasattr(blk, "name") and hasattr(blk, "id") and hasattr(blk, "input") and not hasattr(blk, "text")

        writer = _resolve_writer(config)  # type: ignore[arg-type]
        ids = _ids(invocation)
        scrub = make_scrubber()
        now = time.time()
        tools_called: dict[str, int] = {}
        final_text = FinalText()
        result_fallback = ""
        cost_usd = 0.0
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_creation_tokens = 0
        is_error = False
        error_message = ""
        error_code: str | None = None
        # #466: every provider signal of this run, merged. Applied after the
        # stream ends, because the SDK's closing exception ("Claude Code
        # returned an error result: success") would otherwise replace the
        # provider's own sentence.
        provider_failure: ProviderFailure | None = None
        # The CLI's own is_error payload (result/errors/api_error_status). The
        # Python SDK's closing exception names only the subtype when errors[]
        # is empty; this is what that label discarded.
        cli_error: _CliErrorResult | None = None

        # --- Plan C closeout: per-mode conversation-state capture.
        #
        # api_key mode: build a [{role, content}] messages list as we go —
        #   the SDK's `query()` API does not expose its internal messages
        #   buffer publicly, so we accumulate from the prompt + each
        #   AssistantMessage's text blocks. This is sufficient for the
        #   anthropic_api / Messages-API resume path: AgentSession.messages
        #   feeds straight back as the next prompt's prior context.
        #
        # claude_max (oauth) mode: snapshot the SDK-emitted session_id from
        #   any message that carries one (AssistantMessage.session_id or
        #   ResultMessage.session_id) and emit the in-sandbox path discovered
        #   by claude_cli_session.find_session_file (typically
        #   ~/.claude/projects/<sanitized-cwd>/<id>.jsonl) as ``session_blob_path``.
        #   The orchestrator-side AgentRunner ``docker cp``s the bytes out
        #   before the sandbox is destroyed; see embry0/execution/agent_runner.py.
        captured_messages: list[dict[str, Any]] = []
        captured_session_id: str | None = None

        # --- Ring 2: write settings.json
        ws = _workspace_root()
        claude_dir = ws / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "settings.json").write_text(json.dumps(render_settings_json(invocation.safety_policy), indent=2))

        # --- Build SDK options
        options = build_sdk_options(invocation)

        # --- EMB-36/EMB-46: non-Anthropic provider overlay. The CLI honors
        # ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN/ANTHROPIC_API_KEY, so a
        # compat backend (xAI grok) is per-agent env configuration on the CLI
        # subprocess. Precedence:
        #   1. EMB-46 subscription path — the xai-proxy is live and the
        #      per-sandbox bearer file is present: point the CLI at the proxy
        #      with Bearer auth. The proxy swaps in the rotating SuperGrok
        #      token at egress; no Max OAuth, no console key on this path.
        #   2. EMB-36 console-key fallback — proxy down: base_url straight to
        #      the provider with its API key from the sandbox container env.
        # Both strip CLAUDE_CODE_OAUTH_TOKEN so the grok agent can never ride
        # the Anthropic subscription. Missing credentials fail closed before
        # any tokens are spent.
        if invocation.provider:
            from embry0.agents.providers import PROVIDERS
            from embry0.sandbox.xai_token import read_xai_proxy_token

            prov = next((pr for pr in PROVIDERS if pr.name == invocation.provider), None)
            if prov is None:
                return AgentOutput(
                    agent_type=invocation.agent_type,
                    is_error=True,
                    error_message=f"unknown model provider: {invocation.provider!r}",
                )
            proxy_url = os.environ.get("EMBRY0_XAI_PROXY_URL", "") if invocation.provider == "xai" else ""
            proxy_token = read_xai_proxy_token() if proxy_url else ""
            if proxy_url and proxy_token:
                # Merge over the base env from build_sdk_options — replacing
                # would drop the #240 tool-search kill-switch on provider runs.
                options.env = {
                    **(options.env or {}),
                    "ANTHROPIC_BASE_URL": proxy_url,
                    "ANTHROPIC_AUTH_TOKEN": proxy_token,
                    "ANTHROPIC_API_KEY": "",
                    "CLAUDE_CODE_OAUTH_TOKEN": "",
                }
            else:
                provider_key = os.environ.get(prov.api_key_env, "")
                if not provider_key:
                    cred_hint = (
                        f"the xai-proxy (bearer file + EMBRY0_XAI_PROXY_URL) or {prov.api_key_env}"
                        if invocation.provider == "xai"
                        else prov.api_key_env
                    )
                    return AgentOutput(
                        agent_type=invocation.agent_type,
                        is_error=True,
                        error_message=(
                            f"model {invocation.model!r} requires {cred_hint} in the "
                            "orchestrator environment (injected into the sandbox at create)"
                        ),
                    )
                options.env = {
                    **(options.env or {}),
                    "ANTHROPIC_BASE_URL": prov.base_url,
                    "ANTHROPIC_API_KEY": provider_key,
                    "ANTHROPIC_AUTH_TOKEN": "",
                    "CLAUDE_CODE_OAUTH_TOKEN": "",
                }

        # --- EMB-35 session resume, in precedence order:
        # 1. ``resume_session_id`` → the CLI's own file-based resume. The
        #    session JSONL was staged to the canonical path by AgentRunner
        #    before this process started; the CLI reloads the full prior
        #    conversation (tool_use/thinking included) and its internal
        #    prompt caching applies. Works in BOTH auth modes — the session
        #    file is a CLI artifact, independent of how auth is provided.
        # 2. ``resume_messages`` → text-transcript replay fallback for
        #    sessions that only have a messages list (legacy rows, oversized
        #    or missing blob). The bounded transcript is prepended to the
        #    prompt; lossier and re-billed as input, hence fallback-only.
        resumed_cost_usd = 0.0
        if resume_session_id:
            options.resume = resume_session_id
            # The CLI (2.1.277+) restores a resumed session's saved cost, so
            # the result's total_cost_usd then includes every earlier run of
            # the session, which the job total already counted. This run's
            # cost is that total minus what the CLI restored (#501).
            resumed_cost_usd = _resumed_session_cost(resume_session_id, options.cwd)

        # Denials the safety policy blocked this run, keyed by reason. Read by
        # the pause summary so "the agent went quiet" can be told apart from
        # "the agent spent its turns on a tool it was never going to be allowed".
        policy_denials: dict[str, int] = {}

        # --- Ring 3: PreToolUse hook as Python callable
        async def pre_tool_use_hook(  # noqa: ARG001 — tool_use_id/context unused by design
            hook_input: HookInput,
            tool_use_id: str | None,  # noqa: ARG001
            context: HookContext,  # noqa: ARG001
        ) -> HookJSONOutput:
            tool_name, hook_tool_input = _extract_hook_call(hook_input)
            # NOTE: tools_called is incremented on ToolUseBlock emission (after
            # hook allows) — not here — so denied tools don't inflate the counter.
            # Denials are counted separately in policy_denials.
            return await _evaluate_hook(  # type: ignore[return-value]
                invocation.safety_policy,
                tool_name,
                hook_tool_input,
                tools_called,
                writer,
                policy_denials,
                ids=ids,
            )

        # Attach hook to options. The SDK's hook support was asserted at the
        # top of run() and fails closed; a try/except around this assignment
        # would silently fail-open, the explicit anti-pattern the 2026-04-28
        # review (S3) flagged. Set unconditionally.
        options.hooks = {"PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use_hook])]}

        # --- Prompt assembly: system_context prepended (legacy behavior).
        prompt = invocation.prompt
        if invocation.system_context:
            prompt = f"{invocation.system_context}\n\n{prompt}"
        if resume_messages and not resume_session_id:
            prompt = f"{render_transcript_block(resume_messages)}\n\n{prompt}"

        writer(events.agent_started(model=invocation.model, max_turns=invocation.max_turns, **ids))

        # Seed the api_key-mode messages list with the user prompt as turn 0.
        # We append AssistantMessage text below; this stays empty/unused in
        # claude_max mode (the resume path uses session_id + jsonl bytes).
        captured_messages.append({"role": "user", "content": prompt})

        async def consume(stream: Any) -> None:
            nonlocal result_fallback, cost_usd, captured_session_id
            nonlocal input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
            nonlocal is_error, provider_failure, cli_error
            turn_number = 0
            # Subagent bookkeeping (spec 3.3): parent tool id -> the Task tool
            # call's name, recorded when the parent's tool_call was seen. A
            # subagent is open from the first message that names it as parent
            # until its own tool_result arrives.
            task_calls: dict[str, str] = {}
            open_subagents: set[str] = set()

            def _scoped_ids(parent: str | None) -> dict[str, Any]:
                """The correlation ids for a message under ``parent``.

                Also EMITS subagent_start the first time a known parent is
                seen, so the boundary precedes every event scoped under it.
                A parent we never saw a Task call for is still stamped on the
                events, but opens no subagent: there is nothing to close it.
                """
                if not parent:
                    return dict(ids)
                if parent in task_calls and parent not in open_subagents:
                    open_subagents.add(parent)
                    writer(events.subagent_start(tool_id=parent, name=task_calls[parent], **ids))
                return {**ids, "parent_tool_use_id": parent}

            async for message in stream:
                failure = _sdk_provider_failure(message, now=self._clock())
                if failure is not None:
                    is_error = True
                    provider_failure = merge_provider_failure(provider_failure, failure)
                # subtype "success" on an is_error result is an API failure, not
                # a successful run. Captured even when the provider classifier
                # has nothing to say, so the subtype label cannot win by default.
                captured = _cli_error_result(message)
                if captured is not None:
                    is_error = True
                    cli_error = captured
                # Snapshot session_id from any message that carries one.
                # Both AssistantMessage and ResultMessage expose session_id
                # in the SDK's types.py (see SystemMessage init payload).
                msg_session_id = getattr(message, "session_id", None)
                if msg_session_id and not captured_session_id:
                    captured_session_id = str(msg_session_id)
                if _is_assistant(message):
                    parent = getattr(message, "parent_tool_use_id", None) or None
                    scoped = _scoped_ids(parent)
                    # Only a top-level message is a turn; a subagent's messages
                    # ride under its parent's turn (spec 3.3).
                    if parent is None:
                        turn_number += 1
                        writer(
                            events.turn_start(turn_number=turn_number, model=getattr(message, "model", ""), **scoped)
                        )
                    # One turn_usage per message that reports usage (the SDK's
                    # AssistantMessage.usage is a dict or None); cost_update at
                    # the result stays the run's authoritative total.
                    usage_now = getattr(message, "usage", None)
                    if isinstance(usage_now, dict):
                        model_now = str(getattr(message, "model", "") or invocation.model)
                        turn_in = int(usage_now.get("input_tokens", 0) or 0)
                        turn_out = int(usage_now.get("output_tokens", 0) or 0)
                        turn_cache_read = int(usage_now.get("cache_read_input_tokens", 0) or 0)
                        turn_cache_creation = int(usage_now.get("cache_creation_input_tokens", 0) or 0)
                        writer(
                            events.turn_usage(
                                input_tokens=turn_in,
                                output_tokens=turn_out,
                                cache_read_tokens=turn_cache_read,
                                cache_creation_tokens=turn_cache_creation,
                                cost_usd=price_usage(
                                    model_now,
                                    input_tokens=turn_in,
                                    output_tokens=turn_out,
                                    cache_read_tokens=turn_cache_read,
                                    cache_creation_tokens=turn_cache_creation,
                                ),
                                model=model_now,
                                **scoped,
                            )
                        )
                    # Accumulate this assistant turn's text into the messages
                    # buffer (api_key resume path). We collapse all text
                    # blocks in the message into a single content string;
                    # tool_use / thinking blocks are intentionally dropped
                    # because the Messages-API resume only needs the
                    # textual conversation, not the tool wire-format.
                    assistant_text = ""
                    for block in getattr(message, "content", []):
                        if _is_text_block(block):
                            block_text = str(getattr(block, "text", ""))
                            assistant_text += block_text
                            writer(events.text(text=scrub(block_text), **scoped))
                        elif _is_tool_use_block(block):
                            tn = getattr(block, "name", "") or getattr(block, "tool_name", "")
                            if tn:
                                tools_called[tn] = tools_called.get(tn, 0) + 1
                            raw_block_input = getattr(block, "input", {})
                            if tn in ("Task", "Agent") and isinstance(raw_block_input, dict):
                                task_calls[str(getattr(block, "id", ""))] = str(
                                    raw_block_input.get("subagent_type") or raw_block_input.get("description") or tn
                                )
                            # Preserve full structured input on the event so
                            # downstream consumers (e.g. triage_node parsing
                            # the set_qa_decision tool call) can re-validate
                            # against a Pydantic schema. ``summary`` stays as a
                            # short human-readable summary for log/UI use.
                            writer(
                                events.tool_call(
                                    tool_name=tn,
                                    tool_id=getattr(block, "id", ""),
                                    summary=_summarize_tool_input(tn, raw_block_input),
                                    tool_input=raw_block_input if isinstance(raw_block_input, dict) else {},
                                    **scoped,
                                )
                            )
                        elif ThinkingBlock is not None and isinstance(block, ThinkingBlock):
                            writer(events.thinking(text=scrub(str(getattr(block, "thinking", ""))), **scoped))
                    # Record this assistant turn in the messages buffer.
                    # Skip empty turns (assistant emitted only tool_use /
                    # thinking — no textual reply); they're meaningless to
                    # the Messages-API resume since it only replays text.
                    if assistant_text:
                        captured_messages.append({"role": "assistant", "content": assistant_text})
                    # #329: the final text is built per MESSAGE (blocks of one
                    # message are continuous), joined with a blank line.
                    final_text.add_message(assistant_text)
                elif _is_result(message):
                    if getattr(message, "result", None) and not final_text:
                        result_fallback = str(getattr(message, "result", ""))
                    total_cost_usd = getattr(message, "total_cost_usd", 0.0) or 0.0
                    # A total below the restored one means this CLI did not
                    # restore it (an older CLI): the total is this run's own.
                    cost_usd = (
                        total_cost_usd - resumed_cost_usd if total_cost_usd >= resumed_cost_usd else total_cost_usd
                    )
                    usage = getattr(message, "usage", {}) or {}
                    input_tokens = int(usage.get("input_tokens", 0) or 0)
                    output_tokens = int(usage.get("output_tokens", 0) or 0)
                    cache_read_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)
                    cache_creation_tokens = int(usage.get("cache_creation_input_tokens", 0) or 0)
                    writer(
                        events.cost_update(
                            cost_usd=cost_usd,
                            duration_ms=getattr(message, "duration_ms", 0),
                            duration_api_ms=getattr(message, "duration_api_ms", 0),
                            num_turns=getattr(message, "num_turns", 0),
                            tokens_in=input_tokens,
                            tokens_out=output_tokens,
                            cache_read_tokens=cache_read_tokens,
                            cache_creation_tokens=cache_creation_tokens,
                            **ids,
                        )
                    )
                    # The agent's successful result is its last word. CLI
                    # 2.1.292 would hold the run open after it until every
                    # background shell (an explicit run_in_background, or a
                    # command auto-backgrounded at its timeout) exits or hits
                    # its 30-minute limit, then run one more turn on the
                    # notification; the runner's idle watchdog kills that
                    # silent wait at 360s and the result is lost. Ending here
                    # keeps the old CLI's behaviour: closing the stream ends the
                    # SDK query, which closes the CLI's stdin and stops it and
                    # its shells about 5s later (measured, #501).
                    # An error result goes on to the SDK's closing exception.
                    if not getattr(message, "is_error", False):
                        return
                elif getattr(message, "subtype", None) == "compact_boundary":
                    # SystemMessage(subtype, data): only the compaction
                    # boundary is an event; other subtypes (init, ...) are not.
                    data = getattr(message, "data", None) or {}
                    meta = data.get("compact_metadata") if isinstance(data, dict) else None
                    pre = meta.get("pre_tokens") if isinstance(meta, dict) else None
                    writer(events.context_compacted(pre_tokens=int(pre) if isinstance(pre, int) else None, **ids))
                elif hasattr(message, "content"):
                    # Tool results ride UserMessage (Anthropic protocol). Emit
                    # each as tool_result and recover any ask_user event the
                    # Bash tool captured in its output (EMB-44: the in-sandbox
                    # ask_user helper prints its JSON to the Bash tool's
                    # stdout, so it only ever reaches us inside a result).
                    # String content is a user turn, not a result: scan it for
                    # ask_user events and emit nothing else.
                    user_content = getattr(message, "content", None)
                    parent = getattr(message, "parent_tool_use_id", None) or None
                    scoped = _scoped_ids(parent)
                    if isinstance(user_content, str):
                        for embedded in _extract_embedded_ask_user_events(user_content):
                            writer({**embedded, **scoped})
                    elif isinstance(user_content, list):
                        for block in user_content:
                            tool_use_id = getattr(block, "tool_use_id", None)
                            if tool_use_id is None:
                                continue
                            result_text = _render_tool_result(getattr(block, "content", None))
                            for embedded in _extract_embedded_ask_user_events(result_text):
                                writer({**embedded, **scoped})
                            is_error_result = bool(getattr(block, "is_error", False))
                            writer(
                                events.tool_result(
                                    tool_id=str(tool_use_id),
                                    content=scrub(result_text),
                                    is_error=is_error_result,
                                    **scoped,
                                )
                            )
                            # The Task call's own result closes its subagent.
                            if str(tool_use_id) in open_subagents:
                                open_subagents.discard(str(tool_use_id))
                                writer(events.subagent_end(tool_id=str(tool_use_id), is_error=is_error_result, **ids))

        async def execute() -> None:
            stream = query(prompt=prompt, options=options)
            try:
                await consume(stream)
            finally:
                # ``async for`` does not close the generator it leaves, so the
                # SDK's own cleanup (close stdin, then terminate the CLI) runs
                # only when it is closed explicitly.
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    await aclose()

        # Written after classification so the streamed error matches the
        # message the run actually reports — the SDK exception is often only
        # the subtype, and that is not what downstream should record.
        stream_error = False
        try:
            await asyncio.wait_for(execute(), timeout=max(invocation.timeout_seconds, 0.001))
        except TimeoutError:
            is_error = True
            error_message = f"Agent timed out after {invocation.timeout_seconds}s"
            stream_error = True
        except Exception as exc:
            is_error = True
            error_message = str(exc)
            stream_error = True

        # #466: an account failure (spent window, rate limit, refused token) is
        # the run's cause, whatever ended the stream after it, so its text
        # replaces the SDK's closing exception — on a spent usage window that
        # reads "error result: success". A non-account is_error result whose
        # exception is only the subtype keeps result/errors/api_error_status
        # instead of that label; a closing exception that already carries the
        # errors text (a turn cap) is left as the SDK wrote it.
        provider_resets_at: datetime | None = None
        if provider_failure is not None:
            error_code = provider_failure.code
            provider_resets_at = provider_failure.resets_at
            if provider_failure.text and (error_code in ACCOUNT_FAILURE_CODES or not error_message):
                error_message = provider_failure.text
        account_sentence = (
            provider_failure is not None and bool(provider_failure.text) and error_code in ACCOUNT_FAILURE_CODES
        )
        if cli_error is not None and not account_sentence:
            subtype_only = _is_subtype_only_sdk_error(error_message, cli_error.subtype)
            kept_sdk_wording = (
                bool(error_message) and error_message.startswith(_SDK_ERROR_RESULT_PREFIX) and not subtype_only
            )
            if cli_error.diagnostic and (not error_message or subtype_only):
                error_message = cli_error.diagnostic
            elif not cli_error.diagnostic and (not error_message or subtype_only):
                shown = cli_error.subtype or "unknown"
                error_message = (
                    f"Claude Code is_error result (subtype={shown}) with no result, errors, or api_error_status"
                )
            # A result the classifier already kept can still omit the HTTP status
            # the CLI reported separately. Don't touch an SDK exception that
            # already names the errors text — a turn cap must keep that wording.
            status = cli_error.api_error_status
            if status is not None and error_message and not kept_sdk_wording and str(status) not in error_message:
                error_message = f"{error_message}; api_error_status={status}"
        if stream_error and error_message:
            writer(events.error(message=error_message, code=error_code, **ids))

        elapsed_ms = int((time.time() - now) * 1000)
        output = final_text.render() if final_text else tail_on_line_boundary(result_fallback)

        # --- Session-state population (EMB-35: mode-agnostic).
        # The CLI writes a session JSONL regardless of auth mode, so capture
        # session_id + the canonical in-sandbox path for BOTH modes —
        # AgentRunner.copy_bytes_from() pulls the bytes out before the
        # sandbox is destroyed, and the next run resumes via ``--session-id``.
        # The captured messages list rides along as the replay-fallback data
        # source (used when the blob is missing or oversized).
        # On error we deliberately omit both — there's no useful state to
        # resume from a half-failed turn — EXCEPT a turn-cap death
        # (RAV-1260): the conversation ran normally right up to max_turns,
        # so its state is exactly what the decide-now recovery resume in
        # triage/review needs.
        from embry0.agents.session import is_max_turns_error

        out_messages: list[dict[str, Any]] | None = None
        out_session_id: str | None = None
        out_session_blob_path: str | None = None
        if not is_error or is_max_turns_error(error_message):
            out_messages = list(captured_messages)
            if captured_session_id:
                out_session_id = captured_session_id
                # Use claude_cli_session as single source of truth for the
                # CLI's on-disk session file location. Returns None if the
                # CLI didn't write a session file at any known location —
                # in that case AgentRunner skips the docker-cp extract.
                home_dir = Path(os.path.expanduser("~"))
                try:
                    project_cwd: str | None = os.getcwd()
                except (FileNotFoundError, OSError):
                    project_cwd = None
                discovered = find_session_file(
                    home_dir=home_dir,
                    session_id=captured_session_id,
                    project_cwd=project_cwd,
                )
                out_session_blob_path = str(discovered) if discovered else None

        result = AgentOutput(
            agent_type=invocation.agent_type,
            is_error=is_error,
            error_message=error_message,
            error_code=error_code,
            provider_resets_at=provider_resets_at,
            output=output,
            cost_usd=cost_usd,
            duration_ms=elapsed_ms,
            tools_called=tools_called,
            policy_denials=policy_denials,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            messages=out_messages,
            session_id=out_session_id,
            session_blob_path=out_session_blob_path,
        )
        writer(
            events.agent_completed(
                is_error=is_error,
                error_code=error_code,
                error_message=error_message,
                output=scrub(output),
                cost_usd=cost_usd,
                duration_ms=elapsed_ms,
                tools_called=tools_called,
                policy_denials=policy_denials,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
                **ids,
            )
        )
        return result
