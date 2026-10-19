"""OpenAICompatibleExecutor — agents on any OpenAI-compatible chat-completions API.

Runs the shared tool loop (:func:`embry0.agents._tool_loop.run_tool_loop`) over
``POST <root>/chat/completions`` for every catalog provider whose ``runtime`` is
``chat_completions`` (``embry0/agents/providers.py``): OpenAI, OpenRouter, Gemini's
compatible endpoint, Groq, DeepSeek, and a custom base URL (Ollama, vLLM, LM Studio).
Tool calling is the API's ``tools``/``tool_calls``: each call is gated by
``gate_tool_call`` and run in the sandbox like the CLI's tools, its result sent back
as a ``tool`` message.

The key and, for the custom provider, the base URL are read from the sandbox env
(``EMBRY0_LLM_*``, injected at create from the credential registry or the
orchestrator env, reserved from repository override). They are never on argv.
Missing either fails the run before any request. httpx only: no provider SDK is
baked into the sandbox image.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING, Any

import httpx

from embry0.agents._tool_loop import (
    ToolCall,
    ToolResult,
    Turn,
    Usage,
    error_output,
    mcp_client_from_config,
    price_run,
    run_tool_loop,
)
from embry0.agents.invocation import AgentInvocation
from embry0.agents.mcp_client import McpStdioClient, summarize_content
from embry0.agents.providers import ModelProvider, provider_named, upstream_model
from embry0.agents.xai_tools import execute_tool
from embry0.execution.agent_runner import AgentOutput
from embry0.safety.error_codes import ErrorCode, classify_agent_error

if TYPE_CHECKING:
    from langchain_core.runnables.config import RunnableConfig

# No read timeout of its own: a long completion is bounded by the run's timeout_seconds.
_REQUEST_TIMEOUT = httpx.Timeout(None, connect=15.0)
# What the provider SDKs retry: rate limits, conflicts and server errors.
_RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})
_MAX_RETRIES = 2
_RETRY_BASE_SECONDS = 1.0
_ERROR_BODY_CHARS = 500


class ProviderHTTPError(RuntimeError):
    """The provider answered with an error status, or with an error in place of a completion."""

    def __init__(self, provider: str, status: int, detail: str) -> None:
        super().__init__(f"{provider} answered HTTP {status}: {detail}")
        self.status = status

    @property
    def error_code(self) -> ErrorCode:
        """The code a run that failed this way carries; classify_failure reads it too."""
        status = getattr(self, "status", 0)
        if status == 429:
            return ErrorCode.PROVIDER_RATE_LIMITED
        if status in (401, 403):
            return ErrorCode.AUTH_REJECTED
        return classify_agent_error(str(self)) or ErrorCode.UPSTREAM_HTTP


def _make_client(base_url: str, api_key: str) -> httpx.AsyncClient:
    """The HTTP client for one run. A module-level seam tests may replace."""
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/") + "/",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=_REQUEST_TIMEOUT,
    )


def _make_mcp_client(server_name: str, server_cfg: Any) -> McpStdioClient:
    """Build (not start) the stdio client for one ``mcp_servers`` entry. A seam tests may replace."""
    return mcp_client_from_config(server_name, server_cfg)


def _error_detail(resp: httpx.Response) -> str:
    """The provider's own error text, cut short. A request header is never part of it."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:_ERROR_BODY_CHARS]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:_ERROR_BODY_CHARS]
    return json.dumps(body)[:_ERROR_BODY_CHARS]


def _content_text(content: Any) -> str:
    """A message ``content`` as text: a string, or the text parts of a part list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


def _tool_call(raw: Any, index: int) -> tuple[ToolCall, dict[str, Any]]:
    """The loop's view of one ``tool_calls`` entry, and the entry as it is sent back."""
    entry = dict(raw) if isinstance(raw, dict) else {}
    raw_function = entry.get("function")
    function: dict[str, Any] = raw_function if isinstance(raw_function, dict) else {}
    # A server that leaves the id out still needs one to pair the result with its call.
    call_id = str(entry.get("id") or f"call_{index}")
    entry["id"] = call_id
    name = str(function.get("name", ""))
    arguments = function.get("arguments")
    if isinstance(arguments, dict):
        return ToolCall(call_id, name, arguments), entry
    try:
        parsed = json.loads(arguments) if isinstance(arguments, str) and arguments.strip() else {}
    except json.JSONDecodeError as exc:
        return ToolCall(call_id, name, {}, input_error=f"tool call arguments are not valid JSON ({exc})"), entry
    if not isinstance(parsed, dict):
        return ToolCall(call_id, name, {}, input_error="tool call arguments must be a JSON object"), entry
    return ToolCall(call_id, name, parsed), entry


class _ChatCompletionsWire:
    """OpenAI chat completions with function tools."""

    def __init__(
        self, client: httpx.AsyncClient, *, provider: str, model: str, extras: dict[str, Any] | None = None
    ) -> None:
        self._client = client
        self._provider = provider
        self._model = model
        self._extras = dict(extras or {})

    def tool_defs(self, defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": d["name"],
                    "description": d.get("description", ""),
                    "parameters": d.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
            for d in defs
        ]

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                resp = await self._client.post("chat/completions", json=body)
            except httpx.TransportError:
                if attempt >= _MAX_RETRIES:
                    raise
            else:
                if resp.status_code < 400:
                    data = resp.json()
                    if not isinstance(data, dict):
                        raise ProviderHTTPError(self._provider, resp.status_code, "response is not a JSON object")
                    return data
                if resp.status_code not in _RETRY_STATUSES or attempt >= _MAX_RETRIES:
                    raise ProviderHTTPError(self._provider, resp.status_code, _error_detail(resp))
            attempt += 1
            await asyncio.sleep(_RETRY_BASE_SECONDS * attempt)

    async def turn(self, *, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> Turn:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *messages],
            **self._extras,
        }
        if tools:
            body["tools"] = tools
        data = await self._post(body)
        # Some gateways answer 200 with an error object instead of a completion.
        if isinstance(data.get("error"), dict):
            raise ProviderHTTPError(self._provider, 200, str(data["error"].get("message") or data["error"]))
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderHTTPError(self._provider, 200, "response carries no choices")
        raw_message = choices[0].get("message")
        message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}

        raw_usage = data.get("usage")
        usage: Usage | None = None
        if isinstance(raw_usage, dict):
            prompt_tokens = int(raw_usage.get("prompt_tokens") or 0)
            details = raw_usage.get("prompt_tokens_details")
            cached = int((details.get("cached_tokens") if isinstance(details, dict) else 0) or 0)
            # prompt_tokens counts the cached ones; the loop prices cached input apart.
            usage = Usage(
                input_tokens=max(prompt_tokens - cached, 0),
                output_tokens=int(raw_usage.get("completion_tokens") or 0),
                cache_read_tokens=cached,
            )

        parts: list[tuple[str, str]] = []
        for key in ("reasoning_content", "reasoning"):
            reasoning = message.get(key)
            if isinstance(reasoning, str) and reasoning:
                parts.append(("thinking", reasoning))
        text = _content_text(message.get("content"))
        if text:
            parts.append(("text", text))

        tool_calls: list[ToolCall] = []
        sent_back: list[dict[str, Any]] = []
        for index, raw in enumerate(message.get("tool_calls") or []):
            call, entry = _tool_call(raw, index)
            tool_calls.append(call)
            sent_back.append(entry)

        # The assistant message goes back as the provider wrote it (its reasoning field
        # included, which some providers require on the next turn), minus null fields.
        assistant: dict[str, Any] = {k: v for k, v in message.items() if v is not None}
        assistant["role"] = "assistant"
        if sent_back:
            assistant["tool_calls"] = sent_back
        elif "content" not in assistant:
            assistant["content"] = ""
        return Turn(
            parts=parts,
            tool_calls=tool_calls,
            # Not finish_reason: several servers report "stop" on a turn that calls tools.
            wants_tools=bool(tool_calls),
            usage=usage,
            assistant_message=assistant,
        )

    def tool_result_messages(self, results: list[ToolResult]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in results:
            text = r.content if isinstance(r.content, str) else summarize_content(r.content or "")
            # The API has no error flag on a tool message; the model reads it from the text.
            if r.is_error:
                text = f"Error: {text}"
            out.append({"role": "tool", "tool_call_id": r.tool_id, "content": text})
        return out

    def failure(self, exc: BaseException) -> tuple[str | None, str]:
        if isinstance(exc, ProviderHTTPError):
            return exc.error_code.value, str(exc)
        if isinstance(exc, httpx.TransportError):
            message = f"{self._provider} unreachable: {type(exc).__name__}: {exc}"
            return ErrorCode.UPSTREAM_HTTP.value, message
        message = str(exc)
        classified = classify_agent_error(message)
        return (classified.value if classified else None), message

    async def close(self) -> None:
        await self._client.aclose()


def _endpoint(provider: ModelProvider) -> str:
    """The API root this run calls: the operator's for the custom provider, else the catalog's."""
    if provider.sandbox_base_url_env:
        return os.environ.get(provider.sandbox_base_url_env, "").strip()
    return provider.openai_base_url


class OpenAICompatibleExecutor:
    """Chat-completions executor for the OpenAI-compatible providers. Implements AgentExecutor."""

    async def run(
        self,
        invocation: AgentInvocation,
        config: RunnableConfig | dict[str, Any] | None = None,
        *,
        resume_session_id: str | None = None,  # noqa: ARG002 — no CLI session on this path
        resume_messages: list[dict[str, Any]] | None = None,
    ) -> AgentOutput:
        provider = provider_named(invocation.provider)
        if provider is None or provider.runtime != "chat_completions":
            return error_output(
                invocation.agent_type, f"{invocation.provider!r} is not an OpenAI-compatible chat-completions provider"
            )
        base_url = _endpoint(provider)
        if not base_url:
            return error_output(
                invocation.agent_type,
                f"no base URL for provider {provider.name!r}: {provider.sandbox_base_url_env} is not set in the sandbox",
            )
        api_key = os.environ.get(provider.sandbox_key_env, "").strip()
        if not api_key:
            return error_output(
                invocation.agent_type,
                f"model {invocation.model!r} needs a {provider.name} credential: {provider.sandbox_key_env} "
                "is not set in the sandbox (the registry or the orchestrator env injects it at create)",
            )
        model = upstream_model(provider, invocation.model)
        extras = dict(provider.request_extras.get(invocation.model, {}))
        return await run_tool_loop(
            invocation,
            config,
            make_wire=lambda: _ChatCompletionsWire(
                _make_client(base_url, api_key), provider=provider.name, model=model, extras=extras
            ),
            execute_builtin=execute_tool,
            make_mcp_client=_make_mcp_client,
            compute_cost=price_run,
            resume_messages=resume_messages,
            unpriced_log_event="openai_compatible_cost_unpriced_model",
        )
