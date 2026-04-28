"""Unit tests for the boot-time SDK hooks self-check."""

from unittest.mock import patch

import pytest

from athanor.agents.executor import _assert_sdk_supports_hooks


def test_real_sdk_passes():
    """With the actually-installed claude_agent_sdk, the check passes silently."""
    _assert_sdk_supports_hooks()


def test_raises_when_hooks_attribute_silently_dropped():
    """Simulate an old SDK version where assigning .hooks is silently swallowed."""

    class FakeOptions:
        def __setattr__(self, name, value):
            if name == "hooks":
                # Intentionally drop — older SDK behaviour we want to detect.
                return
            super().__setattr__(name, value)

    fake_module = type("M", (), {"ClaudeAgentOptions": FakeOptions})
    with patch.dict("sys.modules", {"claude_agent_sdk": fake_module}):
        with pytest.raises(RuntimeError, match="does not expose a writable `hooks`"):
            _assert_sdk_supports_hooks()


def test_raises_when_sdk_missing(monkeypatch):
    """Simulate uninstalled SDK."""
    import builtins

    real_import = builtins.__import__

    def block_sdk(name, *args, **kwargs):
        if name == "claude_agent_sdk":
            raise ImportError("No module named 'claude_agent_sdk'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_sdk)
    with pytest.raises(RuntimeError, match="not importable"):
        _assert_sdk_supports_hooks()
