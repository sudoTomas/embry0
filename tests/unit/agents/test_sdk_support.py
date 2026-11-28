"""The agent image's SDK check: Ring-3 hook support and one CLI version (#497)."""

from __future__ import annotations

import builtins
import dataclasses
import sys
import types
from pathlib import Path
from typing import Any, Literal

import pytest

from embry0.agents.sdk_support import (
    SdkSupportError,
    assert_one_cli_version,
    assert_sdk_supports_hooks,
    main,
)


@dataclasses.dataclass
class _Matcher:
    """HookMatcher as the SDK declares it since 0.2.177: a failed callback can block its call."""

    matcher: str | None = None
    hooks: list[Any] = dataclasses.field(default_factory=list)
    on_failure: Literal["continue", "block"] | None = None


def _fake_sdk(
    monkeypatch: pytest.MonkeyPatch, options_cls: type, hook_event: Any, matcher_cls: type = _Matcher
) -> None:
    """Install a stand-in ``claude_agent_sdk`` (and ``.types``) for one test."""
    package = types.ModuleType("claude_agent_sdk")
    package.ClaudeAgentOptions = options_cls  # type: ignore[attr-defined]
    package.HookMatcher = matcher_cls  # type: ignore[attr-defined]
    sdk_types = types.ModuleType("claude_agent_sdk.types")
    sdk_types.HookEvent = hook_event  # type: ignore[attr-defined]
    package.types = sdk_types  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", package)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk.types", sdk_types)


def test_the_installed_sdk_supports_hooks() -> None:
    assert_sdk_supports_hooks()


def test_an_options_class_without_a_hooks_option_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dataclass accepts ``opts.hooks = ...`` without declaring it, so assignment proves nothing."""

    @dataclasses.dataclass
    class OptionsWithoutHooks:
        model: str | None = None

    opts = OptionsWithoutHooks()
    opts.hooks = {"PreToolUse": []}  # type: ignore[attr-defined]  # what the old check relied on
    _fake_sdk(monkeypatch, OptionsWithoutHooks, Literal["PreToolUse"])

    with pytest.raises(SdkSupportError, match="declares no `hooks` option"):
        assert_sdk_supports_hooks()


def test_an_sdk_without_the_pre_tool_use_event_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclasses.dataclass
    class Options:
        hooks: dict[str, Any] | None = None

    _fake_sdk(monkeypatch, Options, Literal["PostToolUse"] | Literal["Stop"])

    with pytest.raises(SdkSupportError, match="PreToolUse"):
        assert_sdk_supports_hooks()


@pytest.mark.parametrize(
    "matcher_cls",
    [
        dataclasses.make_dataclass("HookMatcher", [("matcher", str | None, None)]),
        dataclasses.make_dataclass("HookMatcher", [("on_failure", Literal["continue"] | None, None)]),
        type("HookMatcher", (), {"on_failure": None}),
    ],
    ids=["no-on-failure", "cannot-block", "not-a-dataclass"],
)
def test_an_sdk_whose_hooks_cannot_block_on_failure_is_refused(
    monkeypatch: pytest.MonkeyPatch, matcher_cls: type
) -> None:
    """The Ring-3 hook refuses a call whose check does not complete, which needs HookMatcher(on_failure='block')."""

    @dataclasses.dataclass
    class Options:
        hooks: dict[str, Any] | None = None

    _fake_sdk(monkeypatch, Options, Literal["PreToolUse"], matcher_cls)

    with pytest.raises(SdkSupportError, match="on_failure='block'"):
        assert_sdk_supports_hooks()


def test_an_sdk_with_hooks_and_pre_tool_use_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fakes above fail for their one missing piece, not for being fakes."""

    @dataclasses.dataclass
    class Options:
        hooks: dict[str, Any] | None = None

    _fake_sdk(monkeypatch, Options, Literal["PostToolUse"] | Literal["PreToolUse"])

    assert_sdk_supports_hooks()


def test_a_missing_sdk_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def block_sdk(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("claude_agent_sdk"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_sdk)
    with pytest.raises(SdkSupportError, match="not importable"):
        assert_sdk_supports_hooks()


def _cli(tmp_path: Path, name: str, version: str) -> Path:
    path = tmp_path / name
    path.write_text(f'#!/bin/sh\necho "{version} (Claude Code)"\n')
    path.chmod(0o755)
    return path


def test_the_bundled_cli_is_the_one_version(tmp_path: Path) -> None:
    bundled = _cli(tmp_path, "bundled", "2.1.292")
    assert assert_one_cli_version(bundled=bundled, pinned="2.1.292", which=lambda _name: None) == "2.1.292"


def test_a_claude_on_path_at_the_same_version_is_allowed(tmp_path: Path) -> None:
    bundled = _cli(tmp_path, "bundled", "2.1.292")
    on_path = _cli(tmp_path, "claude", "2.1.292")
    assert assert_one_cli_version(bundled=bundled, pinned="2.1.292", which=lambda _name: str(on_path)) == "2.1.292"


def test_a_second_cli_version_on_path_is_refused(tmp_path: Path) -> None:
    """The drift #497 found: npm's 2.1.263 on PATH while the SDK ran its bundled 2.1.139."""
    bundled = _cli(tmp_path, "bundled", "2.1.292")
    on_path = _cli(tmp_path, "claude", "2.1.263")
    with pytest.raises(SdkSupportError, match="two CLI versions"):
        assert_one_cli_version(bundled=bundled, pinned="2.1.292", which=lambda _name: str(on_path))


def test_a_missing_bundled_cli_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SdkSupportError, match="no bundled CLI"):
        assert_one_cli_version(bundled=tmp_path / "absent", pinned="2.1.292", which=lambda _name: None)


def test_a_bundled_cli_that_is_not_the_pinned_version_is_refused(tmp_path: Path) -> None:
    bundled = _cli(tmp_path, "bundled", "2.1.139")
    with pytest.raises(SdkSupportError, match="SDK pins 2.1.292"):
        assert_one_cli_version(bundled=bundled, pinned="2.1.292", which=lambda _name: None)


def test_the_build_check_exits_non_zero_on_a_failure(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import embry0.agents.sdk_support as sdk_support

    def refuse() -> None:
        raise SdkSupportError("no hooks here")

    monkeypatch.setattr(sdk_support, "assert_sdk_supports_hooks", refuse)
    assert main() == 1
    assert "no hooks here" in capsys.readouterr().err
