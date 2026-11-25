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

from embry0.execution import github_tokens, gitlab_tokens
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

#: The key the environment's custom credential gets from ``LOCAL_LLM_UPSTREAM`` when no variable gives one: a
#: credential needs a secret, and a server that checks no key ignores the one the proxy sends.
PLACEHOLDER_KEY = "no-key"


@dataclass
class ImportReport:
    created: list[str] = field(default_factory=list)  # "github/default", "github/acme", ...
    bound: list[str] = field(default_factory=list)  # "global:forge -> github/default", ...
    skipped: list[str] = field(default_factory=list)  # "github/acme: exists with a different secret ..."

    def as_dict(self) -> dict[str, list[str]]:
        return {"created": list(self.created), "bound": list(self.bound), "skipped": list(self.skipped)}


def _entries(environ: Mapping[str, str], config: Any, report: ImportReport) -> list[tuple[str, str, str, str]]:
    """(provider, label, token, env_key_or_empty) in a deterministic order: the default token, with no key, then each
    owner's ``<PREFIX>__<OWNER>``, labelled with the owner part lowercased."""
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
                out.append((provider, owner, environ[key], key))
    return out


#: The env variable each forge reads an owner's token from (the resolver's last step).
_ENV_KEY = {"github": github_tokens.env_key, "gitlab": gitlab_tokens.env_key}


def _owner_of_key(provider: str, label: str, key: str, known: Mapping[str, set[str]]) -> tuple[str | None, str]:
    """The one owner the env ``key`` holds the token of, or None and why not.

    An env variable cannot spell '-' or '.', so a key whose owner part holds '_' can be read by
    several owners (``GITLAB_TOKEN__MY_GROUP``: ``my-group``, ``my.group``, ``my_group``), and a
    binding names one exactly. Such a key names the owner the deployment knows (a registered
    repository's or an organization's) whose key it is, when there is exactly one. A key with no
    '_' in its owner part names one owner, the part lowercased.
    """
    if "_" not in label:
        return label, ""
    owners = sorted(o for o in known.get(provider, set()) if _ENV_KEY[provider](o) == key.upper())
    if len(owners) == 1:
        return owners[0], ""
    if owners:
        return None, f"{key} is read by {', '.join(owners)}; bind it to the owner it is for"
    return None, (
        f"{key} can name more than one owner, and no repository or organization registered here is under one; "
        "register the owner's repositories and import again, or bind it to its owner"
    )


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

    upstream = str(getattr(config, "local_llm_upstream", "") or "").strip().rstrip("/")
    for provider in enabled_openai_compatible():
        if provider.name == "custom" and upstream:
            entry = _local_upstream_entry(upstream, env, config, report, key_env=provider.api_key_env)
            if entry is not None:
                out.append(entry)
            continue
        fallback = env_fallback(provider.name, env, config)
        if fallback is None:
            continue
        metadata = env_fallback_metadata(provider.name, env)
        refused = _refused_address(metadata["base_url"]) if "base_url" in metadata else None
        if refused is not None:
            report.skipped.append(f"{provider.name}/api-key: its base URL may not be a model server: {refused}")
            continue
        host = base_url_host(metadata["base_url"]) if "base_url" in metadata else default_host(provider.name)
        out.append((provider.name, "api-key", "llm_api_key", host, fallback[1], 0, metadata))
    if upstream and not any(provider.name == "custom" for provider in enabled_openai_compatible()):
        report.skipped.append("custom/api-key: LOCAL_LLM_UPSTREAM is set but the catalogue has the custom provider off")
    return out


def _refused_address(url: str) -> str | None:
    """Why the server ``url`` names may not be a model server (this machine, link-local), or None."""
    from embry0.execution.proxy.local_llm_proxy import refused_server_host

    return refused_server_host(urlparse(url).hostname)


def _local_upstream_entry(
    upstream: str, env: Mapping[str, str], config: Any, report: ImportReport, *, key_env: str
) -> tuple[str, str, str, str, str, int, dict[str, Any]] | None:
    """ADR 0032: ``LOCAL_LLM_UPSTREAM``, which sent every agent run on the custom provider to one server, becomes the
    environment's custom credential (``custom/api-key``): that server's address, and ``LOCAL_LLM_API_KEY``, else
    ``CUSTOM_LLM_API_KEY``, else :data:`PLACEHOLDER_KEY`. Only that credential: a stored one keeps its own address
    and key. None, and a line in the report, for an address that is not an http(s) URL."""
    host = base_url_host(upstream)
    if not upstream.startswith(("http://", "https://")) or not host:
        report.skipped.append("custom/api-key: LOCAL_LLM_UPSTREAM is not an http(s) URL with a host")
        return None
    refused = _refused_address(upstream)
    if refused is not None:
        report.skipped.append(f"custom/api-key: LOCAL_LLM_UPSTREAM may not be a model server: {refused}")
        return None
    key = (
        str(getattr(config, "local_llm_api_key", "") or "").strip()
        or (env.get(key_env) or "").strip()
        or PLACEHOLDER_KEY
    )
    return ("custom", "api-key", "llm_api_key", host, key, 0, {"base_url": upstream})


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

    known: dict[str, set[str]] | None = None
    for provider, label, token, env_key in _entries(env, config, report):
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
        if env_key:
            if known is None and "_" in label:
                known = await repo.known_owners()
            owner, why = _owner_of_key(provider, label, env_key, known or {})
            if owner is None:
                report.skipped.append(f"{name}: bound to no owner: {why}")
                continue
            await _bind(repo, report, "owner", f"{provider}:{owner}", provider, cid, cred_name, actor)
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
            # A custom credential's address is the server it is (ADR 0032): one the environment names elsewhere is
            # moved only as a different secret is, with replace.
            stored_metadata = dict(row.get("metadata") or {})
            address = str(metadata.get("base_url") or "")
            moved = bool(address) and str(stored_metadata.get("base_url") or "").rstrip("/") != address
            differs = [what for what, changed in (("secret", stored != secret), ("address", moved)) if changed]
            if differs and not replace:
                report.skipped.append(
                    f"{name}: stored secret cannot be decrypted (pass replace=true to rotate)"
                    if stored is None
                    else f"{name}: exists with a different {' and '.join(differs)} (pass replace=true)"
                )
                continue
            if stored != secret:
                await repo.rotate(
                    str(row["id"]),
                    secret_ciphertext=await secrets.encrypt(secret),
                    masked_preview=masked_preview(secret),
                )
            if moved:
                await repo.set_address(str(row["id"]), host=host, metadata={**stored_metadata, "base_url": address})
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
