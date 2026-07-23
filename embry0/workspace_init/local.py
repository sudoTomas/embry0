"""Local workspace initializer — allowlisted orchestrator paths, tar-streamed in.

``local`` contexts stage a directory (or single file) the ORCHESTRATOR can
see into the sandbox's ``/workspace``. That is a real exfiltration surface
(the orchestrator sees its own source, /proc, mounted volumes), so:

- the allowlist is authoritative and fails closed (empty = disabled);
- the source path is resolved (symlink-safe) before the allowlist check;
- the archive is built by us with a filter — devices/FIFOs/hardlinks are
  skipped and symlinks whose resolved target escapes the source root are
  dropped — and bounded by byte/file caps.
"""

from __future__ import annotations

import tarfile
import tempfile
from pathlib import Path
from typing import Any

import structlog

from embry0.workspace_init.base import InitContext, LocalContextDeniedError

logger = structlog.get_logger(__name__)

_DEFAULT_MAX_BYTES = 209_715_200  # 200 MiB
_DEFAULT_MAX_FILES = 50_000


def _allowlist_roots(config: Any | None) -> list[Path]:
    raw = getattr(config, "local_context_allowlist", "") or ""
    return [Path(p.strip()).resolve() for p in raw.split(",") if p.strip()]


def _resolve_allowed(path_str: str, config: Any | None) -> Path:
    """Resolve the source path and require it inside an allowlisted root."""
    roots = _allowlist_roots(config)
    if not roots:
        raise LocalContextDeniedError("local contexts are disabled — LOCAL_CONTEXT_ALLOWLIST is empty (fail closed)")
    try:
        resolved = Path(path_str).resolve(strict=True)
    except OSError as exc:
        raise LocalContextDeniedError(f"local context path {path_str!r} does not resolve: {exc}") from exc
    for root in roots:
        if resolved == root or resolved.is_relative_to(root):
            return resolved
    raise LocalContextDeniedError(f"local context path {path_str!r} is outside the allowlist")


def build_workspace_tar(source: Path, dest_path: Path, *, max_bytes: int, max_files: int) -> tuple[int, int]:
    """Write a filtered tar of ``source`` to ``dest_path``; returns (files, bytes).

    Members are stored relative to the source root (a single file becomes
    one member). Raises LocalContextDeniedError when a cap is breached.
    """
    total_bytes = 0
    total_files = 0

    def _within_caps(size: int) -> None:
        nonlocal total_bytes, total_files
        total_files += 1
        total_bytes += size
        if total_files > max_files:
            raise LocalContextDeniedError(f"local context exceeds {max_files} files")
        if total_bytes > max_bytes:
            raise LocalContextDeniedError(f"local context exceeds {max_bytes} bytes")

    with tarfile.open(dest_path, mode="w") as tar:
        entries = [source] if source.is_file() else sorted(source.rglob("*"))
        for entry in entries:
            arcname = entry.name if source.is_file() else str(entry.relative_to(source))
            # Belt-and-braces: we build the archive ourselves, but never emit
            # a member name that could extract outside /workspace.
            if arcname.startswith("/") or ".." in Path(arcname).parts:
                raise LocalContextDeniedError(f"refusing unsafe archive member name {arcname!r}")
            if entry.is_symlink():
                # Ship a symlink only when its resolved target stays inside the
                # source root — escaping links would read through at extract/use.
                try:
                    target = entry.resolve(strict=True)
                except OSError:
                    logger.warning("local_context_symlink_dropped", member=arcname, reason="dangling")
                    continue
                if not (target == source or target.is_relative_to(source)):
                    logger.warning("local_context_symlink_dropped", member=arcname, reason="escapes source root")
                    continue
                _within_caps(0)
                tar.add(entry, arcname=arcname, recursive=False)
            elif entry.is_dir():
                tar.add(entry, arcname=arcname, recursive=False)
            elif entry.is_file():
                _within_caps(entry.stat().st_size)
                tar.add(entry, arcname=arcname, recursive=False)
            else:
                # sockets / FIFOs / devices
                logger.warning("local_context_special_file_skipped", member=arcname)
    return total_files, total_bytes


class LocalWorkspaceInitializer:
    name = "local"

    def validate(self, context: dict[str, Any], config: Any | None) -> None:
        _resolve_allowed(context.get("path") or "", config)

    async def initialize(self, ctx: InitContext) -> dict[str, Any]:
        source = _resolve_allowed(ctx.context.get("path") or "", ctx.config)
        max_bytes = int(getattr(ctx.config, "local_context_max_bytes", _DEFAULT_MAX_BYTES))
        max_files = int(getattr(ctx.config, "local_context_max_files", _DEFAULT_MAX_FILES))

        with tempfile.NamedTemporaryFile(prefix="embry0-local-ctx-", suffix=".tar", delete=False) as tmp:
            tar_path = Path(tmp.name)
        try:
            files, size = build_workspace_tar(source, tar_path, max_bytes=max_bytes, max_files=max_files)
            await ctx.docker.run_cmd(
                ctx.docker.build_exec_cmd(ctx.container_id, ["mkdir", "-p", "/workspace"]),
                timeout=10,
            )
            await ctx.docker.exec_with_stdin(
                ctx.container_id,
                ["tar", "-xpf", "-", "-C", "/workspace"],
                str(tar_path),
                timeout=300,
            )
        finally:
            tar_path.unlink(missing_ok=True)

        ctx.emit({"type": "progress", "message": f"Staged {files} files ({size} bytes) into /workspace"})
        logger.info("local_context_staged", job_id=ctx.job_id, source=str(source), files=files, bytes=size)
        return {}
