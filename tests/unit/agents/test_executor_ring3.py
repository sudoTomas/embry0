"""Unit tests for the boot-time SDK hooks self-check."""

from unittest.mock import patch

import pytest

from embry0.agents.executor import _assert_sdk_supports_hooks


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


# ---------------------------------------------------------------------------
# EMB-37 hotfix: hook-input tool_name extraction
# ---------------------------------------------------------------------------


def test_extract_hook_call_from_dict_input():
    """The SDK's PreToolUseHookInput is a TypedDict — a plain dict at runtime.
    getattr() on it always returned None, so tool_name arrived as '' at
    evaluate_policy: tool-scoped content rules never matched, and the EMB-37
    name check denied EVERY call ('tool '' is not in this agent's allowlist',
    observed on job-616393868ba6)."""
    from embry0.agents.executor import _extract_hook_call

    name, tool_input = _extract_hook_call(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_use_id": "t1"}
    )
    assert name == "Bash"
    assert tool_input == {"command": "ls"}


def test_extract_hook_call_from_object_input():
    from embry0.agents.executor import _extract_hook_call

    class _Obj:
        tool_name = "Read"
        tool_input = {"file_path": "/x"}

    name, tool_input = _extract_hook_call(_Obj())
    assert name == "Read"
    assert tool_input == {"file_path": "/x"}


def test_extract_hook_call_defends_malformed_input():
    from embry0.agents.executor import _extract_hook_call

    name, tool_input = _extract_hook_call({"tool_input": "not-a-dict"})
    assert name == ""
    assert tool_input == {}
