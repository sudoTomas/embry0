"""The custom provider's servers, as startup finds them (ADR 0032).

A custom credential is its server: its address (``metadata.base_url``) and its
key. Every sandbox on the custom provider reaches that server through the
local-llm-proxy, and every background call reaches it directly, so each
credential's own address and key are the ones that work or do not.

Before ADR 0032, ``LOCAL_LLM_UPSTREAM`` sent every agent run on the custom
provider to one root with ``LOCAL_LLM_API_KEY``, whatever its credential named,
and a stored credential could carry a placeholder address or key and still
serve those runs. So at startup each stored custom credential that is not
disabled is probed once (``GET <base_url>/models`` with its key, as
``provider_probe`` checks any OpenAI-compatible key), and one that would not
work now is named in the log, ``custom_credential_check_failed`` with a
``reason``:

- ``no_server``: its metadata names no address;
- ``refused_host``: its address is this machine or link-local, which no job reaches (and which is not probed);
- ``unreachable``: nothing answers at its address;
- ``key_refused``: the server refused its key (401 or 403), which is what a
  placeholder key comes to on a server that checks one;
- ``unexpected_answer``: something answered, but not with a model list (a
  wrong path, say);
- ``unreadable``: its secret could not be read.

The check only logs: it never disables a credential or holds up a start. The
two variables route nothing now; while either is set, startup says so
(``settings_env_retired``, :func:`warn_retired_upstream`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import structlog

from embry0.execution.proxy.local_llm_proxy import refused_server_host
from embry0.services.provider_probe import (
    STATUS_ENTITLEMENT_REFUSED,
    STATUS_ERROR,
    STATUS_NOT_CONFIGURED,
    STATUS_UNAUTHORIZED,
    STATUS_UNREACHABLE,
    ProbeCheck,
    probe_llm_secret,
)

logger = structlog.get_logger(__name__)

#: How many servers are probed at once.
PROBE_CONCURRENCY = 4

#: A probe's status -> the reason the log gives; a status not here is a server that works.
_REASONS = {
    STATUS_NOT_CONFIGURED: "no_server",
    STATUS_UNREACHABLE: "unreachable",
    STATUS_UNAUTHORIZED: "key_refused",
    STATUS_ENTITLEMENT_REFUSED: "key_refused",
    STATUS_ERROR: "unexpected_answer",
}

Probe = Callable[..., Awaitable[ProbeCheck]]


def warn_retired_upstream(config: Any) -> list[str]:
    """Warn once, at startup, for ``LOCAL_LLM_UPSTREAM`` and ``LOCAL_LLM_API_KEY`` while either is set: they route
    nothing now. Never the value. The variables warned about."""
    from embry0.settings.env import LOCAL_UPSTREAM_RETIRED, env_name, is_set

    names: list[str] = []
    for attr in ("local_llm_upstream", "local_llm_api_key"):
        if not is_set(config, attr):
            continue
        name = env_name(attr)
        logger.warning(
            "settings_env_retired", variable=name, hint=f"{name} is no longer read: {LOCAL_UPSTREAM_RETIRED}; remove it"
        )
        names.append(name)
    return names


async def check_custom_servers(credentials: Any, secrets: Any, *, probe: Probe | None = None) -> dict[str, str]:
    """Probe each stored custom credential that is not disabled, and warn about each that would not work now.

    ``credentials`` is the credentials repository and ``secrets`` its secrets provider. Returns each probed
    credential's id with its reason, or ``ok``. Never raises: a check that cannot be made is logged.
    """
    run_probe: Probe = probe or probe_llm_secret
    try:
        rows = [
            row
            for row in await credentials.list_all()
            if row.get("provider") == "custom" and row.get("kind") == "llm_api_key" and row.get("status") != "disabled"
        ]
    except Exception:  # noqa: BLE001 - a start never waits on this
        logger.warning("custom_credential_check_unread", exc_info=True)
        return {}
    gate = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def one(row: dict[str, Any]) -> tuple[str, str]:
        credential_id = str(row["id"])
        fields = {"credential_id": credential_id, "label": str(row.get("label") or ""), "host": row.get("host")}
        base_url = str((row.get("metadata") or {}).get("base_url") or "").strip()
        if not base_url:
            logger.warning("custom_credential_check_failed", reason="no_server", detail="no base_url", **fields)
            return credential_id, "no_server"
        refused = refused_server_host(urlsplit(base_url).hostname)
        if refused is not None:
            logger.warning("custom_credential_check_failed", reason="refused_host", detail=refused, **fields)
            return credential_id, "refused_host"
        async with gate:
            try:
                stored = await credentials.get(credential_id)
                secret = await secrets.decrypt(str((stored or {})["secret_ciphertext"]))
            except Exception as exc:  # noqa: BLE001 - one credential's secret never stops the others' checks
                logger.warning(
                    "custom_credential_check_failed", reason="unreadable", detail=type(exc).__name__, **fields
                )
                return credential_id, "unreadable"
            try:
                check = await run_probe("custom", "llm_api_key", secret, base_url=base_url)
            except Exception as exc:  # noqa: BLE001 - as above
                logger.warning(
                    "custom_credential_check_failed", reason="unreachable", detail=type(exc).__name__, **fields
                )
                return credential_id, "unreachable"
        reason = _REASONS.get(check.status)
        if reason is None:
            logger.info("custom_credential_checked", **fields)
            return credential_id, "ok"
        logger.warning("custom_credential_check_failed", reason=reason, detail=check.message, **fields)
        return credential_id, reason

    return dict(await asyncio.gather(*(one(row) for row in rows)))
