"""What an agent image's Claude Agent SDK must provide, checked where agents run (#497).

Every agent ``query()`` runs inside a sandbox image, and the SDK runs the CLI it
bundles (``claude_agent_sdk/_bundled/claude``): embry0 never passes
``cli_path``. So the SDK and CLI that matter are the sandbox image's, not the
orchestrator's. The sandbox image builds run ``python -m
embry0.agents.sdk_support`` and fail on either problem below;
``SdkAgentExecutor`` re-checks hook support before every run, for an image
built some other way (a sandbox profile may name any ``base_image``).
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
import sys
import typing
from collections.abc import Callable
from pathlib import Path


class SdkSupportError(RuntimeError):
    """The installed SDK or its CLI cannot run embry0 agents as designed."""


def _literal_values(annotation: object) -> set[str]:
    """Every string a ``Literal`` (or a union of them) admits."""
    if typing.get_origin(annotation) is typing.Literal:
        return {str(value) for value in typing.get_args(annotation)}
    return {value for arg in typing.get_args(annotation) for value in _literal_values(arg)}


def assert_sdk_supports_hooks() -> None:
    """Refuse an SDK that cannot carry the Ring-3 ``PreToolUse`` hook.

    Without the hook, the safety policy's tool-name and command checks never
    run, and the CLI executes whatever the model asks for. Assigning
    ``options.hooks`` proves nothing on its own: a plain dataclass accepts any
    attribute, so the option must be declared, and ``PreToolUse`` must be an
    event the SDK forwards.
    """
    try:
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
        from claude_agent_sdk.types import HookEvent
    except ImportError as exc:
        raise SdkSupportError(f"claude_agent_sdk with hook support is not importable: {exc}") from exc

    declared = (
        {field.name for field in dataclasses.fields(ClaudeAgentOptions)}
        if dataclasses.is_dataclass(ClaudeAgentOptions)
        else set()
    )
    if "hooks" not in declared:
        raise SdkSupportError(
            "claude_agent_sdk.ClaudeAgentOptions declares no `hooks` option, so the Ring-3 "
            "PreToolUse hook cannot be enforced"
        )
    if "PreToolUse" not in _literal_values(HookEvent):
        raise SdkSupportError("claude_agent_sdk does not support the PreToolUse hook event")
    # A call whose hook does not complete is refused (executor.gate_hooks): HookMatcher(on_failure="block"), in
    # claude-agent-sdk 0.2.177 and later.
    on_failure = (
        next((f.type for f in dataclasses.fields(HookMatcher) if f.name == "on_failure"), None)
        if dataclasses.is_dataclass(HookMatcher)
        else None
    )
    hints = typing.get_type_hints(HookMatcher) if on_failure is not None else {}
    if "block" not in _literal_values(hints.get("on_failure")):
        raise SdkSupportError(
            "claude_agent_sdk.HookMatcher cannot block a call whose hook fails (on_failure='block'), which the "
            "Ring-3 PreToolUse hook needs: claude-agent-sdk 0.2.177 or later"
        )


def _cli_version(path: str | Path) -> str:
    """The version a Claude Code binary reports, e.g. ``2.1.292``."""
    try:
        proc = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SdkSupportError(f"cannot run {path} --version: {exc}") from exc
    words = proc.stdout.split()
    if proc.returncode != 0 or not words:
        raise SdkSupportError(f"{path} --version exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()!r}")
    return words[0]


def assert_one_cli_version(
    *,
    bundled: Path | None = None,
    pinned: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """The one Claude Code version this image can run; raise when there are two.

    The SDK runs its bundled CLI first and falls back to ``claude`` on PATH
    only when the bundle is missing, so the bundle must exist and must be the
    version the SDK pins. Any ``claude`` on PATH must be that version too:
    a second, unused CLI is what made changelog reviews read the wrong binary.
    """
    if bundled is None or pinned is None:
        import claude_agent_sdk
        from claude_agent_sdk._cli_version import __cli_version__

        bundled = bundled or Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
        pinned = pinned or __cli_version__
    if not bundled.is_file():
        raise SdkSupportError(
            f"claude_agent_sdk has no bundled CLI at {bundled}; the SDK would fall back to any `claude` on PATH"
        )
    bundled_version = _cli_version(bundled)
    if bundled_version != pinned:
        raise SdkSupportError(f"bundled CLI {bundled} is {bundled_version}, but the SDK pins {pinned}")
    on_path = which("claude")
    if on_path is not None:
        path_version = _cli_version(on_path)
        if path_version != bundled_version:
            raise SdkSupportError(
                f"`claude` on PATH ({on_path}) is {path_version}, but the SDK runs its bundled CLI "
                f"{bundled_version}: one image, two CLI versions"
            )
    return bundled_version


def main() -> int:
    """Image build check: hook support plus a single CLI version."""
    try:
        assert_sdk_supports_hooks()
        cli = assert_one_cli_version()
    except SdkSupportError as exc:
        print(f"agent SDK check failed: {exc}", file=sys.stderr)
        return 1
    import claude_agent_sdk

    print(f"claude-agent-sdk {claude_agent_sdk.__version__}: PreToolUse hooks supported; runs bundled CLI {cli}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
