"""Which accounts can list repos: registry credentials for a forge, then the env tokens (spec §3.9)."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import structlog
from cryptography.fernet import InvalidToken

logger = structlog.get_logger(__name__)

_ENV_PREFIX = {"github": "GITHUB_TOKEN", "gitlab": "GITLAB_TOKEN"}


@dataclass(frozen=True)
class ForgeCredentialEntry:
    key: str
    credential_id: str | None
    label: str
    token: str
    source: str

    def __repr__(self) -> str:
        return f"ForgeCredentialEntry(key={self.key!r}, source={self.source!r})"

    def without_token(self) -> ForgeCredentialEntry:
        """Attribution only. The discovery cache outlives the request that decrypted this."""
        if not self.token:
            return self
        return ForgeCredentialEntry(self.key, self.credential_id, self.label, "", self.source)


async def forge_credential_entries(
    repo: Any,
    secrets: Any,
    config: Any,
    forge: str,
    *,
    environ: Mapping[str, str] | None = None,
    keep: Callable[[Mapping[str, Any]], Awaitable[bool]] | None = None,
    with_env: bool = True,
) -> list[ForgeCredentialEntry]:
    """The accounts that list ``forge``'s repositories: each registry credential of it, then the environment's tokens.

    ``keep``, when given, is awaited with each registry credential's row before its secret is decrypted, and one it
    answers False for is left out; ``with_env`` False leaves the environment's tokens out. For a caller that may list only
    with credentials of its own.
    """
    if forge not in _ENV_PREFIX:
        raise ValueError(f"unknown forge {forge!r}")
    env = os.environ if environ is None else environ
    out: list[ForgeCredentialEntry] = []
    seen: set[str] = set()

    def add(entry: ForgeCredentialEntry) -> None:
        if entry.token and entry.token not in seen:
            seen.add(entry.token)
            out.append(entry)

    # None means "no registry on this app" (tests, and any caller that only has env).
    # Falling through to list_all would be caught below and logged on every request.
    if repo is not None:
        try:
            rows = sorted(
                (r for r in await repo.list_all() if r["provider"] == forge and r["status"] != "disabled"),
                key=lambda r: str(r["label"]),
            )
            for row in rows:
                if keep is not None and not await keep(row):
                    continue
                full = await repo.get(str(row["id"]))
                if full is None:
                    continue
                try:
                    token = await secrets.decrypt(str(full["secret_ciphertext"]))
                except InvalidToken:
                    logger.warning("discovery_credential_undecryptable", credential_id=row["id"], label=row["label"])
                    continue
                add(
                    ForgeCredentialEntry(
                        f"credential:{row['label']}", str(row["id"]), str(row["label"]), token, "registry"
                    )
                )
        except Exception as exc:  # noqa: BLE001 — a registry outage must not hide env-discoverable repos
            logger.warning("discovery_registry_error", forge=forge, error=str(exc)[:200])

    if not with_env:
        return out
    prefix = _ENV_PREFIX[forge]
    default = str(getattr(config, f"{forge}_token", "") or "")
    add(ForgeCredentialEntry(prefix, None, prefix, default, "env"))
    for key in sorted(env):
        if key.startswith(prefix + "__"):
            add(ForgeCredentialEntry(key, None, key, env[key], "env"))
    return out
