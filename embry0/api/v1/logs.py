"""Container-log query API (RAV-1057) — Loki proxy for on-demand investigation.

The scheduled watcher (RAV-657) analyzes logs autonomously; this endpoint is
the interactive lane: the dashboard's Logs page, embry0-mcp's ``logs_query``
tool and the sandbox context-proxy read production logs through it.

Production logs carry personal data, so both routes need operator tier
(``require_log_reader``: a read or merge key gets 403), and every line
returned goes through the log-text redactor (``embry0/safety/redaction.py``,
with the operator's ``LOG_REDACTION_FIELDS``) whoever asks.
"""

import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Query, Request

from embry0.safety.redaction import redact_bounded, redact_log_text, secret_env_values

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["logs"])

# Per-line ceiling applied before redaction, so the cost of one response does
# not depend on how long a line Loki happens to hold (up to `limit` lines).
# The context-proxy's own ceiling is the same; it redacts again, and
# redaction is idempotent.
MAX_LINE_CHARS = 2000
_TRUNCATED = " … [line truncated]"


def _redacted_line(raw: str, known: list[str]) -> str:
    line = raw.strip()

    def redact(text: str) -> str:
        return redact_log_text(text, known)

    return redact_bounded(line, MAX_LINE_CHARS, redact=redact) + (_TRUNCATED if len(line) > MAX_LINE_CHARS else "")


def _escape_logql(value: str) -> str:
    """Escape a user value for embedding inside a LogQL double-quoted string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_query(container: str | None, search: str | None, containers: str | None = None) -> str:
    if containers:
        # RAV-1295: exact-name alternation for the dashboard's multi-select
        # (comma-separated). re.escape neutralises regex metacharacters in
        # names; _escape_logql then handles LogQL string quoting.
        names = [n.strip() for n in containers.split(",") if n.strip()]
        alternation = "|".join(_escape_logql(re.escape(n)) for n in names)
        selector = f'{{container_name=~"^({alternation})$"}}'
    elif container:
        selector = f'{{container_name=~".*{_escape_logql(container)}.*"}}'
    else:
        selector = '{container_name=~".+"}'
    if search:
        return f'{selector} |~ "{_escape_logql(search)}"'
    return selector


# RAV-1295: the container set changes rarely — cache the label lookup
# briefly so a polling tail doesn't hammer Loki's label API.
_CONTAINERS_CACHE: dict[str, Any] = {"at": 0.0, "values": []}
_CONTAINERS_CACHE_TTL_SECONDS = 60.0


@router.get("/logs/containers")
async def list_log_containers(request: Request) -> dict[str, Any]:
    """Container names Loki is collecting logs for (label values, cached).

    Operator surface — deliberately unfiltered. The sandbox context-proxy
    allowlist (CONTEXT_PROXY_CONTAINER_ALLOWLIST) exists because agent
    tools must not read customer-data-bearing logs; this route serves the
    operator dashboard at operator tier, so it lists everything promtail
    ships, and the lines ``/logs/query`` returns from them are redacted.
    """
    config = request.app.state.config
    loki_url = getattr(config, "watcher_loki_url", "")
    if not loki_url:
        raise HTTPException(status_code=503, detail="Loki is not configured (WATCHER_LOKI_URL)")

    now = time.monotonic()
    if _CONTAINERS_CACHE["values"] and now - _CONTAINERS_CACHE["at"] < _CONTAINERS_CACHE_TTL_SECONDS:
        return {"containers": _CONTAINERS_CACHE["values"], "cached": True}

    # Bound the label lookback to the query API's own max window (24h) so
    # long-gone containers age out of the dropdown.
    end = datetime.now(UTC)
    start = end - timedelta(hours=24)
    params = {
        "start": str(int(start.timestamp() * 1e9)),
        "end": str(int(end.timestamp() * 1e9)),
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{loki_url}/loki/api/v1/label/container_name/values", params=params)
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300]
        raise HTTPException(status_code=502, detail=f"Loki label query failed: {detail}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"cannot reach Loki: {exc}") from exc

    values = sorted(body.get("data") or [])
    _CONTAINERS_CACHE["at"] = now
    _CONTAINERS_CACHE["values"] = values
    logger.info("log_containers_listed", count=len(values))
    return {"containers": values, "cached": False}


@router.get("/logs/query")
async def query_logs(
    request: Request,
    container: str | None = Query(default=None, max_length=200),
    containers: str | None = Query(default=None, max_length=1000),
    search: str | None = Query(default=None, max_length=500),
    since_minutes: int = Query(default=60, ge=1, le=1440),
    limit: int = Query(default=200, ge=1, le=1000),
    logql: str | None = Query(default=None, max_length=1000),
) -> dict[str, Any]:
    """Query container logs from Loki, newest first.

    ``container`` matches the Docker container name (substring);
    ``containers`` is a comma-separated list of exact names and
    wins over ``container``; ``search`` is a regex line filter; ``logql``
    overrides all of them with a raw LogQL query. Each line is redacted and
    cut to ``MAX_LINE_CHARS``, and a cut line ends with a marker.
    """
    config = request.app.state.config
    loki_url = getattr(config, "watcher_loki_url", "")
    if not loki_url:
        raise HTTPException(status_code=503, detail="Loki is not configured (WATCHER_LOKI_URL)")

    query = logql or _build_query(container, search, containers)
    end = datetime.now(UTC)
    start = end - timedelta(minutes=since_minutes)
    params = {
        "query": query,
        "start": str(int(start.timestamp() * 1e9)),
        "end": str(int(end.timestamp() * 1e9)),
        "limit": str(limit),
        "direction": "backward",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{loki_url}/loki/api/v1/query_range", params=params)
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300]
        raise HTTPException(status_code=502, detail=f"Loki query failed: {detail}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"cannot reach Loki: {exc}") from exc

    entries: list[tuple[str, dict[str, Any]]] = []
    for stream in (body.get("data") or {}).get("result") or []:
        container_name = (stream.get("stream") or {}).get("container_name", "?")
        for ts, line in stream.get("values") or []:
            entries.append(
                (
                    ts,
                    {
                        "ts": datetime.fromtimestamp(int(ts) / 1e9, tz=UTC).isoformat(),
                        "container": container_name,
                        "line": line.strip(),
                    },
                )
            )
    entries.sort(key=lambda e: e[0], reverse=True)
    known = secret_env_values()
    lines = [{**entry, "line": _redacted_line(entry["line"], known)} for _, entry in entries[:limit]]
    logger.info("logs_queried", query=query, since_minutes=since_minutes, returned=len(lines))
    return {
        "query": query,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "count": len(lines),
        "lines": lines,
    }
