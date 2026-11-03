"""In-sandbox stdio MCP server exposing ``logs_query`` (RAV-1140).

The watcher's analysis agent runs with an empty workspace — all evidence rides
the briefing prompt, and the injected log window is chosen blind. This server
gives it a typed drill-down tool: one ``logs_query`` call = one mediated GET to
the context-proxy (``EMBRY0_CONTEXT_PROXY_URL``), authenticated with the
per-sandbox bearer from :mod:`embry0.sandbox.context_token`. The proxy enforces
the container allowlist, caps, and quota server-side; this module clamps
client-side too so the agent sees an honest echo of what actually ran, never an
opaque validation error.

Hand-rolled newline-delimited JSON-RPC 2.0 (the MCP stdio transport) on stdlib
only — the ``mcp`` python package is deliberately not a sandbox-image
dependency (see :mod:`embry0.agents.mcp_client`, the mirror-image client).
Single-client, synchronous: the Claude CLI spawns one instance per session.
Every request id always gets a response — the agent can never hang on a dead
call.

Registered per-invocation as::

    {"context": {"type": "stdio", "command": "python3", "args": ["-P", "-s", "-m", "embry0.sandbox.context_mcp"]}}
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from embry0.sandbox.context_token import read_context_proxy_token

_PROTOCOL_VERSION = "2025-06-18"
_SERVER_INFO = {"name": "context", "version": "1.0.0"}
_REQUEST_TIMEOUT = 40.0  # > proxy's 35s upstream client, > Loki's 30s
# Same cap as agents/mcp_client._MAX_RESULT_CHARS.
_MAX_RESULT_CHARS = 30_000

# Client-side clamps, mirroring the context-proxy's server-side bounds.
_MAX_SINCE_MINUTES = 240
_MAX_LIMIT = 200
_MAX_SEARCH_LEN = 500

_NOT_PROVISIONED = "log access is not provisioned for this job — work from the briefing pack"

_TOOL_DEF = {
    "name": "logs_query",
    "description": (
        "Query recent production container logs (read-only, mediated). Use to drill "
        "down on evidence already in the briefing: widen a window around a timestamp, "
        "check a sibling container, or look before/after a deploy marker. The briefing "
        "pack is the starting point — query only to confirm or refute a specific "
        "hypothesis. Queries are quota-limited per job; an invalid container name "
        "returns the allowed list."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "container": {
                "type": "string",
                "description": "Exact container name to query (an invalid name returns the allowed list).",
            },
            "search": {
                "type": "string",
                "description": "Optional regex line filter (max 500 chars).",
            },
            "since_minutes": {
                "type": "integer",
                "description": f"Look-back window in minutes, 1-{_MAX_SINCE_MINUTES} (default 60).",
            },
            "limit": {
                "type": "integer",
                "description": f"Max lines returned, 1-{_MAX_LIMIT} (default 100).",
            },
        },
        "required": ["container"],
    },
}


def _clamp(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(lo, min(hi, parsed))


def _truncate(text: str) -> str:
    if len(text) > _MAX_RESULT_CHARS:
        return text[:_MAX_RESULT_CHARS] + f"\n… [truncated {len(text) - _MAX_RESULT_CHARS} chars]"
    return text


def _iso_ts(raw: Any) -> str:
    """Loki nanosecond-epoch string → ISO-8601 UTC; anything else verbatim."""
    try:
        return datetime.fromtimestamp(int(raw) / 1e9, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(raw)


def _format_result(payload: dict[str, Any], clamped_note: str) -> str:
    lines = payload.get("lines") or []
    header = (
        f"query={payload.get('query', '')} count={payload.get('count', len(lines))}"
        f"{clamped_note} window={payload.get('window_start', '?')}..{payload.get('window_end', '?')}"
    )
    rows = [f"{_iso_ts(entry.get('ts'))}  {entry.get('container', '')}  {entry.get('line', '')}" for entry in lines]
    if not rows:
        rows = ["(no matching lines in this window)"]
    return _truncate("\n".join([header, *rows]))


def run_query(arguments: dict[str, Any]) -> tuple[str, bool]:
    """Execute one mediated log query. Returns (text, is_error) — never raises."""
    base_url = os.environ.get("EMBRY0_CONTEXT_PROXY_URL", "").rstrip("/")
    token = read_context_proxy_token()
    if not base_url or not token:
        return _NOT_PROVISIONED, True

    container = str(arguments.get("container") or "").strip()
    if not container:
        return "logs_query requires a `container` argument", True

    requested_since = arguments.get("since_minutes")
    requested_limit = arguments.get("limit")
    since_minutes = _clamp(requested_since, 60, 1, _MAX_SINCE_MINUTES)
    limit = _clamp(requested_limit, 100, 1, _MAX_LIMIT)
    clamped: list[str] = []
    if requested_since is not None and str(requested_since) != str(since_minutes):
        clamped.append(f"since_minutes={since_minutes}")
    if requested_limit is not None and str(requested_limit) != str(limit):
        clamped.append(f"limit={limit}")
    clamped_note = f" (clamped: {', '.join(clamped)})" if clamped else ""

    params: dict[str, str] = {
        "container": container,
        "since_minutes": str(since_minutes),
        "limit": str(limit),
    }
    search = str(arguments.get("search") or "")[:_MAX_SEARCH_LEN]
    if search:
        params["search"] = search

    url = f"{base_url}/api/v1/logs/query?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:  # noqa: S310 — fixed http proxy URL
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = str(body.get("error") or body.get("detail") or "")
        except (ValueError, OSError):
            pass
        if exc.code == 401:
            return "this sandbox is not enrolled for log access", True
        if exc.code == 400:
            return detail or "bad request", True
        if exc.code == 429:
            return (detail or "log query quota exhausted") + " — conclude from evidence already gathered", True
        return f"log backend unavailable: {detail[:300] or f'HTTP {exc.code}'}", True
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return f"context-proxy unreachable: {exc}", True
    except ValueError as exc:
        return f"context-proxy returned malformed JSON: {exc}", True

    return _format_result(payload, clamped_note), False


def handle_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC message in → reply dict out (None for notifications)."""
    method = msg.get("method", "")
    msg_id = msg.get("id")

    if msg_id is None:
        # Notification (e.g. notifications/initialized) — no reply on the wire.
        return None

    if method == "initialize":
        requested = (msg.get("params") or {}).get("protocolVersion")
        version = requested if isinstance(requested, str) and requested else _PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": dict(_SERVER_INFO),
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": [_TOOL_DEF]}}
    if method == "tools/call":
        params = msg.get("params") or {}
        tool_name = params.get("name", "")
        if tool_name != "logs_query":
            text, is_error = f"unknown tool: {tool_name}", True
        else:
            try:
                text, is_error = run_query(dict(params.get("arguments") or {}))
            except Exception as exc:  # noqa: BLE001 — the agent must always get a reply
                text, is_error = f"logs_query failed: {type(exc).__name__}: {exc}", True
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
        }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            # No id recoverable — nothing meaningful to reply to.
            print(f"context_mcp: unparseable message: {line[:200]!r}", file=sys.stderr, flush=True)
            continue
        if not isinstance(msg, dict):
            continue
        try:
            reply = handle_message(msg)
        except Exception as exc:  # noqa: BLE001 — every request id must get a response
            reply = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32603, "message": f"internal error: {type(exc).__name__}: {exc}"},
            }
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
