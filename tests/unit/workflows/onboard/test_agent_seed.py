"""Onboarding agent seed — sync + shape tests (EMB-50)."""

from __future__ import annotations

from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from embry0.workflows.onboard.agent_seed import ONBOARDING_AGENT_SEED


def test_onboarding_seed_in_sync_with_builtin_seed():
    """BUILTIN_SEED's onboarding entry must match ONBOARDING_AGENT_SEED —
    same seam-keeping test as the qa agent's."""
    builtin = BUILTIN_SEED["onboarding"]
    for key in (
        "description",
        "model",
        "tools",
        "skills",
        "system_prompt",
        "execution_mode",
        "auth_mode",
        "mcp_servers",
    ):
        assert builtin[key] == ONBOARDING_AGENT_SEED[key], f"drift on {key!r}"


def test_prompt_teaches_output_contract():
    prompt = ONBOARDING_AGENT_SEED["system_prompt"]
    assert "/workspace/.onboard/qa.yaml" in prompt
    assert "/workspace/.onboard/notes.md" in prompt
    assert "version: 2" in prompt


def test_agent_has_write_but_no_browser_tools():
    tools = ONBOARDING_AGENT_SEED["tools"]
    assert "Write" in tools
    assert not any(t.startswith("mcp__playwright") for t in tools)
