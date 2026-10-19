"""Move the pre-registry env tokens into the registry (spec §3.10)."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import structlog
from cryptography.fernet import InvalidToken

from embry0.execution.xai_credential import DurableState, XaiCredentialError, XaiCredentialStore
from embry0.services.credentials.preview import masked_preview
from embry0.services.credentials.providers import (
    _CLAUDE_CREDENTIALS_FILE,
    _claude_access_token,
    base_url_host,
    default_host,
    env_fallback,
    env_fallback_metadata,
)
from embry0.storage.encryption import SecretsProvider
from embry0.storage.repositories.credentials import CredentialsRepository

logger = structlog.get_logger(__name__)

# The orchestrator image's home. Path.home() in a unit test is the operator's
# home, and that auth.json is a live refresh token.
_ORCHESTRATOR_GROK_AUTH = "/home/orchestrator/.grok/auth.json"


@dataclass
class ImportReport:
    created: list[str] = field(default_factory=list)  # "github/default", "github/acme", ...
    bound: list[str] = field(default_factory=list)  # "global:forge -> github/default", ...
    skipped: list[str] = field(default_factory=list)  # "github/acme: exists with a different secret ..."

    def as_dict(self) -> dict[str, list[str]]:
        return {"created": list(self.created), "bound": list(self.bound), "skipped": list(self.skipped)}


def _entries(environ: Mapping[str, str], config: Any, report: ImportReport) -> list[tuple[str, str, str, str]]:
    """(provider, label, token, owner_scope_value_or_empty) in a deterministic order.

    The owner comes from the env key, so it is already in the resolver's normalized form
    (``CredentialResolver.owner_of``): lowercase, with ``_`` where the owner had ``-`` or ``.``.
    """
    out: list[tuple[str, str, str, str]] = []
    for provider, prefix in (("github", "GITHUB_TOKEN"), ("gitlab", "GITLAB_TOKEN")):
        default = str(getattr(config, f"{provider}_token", "") or "")
        if default:
            out.append((provider, "default", default, ""))
        for key in sorted(environ):
            if key.startswith(prefix + "__") and environ[key]:
                owner = key[len(prefix) + 2 :].lower()
                if not owner:
                    report.skipped.append(f"{provider}/: empty owner in env key {key}")
                    continue
                out.append((provider, owner, environ[key], f"{provider}:{owner}"))
    return out


async def _bind(
    repo: CredentialsRepository,
    report: ImportReport,
    scope_kind: str,
    scope_value: str,
    provider: str,
    cid: str,
    name: str,
    actor: str,
    *,
    role: str = "forge",
    priority: int = 0,
) -> None:
    scope = f"{scope_kind}:{scope_value}:{role}" if scope_value else f"{scope_kind}:{role}"
    existing = await repo.bindings_for_scope(scope_kind, scope_value)
    same = [b for b in existing if b["role"] == role and b["provider"] == provider]
    if any(b["credential_id"] == cid for b in same):
        return
    # The global llm scope is a pool. Every other role keeps one credential per provider.
    if same and not (role == "llm" and scope_kind == "global"):
        report.skipped.append(f"{scope} already bound to {same[0]['credential_id']}")
        return
    keep = [
        {
            "credential_id": b["credential_id"],
            "role": b["role"],
            "pinned": b.get("pinned", False),
            "priority": b.get("priority", 0),
        }
        for b in existing
    ]
    await repo.replace_bindings(
        scope_kind,
        scope_value,
        [*keep, {"credential_id": cid, "role": role, "pinned": False, "priority": priority}],
        created_by=actor,
    )
    report.bound.append(f"{scope} -> {name}")


def _durable_json(state: DurableState) -> str:
    """The plaintext ``XaiCredentialStore.save`` encrypts."""
    return json.dumps(
        {
            "refresh_token": state.refresh_token,
            "client_id": state.client_id,
            "token_endpoint": state.token_endpoint,
        }
    )


async def _xai_state_from_file(path: str, secrets: SecretsProvider) -> DurableState | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        return await XaiCredentialStore(path, secrets).load()
    except XaiCredentialError:
        return None


async def _xai_state_from_cli(path: str, secrets: SecretsProvider) -> DurableState | None:
    if not path or not os.path.isfile(path):
        return None
    fd, name = tempfile.mkstemp(prefix="embry0-xai-import-")
    os.close(fd)
    try:
        return await XaiCredentialStore(name, secrets).seed_from_grok_cli(path)
    except XaiCredentialError:
        return None
    finally:
        for leftover in (name, name + ".tmp"):
            try:
                os.unlink(leftover)
            except OSError:
                pass


async def _llm_entries(
    env: Mapping[str, str],
    config: Any,
    secrets: SecretsProvider,
    report: ImportReport,
    *,
    claude_credentials_file: str | None,
    xai_store_path: str | None,
    grok_auth_file: str | None,
) -> list[tuple[str, str, str, str, str, int, dict[str, Any]]]:
    """``(provider, label, kind, host, secret, priority, metadata)`` for the §3.10 LLM rows."""
    out: list[tuple[str, str, str, str, str, int, dict[str, Any]]] = []
    claude_path = _CLAUDE_CREDENTIALS_FILE if claude_credentials_file is None else claude_credentials_file
    token = (env.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
    file_token = _claude_access_token(claude_path) or ""
    if token:
        out.append(("anthropic", "default", "llm_oauth", default_host("anthropic"), token, 0, {}))
        if file_token:
            report.skipped.append("anthropic/default: credentials file ignored because CLAUDE_CODE_OAUTH_TOKEN is set")
    elif file_token:
        out.append(("anthropic", "default", "llm_oauth", default_host("anthropic"), file_token, 0, {}))

    if xai_store_path is None:
        store_path = (env.get("XAI_CREDENTIAL_PATH") or "").strip() or str(
            getattr(config, "xai_credential_path", "") or ""
        )
    else:
        store_path = xai_store_path
    if grok_auth_file is None:
        grok_path = str(getattr(config, "xai_grok_cli_store", "") or "") or _ORCHESTRATOR_GROK_AUTH
    else:
        grok_path = grok_auth_file
    state = await _xai_state_from_file(store_path, secrets)
    if state is None and store_path and os.path.isfile(store_path):
        report.skipped.append("xai/default: credential file unreadable")
    elif state is not None:
        out.append(("xai", "default", "llm_oauth", default_host("xai"), _durable_json(state), 0, {}))
        if grok_path and os.path.isfile(grok_path):
            report.skipped.append("xai/default: grok auth file ignored because the credential file is set")
    else:
        cli_state = await _xai_state_from_cli(grok_path, secrets)
        if cli_state is not None:
            out.append(("xai", "default", "llm_oauth", default_host("xai"), _durable_json(cli_state), 0, {}))
        elif grok_path and os.path.isfile(grok_path):
            report.skipped.append("xai/default: grok auth file unreadable")

    api_key = (env.get("XAI_API_KEY") or "").strip()
    if api_key:
        out.append(("xai", "api-key", "llm_api_key", default_host("xai"), api_key, 1, {}))

    # The enabled OpenAI-compatible providers: one API key each, from the variable the
    # resolver falls back to. A custom key is imported only with its base URL.
    from embry0.agents.providers import enabled_openai_compatible

    for provider in enabled_openai_compatible(env):
        fallback = env_fallback(provider.name, env, config)
        if fallback is None:
            continue
        metadata = env_fallback_metadata(provider.name, env)
        host = base_url_host(metadata["base_url"]) if "base_url" in metadata else default_host(provider.name, env)
        out.append((provider.name, "api-key", "llm_api_key", host, fallback[1], 0, metadata))
    return out


async def import_from_env(
    repo: CredentialsRepository,
    secrets: SecretsProvider,
    config: Any,
    *,
    environ: Mapping[str, str] | None = None,
    replace: bool = False,
    actor: str = "import-env",
    claude_credentials_file: str | None = None,
    xai_store_path: str | None = None,
    grok_auth_file: str | None = None,
) -> ImportReport:
    env = os.environ if environ is None else environ
    report = ImportReport()
    gitlab_host = urlparse(str(getattr(config, "gitlab_url", "") or "")).hostname or "gitlab.com"
    by_value: dict[tuple[str, str], tuple[str, str]] = {}  # (provider, token) -> (credential id, name)

    for provider, label, token, owner_scope in _entries(env, config, report):
        name = f"{provider}/{label}"
        hit = by_value.get((provider, token))
        if hit is None:
            row = await repo.find_by_label(provider, label)
            if row is not None:
                try:
                    stored: str | None = await secrets.decrypt(str(row["secret_ciphertext"]))
                except InvalidToken:  # written under a key this process lacks: treat as a different secret
                    stored = None
                if stored != token:
                    if not replace:
                        report.skipped.append(
                            f"{name}: stored secret cannot be decrypted (pass replace=true to rotate)"
                            if stored is None
                            else f"{name}: exists with a different secret (pass replace=true)"
                        )
                        continue
                    # rotate also clears a decryption_failed status
                    await repo.rotate(
                        str(row["id"]),
                        secret_ciphertext=await secrets.encrypt(token),
                        masked_preview=masked_preview(token),
                    )
            else:
                row = await repo.create(
                    kind="forge_token",
                    provider=provider,
                    host="github.com" if provider == "github" else gitlab_host,
                    label=label,
                    secret_ciphertext=await secrets.encrypt(token),
                    masked_preview=masked_preview(token),
                    created_by=actor,
                )
                report.created.append(name)
            hit = (str(row["id"]), name)
            by_value[(provider, token)] = hit
        cid, cred_name = hit
        if owner_scope:
            await _bind(repo, report, "owner", owner_scope, provider, cid, cred_name, actor)
        else:
            await _bind(repo, report, "global", "", provider, cid, cred_name, actor)

    for provider, label, kind, host, secret, priority, metadata in await _llm_entries(
        env,
        config,
        secrets,
        report,
        claude_credentials_file=claude_credentials_file,
        xai_store_path=xai_store_path,
        grok_auth_file=grok_auth_file,
    ):
        name = f"{provider}/{label}"
        row = await repo.find_by_label(provider, label)
        if row is not None:
            try:
                stored = await secrets.decrypt(str(row["secret_ciphertext"]))
            except InvalidToken:
                stored = None
            if stored != secret:
                if not replace:
                    report.skipped.append(
                        f"{name}: stored secret cannot be decrypted (pass replace=true to rotate)"
                        if stored is None
                        else f"{name}: exists with a different secret (pass replace=true)"
                    )
                    continue
                await repo.rotate(
                    str(row["id"]),
                    secret_ciphertext=await secrets.encrypt(secret),
                    masked_preview=masked_preview(secret),
                )
        else:
            row = await repo.create(
                kind=kind,
                provider=provider,
                host=host,
                label=label,
                secret_ciphertext=await secrets.encrypt(secret),
                masked_preview=masked_preview(secret),
                created_by=actor,
                metadata=metadata,
            )
            report.created.append(name)
        await _bind(repo, report, "global", "", provider, str(row["id"]), name, actor, role="llm", priority=priority)

    logger.info("credentials_imported_from_env", **report.as_dict())
    return report
