"""The one way runtime code obtains a forge token (spec 2026-09-29 §3.1).

Precedence: repo binding → owner binding → host binding → global binding →
the pre-registry env variables → CredentialMissingError. Every step is
appended to ``trace`` so /credentials/resolve can show why.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import structlog
from cryptography.fernet import InvalidToken

from embry0.agents.fallback import Pause
from embry0.execution import github_tokens, gitlab_tokens
from embry0.safety.error_codes import ErrorCode
from embry0.services.credentials.providers import (
    base_url_host,
    default_host,
    env_fallback,
    env_fallback_metadata,
    llm_providers,
)
from embry0.storage.encryption import SecretsProvider
from embry0.storage.repositories.credentials import CredentialsRepository

logger = structlog.get_logger(__name__)

FORGES = ("github", "gitlab")
# Short enough that a pagination sweep reuses one lookup; mutations call invalidate() (#439).
FORGE_CACHE_TTL_SECONDS = 5.0


@dataclass(frozen=True)
class ResolvedCredential:
    id: str | None
    kind: str
    provider: str
    host: str
    label: str
    secret: str
    git_author_name: str | None
    git_author_email: str | None
    source: str
    trace: tuple[str, ...]
    # Non-secret settings stored with the secret: the tailnet a tailnet_api_key reads.
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def __repr__(self) -> str:  # never leak the secret through a stray log
        return f"ResolvedCredential(id={self.id!r}, label={self.label!r}, provider={self.provider!r}, source={self.source!r})"


class CredentialMissingError(LookupError):
    error_code = ErrorCode.CREDENTIAL_MISSING


class CredentialUnavailableError(RuntimeError):
    error_code = ErrorCode.CREDENTIAL_UNAVAILABLE


class CredentialResolver:
    def __init__(
        self,
        repo: CredentialsRepository,
        secrets: SecretsProvider | None,
        config: Any,
        *,
        environ: Mapping[str, str] | None = None,
        credentials_file: str | None = None,
        cache_ttl_seconds: float = FORGE_CACHE_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
        job_pins: Callable[[str], Awaitable[Mapping[str, str]]] | None = None,
    ) -> None:
        self._repo = repo
        # A job's chosen keys, provider to credential id (embry0.services.run_with).
        self._job_pins = job_pins
        self._secrets = secrets
        self._config = config
        self._environ: Mapping[str, str] = os.environ if environ is None else environ
        self._credentials_file = credentials_file
        self._cache_ttl = cache_ttl_seconds
        self._clock = clock or time.monotonic
        self._env_keys_used: set[str] = set()
        self._known_values: list[str] | None = None
        self._forge_cache: dict[tuple[str, str], tuple[float, ResolvedCredential]] = {}

    async def _decrypt(self, ciphertext: str) -> str:
        # None when the app state had no secrets provider; only resolving a stored secret needs one.
        if self._secrets is None:
            raise RuntimeError("the credential resolver has no secrets provider, so it cannot decrypt a stored secret")
        return await self._secrets.decrypt(ciphertext)

    def invalidate(self) -> None:
        """Drop cached forge resolutions. Call after any credential or binding mutation."""
        self._forge_cache.clear()

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def owner_of(repo: str) -> str:
        """Owner scope value: the first path segment, lowercased, with '-' and '.' as '_'.

        Env keys are lossy (``my-org`` -> ``GITHUB_TOKEN__MY_ORG``), so owner bindings imported from
        the environment can only be stored in this normalized form; lookups normalize the same way.
        The repo scope keeps the exact lowercase path.
        """
        owner = repo.split("/", 1)[0] if repo and "/" in repo else ""
        return owner.lower().replace("-", "_").replace(".", "_")

    @staticmethod
    def _check_forge(forge: str) -> None:
        if forge not in FORGES:
            raise ValueError(f"unknown forge {forge!r}")

    def forge_host(self, forge: str) -> str:
        self._check_forge(forge)
        if forge == "gitlab":
            url = str(getattr(self._config, "gitlab_url", "") or "")
            return (urlparse(url).hostname or "gitlab.com").lower()
        return "github.com"

    def env_fallback_keys(self) -> list[str]:
        return sorted(self._env_keys_used)

    def _env_token(self, repo: str, forge: str) -> tuple[str, str]:
        """(token, env_key) exactly as the pre-registry code resolved it."""
        if forge == "gitlab":
            default = str(getattr(self._config, "gitlab_token", "") or "")
            ns = gitlab_tokens.namespace_of(repo)
            key = "GITLAB_TOKEN__" + ns.upper().replace("-", "_").replace(".", "_") if ns else ""
            token = self._environ.get(key, "") if key else ""
            return (token, key) if token else (default, "GITLAB_TOKEN")
        default = str(getattr(self._config, "github_token", "") or "")
        owner = repo.split("/", 1)[0] if repo and "/" in repo else ""
        key = "GITHUB_TOKEN__" + owner.upper().replace("-", "_") if owner else ""
        token = self._environ.get(key, "") if key else ""
        return (token, key) if token else (default, "GITHUB_TOKEN")

    @staticmethod
    def _availability(row: Mapping[str, Any]) -> str | None:
        """None when usable, else the reason it is skipped."""
        status = row.get("status")
        if status == "disabled":
            return f"disabled:{row.get('status_reason') or ''}".rstrip(":")
        if status == "quarantined":
            until = row.get("quarantined_until")
            if until is None:
                return "quarantined:until-cleared"
            if until > datetime.now(UTC):
                return f"quarantined:until-{until.isoformat()}"
        return None

    async def _decrypt_or_disable(self, row: Mapping[str, Any]) -> str | None:
        try:
            return await self._decrypt(str(row["secret_ciphertext"]))
        except InvalidToken:
            logger.error("credential_decryption_failed", credential_id=row["id"], label=row["label"])
            try:
                await self._repo.set_status(str(row["id"]), "disabled", reason="decryption_failed")
            except Exception as exc:  # noqa: BLE001 — a failed write must not abandon later candidates
                logger.warning(
                    "credential_registry_error", op="set_status", credential_id=row["id"], error=str(exc)[:200]
                )
            return None

    async def _record_use(self, row: Mapping[str, Any], *, job_id: str, role: str, provider: str) -> None:
        """A failed usage write must never change which credential the job gets."""
        try:
            await self._repo.record_use(
                credential_id=str(row["id"]),
                credential_label=str(row["label"]),
                job_id=job_id,
                role=role,
                provider=provider,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("credential_registry_error", op="record_use", credential_id=row["id"], error=str(exc)[:200])

    # -- forge ------------------------------------------------------------------

    async def resolve_forge(self, repo: str, forge: str, *, job_id: str = "") -> ResolvedCredential:
        self._check_forge(forge)
        key = ((repo or "").lower(), forge)
        now = self._clock()
        hit = self._forge_cache.get(key)
        if hit is not None and now - hit[0] < self._cache_ttl:
            resolved = hit[1]
            if job_id and resolved.id:
                await self._record_use(
                    {"id": resolved.id, "label": resolved.label}, job_id=job_id, role="forge", provider=forge
                )
            return resolved
        resolved = await self._resolve_forge_uncached(repo, forge, job_id=job_id)
        self._forge_cache[key] = (now, resolved)
        return resolved

    async def _resolve_forge_uncached(self, repo: str, forge: str, *, job_id: str = "") -> ResolvedCredential:
        trace: list[str] = []
        steps = (
            ("repo", repo.lower() if repo else ""),
            ("owner", f"{forge}:{self.owner_of(repo)}"),
            ("host", self.forge_host(forge)),
            ("global", ""),
        )
        try:
            for scope_kind, scope_value in steps:
                if scope_kind != "global" and not scope_value.split(":", 1)[-1]:
                    continue
                rows = await self._repo.candidates(
                    scope_kind=scope_kind, scope_value=scope_value, role="forge", provider=forge
                )
                for row in rows:
                    why = self._availability(row)
                    if why:
                        trace.append(f"{scope_kind}:{scope_value} -> {row['label']} skipped ({why})")
                        if row.get("pinned"):
                            raise CredentialUnavailableError(
                                f"credential '{row['label']}' is pinned for {repo} but {why}"
                            )
                        continue
                    secret = await self._decrypt_or_disable(row)
                    if secret is None:
                        trace.append(f"{scope_kind}:{scope_value} -> {row['label']} skipped (decryption_failed)")
                        if row.get("pinned"):
                            raise CredentialUnavailableError(
                                f"credential '{row['label']}' is pinned for {repo} but decryption_failed"
                            )
                        continue
                    trace.append(f"{scope_kind}:{scope_value} -> {row['label']}")
                    if job_id:
                        await self._record_use(row, job_id=job_id, role="forge", provider=forge)
                    return ResolvedCredential(
                        id=str(row["id"]),
                        kind=str(row["kind"]),
                        provider=str(row["provider"]),
                        host=str(row["host"]),
                        label=str(row["label"]),
                        secret=secret,
                        git_author_name=row.get("git_author_name"),
                        git_author_email=row.get("git_author_email"),
                        source="registry",
                        trace=tuple(trace),
                    )
                trace.append(f"{scope_kind}:{scope_value} -> none")
        except CredentialUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 — a registry outage must not fail a job that env can serve
            logger.warning("credential_registry_error", repo=repo, forge=forge, error=str(exc)[:200])
            trace.append(f"registry error: {type(exc).__name__}")

        token, key = self._env_token(repo, forge)
        if token:
            if key not in self._env_keys_used:
                self._env_keys_used.add(key)
                logger.warning(
                    "credential_env_fallback",
                    env_key=key,
                    forge=forge,
                    msg="token served from the environment; run `embry0 credentials import-env` to move it into the registry",
                )
            trace.append(f"env:{key}")
            return ResolvedCredential(
                id=None,
                kind="forge_token",
                provider=forge,
                host=self.forge_host(forge),
                label=key,
                secret=token,
                git_author_name=None,
                git_author_email=None,
                source="env",
                trace=tuple(trace),
            )
        trace.append(f"env:{key or forge.upper() + '_TOKEN'} -> none")
        raise CredentialMissingError(
            f"no {forge} credential for {repo!r}: bind one in the registry or set "
            f"{'GITLAB_TOKEN' if forge == 'gitlab' else 'GITHUB_TOKEN'} (trace: {'; '.join(trace)})"
        )

    async def has_forge_credential(self, forge: str) -> bool:
        self._check_forge(forge)
        try:
            if await self._repo.any_for_provider(forge):
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("credential_registry_error", forge=forge, error=str(exc)[:200])
        prefix = "GITLAB_TOKEN" if forge == "gitlab" else "GITHUB_TOKEN"
        default = str(getattr(self._config, f"{forge}_token", "") or "")
        return bool(default) or any(k.startswith(prefix + "__") and v for k, v in self._environ.items())

    # -- tailnet ----------------------------------------------------------------

    async def resolve_tailnet(self, *, credential_id: str | None = None) -> ResolvedCredential:
        """The tailnet API key embry0 reads devices with (ADR 0013).

        ``credential_id`` names one; otherwise the ``tailnet`` binding at
        global scope, the only scope that role takes. There is no env
        fallback. The tailnet it reads is ``metadata["tailnet"]``.
        """
        trace: list[str] = []
        if credential_id:
            row = await self._repo.get(credential_id)
            if row is None:
                raise CredentialMissingError(f"no credential {credential_id!r}")
            if row["kind"] != "tailnet_api_key":
                raise CredentialUnavailableError(f"credential '{row['label']}' is not a tailnet_api_key")
            rows = [row]
            trace.append(f"explicit:{credential_id}")
        else:
            rows = await self._repo.candidates(
                scope_kind="global", scope_value="", role="tailnet", provider="tailscale"
            )
        for row in rows:
            why = self._availability(row)
            if why:
                trace.append(f"global: -> {row['label']} skipped ({why})")
                if credential_id:
                    raise CredentialUnavailableError(f"credential '{row['label']}' is {why}")
                continue
            secret = await self._decrypt_or_disable(row)
            if secret is None:
                trace.append(f"global: -> {row['label']} skipped (decryption_failed)")
                if credential_id:
                    raise CredentialUnavailableError(f"credential '{row['label']}' cannot be decrypted; rotate it")
                continue
            trace.append(f"global: -> {row['label']}")
            return ResolvedCredential(
                id=str(row["id"]),
                kind=str(row["kind"]),
                provider=str(row["provider"]),
                host=str(row["host"]),
                label=str(row["label"]),
                secret=secret,
                git_author_name=None,
                git_author_email=None,
                source="registry",
                trace=tuple(trace),
                metadata=dict(row.get("metadata") or {}),
            )
        raise CredentialMissingError(
            "no tailnet credential: create a tailnet_api_key credential and bind it at global scope "
            f"with role tailnet (trace: {'; '.join(trace) or 'global: -> none'})"
        )

    # -- llm --------------------------------------------------------------------

    async def resolve_llm(self, repo: str, provider: str, *, job_id: str = "") -> ResolvedCredential:
        """The key chosen for the job, else repo binding, then owner (github scope, then gitlab), then the global pool, then env."""
        self._check_llm_provider(provider)
        if job_id:
            pinned = await self._job_pin(job_id, provider)
            if pinned:
                return await self._resolve_job_pin(pinned, provider, job_id=job_id)
            reused = await self._reuse_llm(provider, job_id, await self._uses(job_id))
            if reused is not None:
                return reused
        return await self._resolve_llm_fresh(repo, provider, job_id=job_id)

    async def resolve_llm_for_job(
        self,
        repo: str,
        providers: Iterable[str],
        *,
        job_id: str,
        parent_job_id: str | None = None,
    ) -> dict[str, ResolvedCredential]:
        """One credential per provider. A derived sandbox reuses the parent's still-usable choice."""
        parent_uses = await self._uses(parent_job_id) if parent_job_id else []
        resolved: dict[str, ResolvedCredential] = {}
        for provider in providers:
            # A key chosen for this job outranks the parent's: resolve_llm serves it.
            chosen = await self._job_pin(job_id, provider)
            reused = await self._reuse_llm(provider, job_id, parent_uses) if parent_uses and not chosen else None
            resolved[provider] = reused if reused is not None else await self.resolve_llm(repo, provider, job_id=job_id)
        return resolved

    async def job_pinned(self, job_id: str, provider: str) -> bool:
        """Whether a person chose the key that serves ``provider`` for this job (Run with)."""
        return bool(await self._job_pin(job_id, provider))

    async def llm_pause(self, repo: str, provider: str) -> Pause | None:
        """Why ``provider`` has no usable account for ``repo``, or None when it has one.

        Provider fallback (spec 2026-10-25) asks this before it moves a job's
        agents to another provider. It walks the bindings ``resolve_llm`` walks
        and records no use. Paused means every bound account is quarantined or
        disabled and the environment holds none, or holds the same secret as
        one of them: an account imported into the registry keeps its
        environment copy, which would otherwise serve the spent token again.
        A provider this deployment does not enable is paused (``not_enabled``);
        a registry error is not, so a broken read never moves a job. The job's
        own pin is the caller's to check (``job_pinned``).
        """
        if provider not in llm_providers(self._environ):
            return Pause(provider, "not_enabled")
        owner = self.owner_of(repo)
        steps: list[tuple[str, str]] = [("repo", repo.lower())] if repo else []
        if owner:
            steps += [("owner", f"github:{owner}"), ("owner", f"gitlab:{owner}")]
        steps.append(("global", ""))
        unusable: list[Mapping[str, Any]] = []
        try:
            for scope_kind, scope_value in steps:
                for row in await self._repo.candidates(
                    scope_kind=scope_kind, scope_value=scope_value, role="llm", provider=provider
                ):
                    if self._availability(row) is None:
                        return None
                    unusable.append(row)
        except Exception as exc:  # noqa: BLE001 — a registry outage must not move a job off its provider
            logger.warning("credential_registry_error", op="llm_pause", provider=provider, error=str(exc)[:200])
            return None
        fallback = env_fallback(provider, self._environ, self._config, credentials_file=self._credentials_file)
        if fallback is not None and not await self._is_secret_of(fallback[1], unusable):
            return None
        if not unusable:
            return Pause(provider, "missing")
        # The pause ends when the first timed quarantine does; a disabled
        # account, or one quarantined until cleared, names no end.
        timed = [row for row in unusable if isinstance(row.get("quarantined_until"), datetime)]
        first = min(timed, key=lambda row: row["quarantined_until"]) if timed else unusable[0]
        reason = str(first.get("status_reason") or first.get("status") or "paused")
        return Pause(provider, reason, first["quarantined_until"] if timed else None)

    async def pinned_pause(self, job_id: str, provider: str) -> tuple[bool, Pause | None]:
        """``(pinned, pause)``: whether a person chose the key for ``provider``, and why it cannot serve now.

        Spent account waits (spec 2026-10-25): a chosen key never moves, so a
        quarantined one makes the job wait for it. A pin or a row that cannot
        be read is pinned and not paused: resolving it then fails the job as
        before.
        """
        try:
            credential_id = await self._job_pin(job_id, provider)
        except CredentialUnavailableError:
            return True, None
        if not credential_id:
            return False, None
        try:
            row = await self._repo.get(credential_id)
        except Exception as exc:  # noqa: BLE001 — the resolve that follows says what is wrong
            logger.warning("credential_registry_error", op="pinned_pause", provider=provider, error=str(exc)[:200])
            return True, None
        if not row or row.get("status") != "quarantined":
            return True, None
        until = row.get("quarantined_until")
        if not isinstance(until, datetime) or until <= datetime.now(UTC):
            return True, None
        return True, Pause(provider, str(row.get("status_reason") or "quarantined"), until)

    async def job_uses_registry(self, job_id: str, provider: str) -> bool:
        """Whether a registry account served ``provider`` for this job (the use ledger), not the environment."""
        return any(use.get("role") == "llm" and use.get("provider") == provider for use in await self._uses(job_id))

    async def _is_secret_of(self, secret: str, rows: Iterable[Mapping[str, Any]]) -> bool:
        """Whether ``secret`` is the stored secret of one of ``rows``."""
        for row in rows:
            try:
                if await self._decrypt(str(row["secret_ciphertext"])) == secret:
                    return True
            except Exception:  # noqa: BLE001 — an unreadable row is not the same account
                continue
        return False

    async def _job_pin(self, job_id: str, provider: str) -> str | None:
        """The credential id the job chose for ``provider``, or None.

        A pin that cannot be read fails the job: a person chose that key, and
        another one must not run in its place.
        """
        if self._job_pins is None or not job_id:
            return None
        try:
            pins = await self._job_pins(job_id)
        except Exception as exc:
            raise CredentialUnavailableError(f"the key chosen for job {job_id} could not be read: {exc}") from exc
        pinned = pins.get(provider)
        return str(pinned) if pinned else None

    async def _resolve_job_pin(self, credential_id: str, provider: str, *, job_id: str) -> ResolvedCredential:
        """The key chosen for the job; :class:`CredentialUnavailableError` when it cannot serve ``provider``."""
        row = await self._repo.get(credential_id)
        if not row:
            raise CredentialUnavailableError(f"the key chosen for this job ({credential_id}) no longer exists")
        label = str(row.get("label") or credential_id)
        if str(row.get("provider") or "") != provider or str(row.get("kind") or "") not in ("llm_api_key", "llm_oauth"):
            raise CredentialUnavailableError(f"the key chosen for this job, '{label}', is not a {provider} key")
        why = self._availability(row)
        if why:
            raise CredentialUnavailableError(f"the key chosen for this job, '{label}', is {why}")
        secret = await self._decrypt_or_disable(row)
        if secret is None:
            raise CredentialUnavailableError(f"the key chosen for this job, '{label}', could not be decrypted")
        await self._record_use(row, job_id=job_id, role="llm", provider=provider)
        return self._resolved_registry(
            row, secret, (f"job:{job_id} -> {label} (chosen for the job)",), provider=provider
        )

    def _check_llm_provider(self, provider: str) -> None:
        if provider not in llm_providers(self._environ):
            raise ValueError(f"unknown llm provider {provider!r}")

    async def _uses(self, job_id: str) -> list[dict[str, Any]]:
        try:
            return list(await self._repo.uses_for_job(job_id))
        except Exception as exc:  # noqa: BLE001 — a ledger read must not hide an env account
            logger.warning("credential_registry_error", op="uses_for_job", job_id=job_id, error=str(exc)[:200])
            return []

    async def _reuse_llm(self, provider: str, job_id: str, uses: list[dict[str, Any]]) -> ResolvedCredential | None:
        for use in uses:
            if use.get("role") != "llm" or use.get("provider") != provider:
                continue
            try:
                row = await self._repo.get(str(use["credential_id"]))
            except Exception as exc:  # noqa: BLE001
                logger.warning("credential_registry_error", op="get", error=str(exc)[:200])
                return None
            if not row or self._availability(row):
                continue
            secret = await self._decrypt_or_disable(row)
            if secret is None:
                continue
            if job_id:
                await self._record_use(row, job_id=job_id, role="llm", provider=provider)
            return self._resolved_registry(
                row, secret, (f"reuse:{use.get('credential_id')} -> {row['label']}",), provider=provider
            )
        return None

    async def _resolve_llm_fresh(self, repo: str, provider: str, *, job_id: str) -> ResolvedCredential:
        trace: list[str] = []
        owner = self.owner_of(repo)
        steps: list[tuple[str, str]] = []
        if repo:
            steps.append(("repo", repo.lower()))
        if owner:
            # The forge is not an LLM concept; a repo may be bound under either owner prefix.
            steps.append(("owner", f"github:{owner}"))
            steps.append(("owner", f"gitlab:{owner}"))
        steps.append(("global", ""))
        try:
            for scope_kind, scope_value in steps:
                rows = await self._repo.candidates(
                    scope_kind=scope_kind, scope_value=scope_value, role="llm", provider=provider
                )
                for row in rows:
                    why = self._availability(row)
                    if why:
                        trace.append(f"{scope_kind}:{scope_value} -> {row['label']} skipped ({why})")
                        if row.get("pinned") and scope_kind != "global":
                            raise CredentialUnavailableError(
                                f"credential '{row['label']}' is pinned for {repo} but {why}"
                            )
                        continue
                    secret = await self._decrypt_or_disable(row)
                    if secret is None:
                        trace.append(f"{scope_kind}:{scope_value} -> {row['label']} skipped (decryption_failed)")
                        if row.get("pinned") and scope_kind != "global":
                            raise CredentialUnavailableError(
                                f"credential '{row['label']}' is pinned for {repo} but decryption_failed"
                            )
                        continue
                    trace.append(f"{scope_kind}:{scope_value} -> {row['label']}")
                    if job_id:
                        await self._record_use(row, job_id=job_id, role="llm", provider=provider)
                    return self._resolved_registry(row, secret, tuple(trace), provider=provider)
                trace.append(f"{scope_kind}:{scope_value} -> none")
        except CredentialUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 — a registry outage must not fail a job that env can serve
            logger.warning("credential_registry_error", repo=repo, provider=provider, error=str(exc)[:200])
            trace.append(f"registry error: {type(exc).__name__}")

        fallback = env_fallback(provider, self._environ, self._config, credentials_file=self._credentials_file)
        if fallback:
            kind, secret, key = fallback
            if key not in self._env_keys_used:
                self._env_keys_used.add(key)
                logger.warning(
                    "credential_env_fallback",
                    env_key=key,
                    provider=provider,
                    msg="token served from the environment; run `embry0 credentials import-env` to move it into the registry",
                )
            trace.append(f"env:{key}")
            metadata = env_fallback_metadata(provider, self._environ)
            return ResolvedCredential(
                id=None,
                kind=kind,
                provider=provider,
                host=base_url_host(metadata["base_url"])
                if "base_url" in metadata
                else default_host(provider, self._environ),
                label=key,
                secret=secret,
                git_author_name=None,
                git_author_email=None,
                source="env",
                trace=tuple(trace),
                metadata=metadata,
            )
        trace.append(f"env:{provider} -> none")
        raise CredentialMissingError(
            f"no {provider} credential for {repo!r}: bind one in the registry (trace: {'; '.join(trace)})"
        )

    @staticmethod
    def _resolved_registry(
        row: Mapping[str, Any], secret: str, trace: tuple[str, ...], *, provider: str
    ) -> ResolvedCredential:
        return ResolvedCredential(
            id=str(row["id"]),
            kind=str(row["kind"]),
            provider=str(row.get("provider") or provider),
            host=str(row.get("host") or default_host(provider)),
            label=str(row["label"]),
            secret=secret,
            git_author_name=row.get("git_author_name"),
            git_author_email=row.get("git_author_email"),
            source="registry",
            trace=trace,
            # A custom provider's base URL rides here into the sandbox env.
            metadata=dict(row.get("metadata") or {}),
        )

    async def known_secret_values(self, *, refresh: bool = False) -> list[str]:
        """Plaintext of every registry secret, for the log scrubbers. Cached; refresh after create/rotate."""
        if self._known_values is not None and not refresh:
            return self._known_values
        values: list[str] = []
        try:
            for row in await self._repo.all_ciphertexts():
                try:
                    values.append(await self._decrypt(str(row["secret_ciphertext"])))
                except InvalidToken:
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("credential_registry_error", op="known_secret_values", error=str(exc)[:200])
        self._known_values = [v for v in values if len(v) >= 8]
        return self._known_values


async def forge_token(resolver: CredentialResolver | None, repo: str, forge: str, env_default: str) -> str:
    """Bridge for orchestrator call sites: the registry when wired, else the env lookup they used before."""
    if resolver is None:
        if forge == "gitlab":
            return gitlab_tokens.resolve_for_repo(repo, env_default)
        return github_tokens.resolve_for_repo(repo, env_default)
    try:
        return (await resolver.resolve_forge(repo, forge)).secret
    except CredentialMissingError:
        return ""
