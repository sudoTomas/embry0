"""The browser tool arguments the safety policy refuses (Ring 3 argument checks).

vibium 26.11's browser_find and browser_find_all take a model provider, model,
endpoint and effort per call; with a provider that needs no key and an endpoint
of the call's choosing they send the page's screenshot there, and strategy vlm
asks a model by name. browser_record_stop's upload sends the recording to a
remote station. A QA agent is granted those tools, so the calls that would use
those arguments are refused, whatever the server is configured with.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.safety.policy import (
    BROWSER_ARGUMENT_CHECKS,
    ArgumentCheck,
    SafetyPolicy,
    default_policy_for_agent,
    evaluate_policy,
)

FIND = "mcp__vibium__browser_find"
FIND_ALL = "mcp__vibium__browser_find_all"
RECORD_STOP = "mcp__vibium__browser_record_stop"


def _qa_policy(*tools: str) -> SafetyPolicy:
    """The QA agent's baseline policy, granted the vibium tools under test."""
    import dataclasses

    base = default_policy_for_agent("qa")
    return dataclasses.replace(base, allowed_tools=[*(base.allowed_tools or []), *tools])


@pytest.mark.parametrize("tool", [FIND, FIND_ALL])
@pytest.mark.parametrize(
    "arguments",
    [
        {"provider": "openai-compatible", "model": "m", "aiBaseURL": "http://203.0.113.5/v1"},
        {"provider": "local"},
        {"model": "a-vision-model"},
        {"aiBaseURL": "http://203.0.113.5/v1"},
        {"reasoningEffort": "high"},
        {"strategy": "vlm"},
        {"provider": ""},
    ],
    ids=["provider+model+endpoint", "provider", "model", "endpoint", "effort", "vlm", "empty-provider"],
)
def test_a_find_that_could_reach_a_model_is_refused(tool: str, arguments: dict[str, Any]) -> None:
    verdict = evaluate_policy(_qa_policy(tool), tool, {"description": "the Sign In button", **arguments})
    assert not verdict.allowed, f"{tool} {arguments} was allowed"
    assert "model-backed locating is not available" in verdict.reason, verdict.reason


@pytest.mark.parametrize("tool", [FIND, FIND_ALL])
@pytest.mark.parametrize(
    "arguments",
    [
        {"css": "h1"},
        {"role": "button", "text": "Save"},
        {"description": "the Sign In button"},
        {"description": "the Sign In button", "strategy": "auto"},
        {"selector": "#login", "strategy": "tree"},
        {"image": "data:image/png;base64,AAAA", "strategy": "cv"},
        {"css": "h1", "strategy": None},
    ],
    ids=["css", "role+text", "description", "auto", "tree", "cv", "null-strategy"],
)
def test_a_find_that_stays_local_is_allowed(tool: str, arguments: dict[str, Any]) -> None:
    verdict = evaluate_policy(_qa_policy(tool), tool, arguments)
    assert verdict.allowed, f"{tool} {arguments} was refused: {verdict.reason}"


@pytest.mark.parametrize("upload", [True, "https://station.example/", 1])
def test_a_recording_is_never_uploaded_from_the_sandbox(upload: Any) -> None:
    verdict = evaluate_policy(_qa_policy(RECORD_STOP), RECORD_STOP, {"path": "/workspace/x.zip", "upload": upload})
    assert not verdict.allowed and "a recording stays in the sandbox" in verdict.reason, verdict


@pytest.mark.parametrize("arguments", [{"path": "/workspace/.qa/traces/session.zip"}, {"upload": False}, {}])
def test_a_recording_saved_in_the_sandbox_is_allowed(arguments: dict[str, Any]) -> None:
    verdict = evaluate_policy(_qa_policy(RECORD_STOP), RECORD_STOP, arguments)
    assert verdict.allowed, verdict.reason


def test_the_checks_read_only_their_own_tools() -> None:
    """Another server's tool with the same argument names is not theirs to judge."""
    tool = "mcp__playwright__browser_find"
    assert evaluate_policy(_qa_policy(tool), tool, {"provider": "x", "upload": True}).allowed


def test_every_agent_s_baseline_policy_carries_the_checks() -> None:
    for agent_type in ("qa", "developer", "triage"):
        assert default_policy_for_agent(agent_type).argument_checks == list(BROWSER_ARGUMENT_CHECKS), agent_type


def test_the_sandbox_builds_its_policy_with_the_checks() -> None:
    """The policy the sandbox enforces is built in the sandbox, from the agent type: it must carry them."""
    from embry0.sandbox.runner import _invocation_from_config

    invocation = _invocation_from_config({"prompt": "p", "agent_type": "qa", "tools": [FIND, RECORD_STOP]})
    policy = invocation.safety_policy
    assert policy.argument_checks == list(BROWSER_ARGUMENT_CHECKS)
    assert not evaluate_policy(policy, FIND, {"provider": "local", "model": "m"}).allowed
    assert not evaluate_policy(policy, RECORD_STOP, {"upload": True}).allowed
    assert evaluate_policy(policy, FIND, {"css": "h1"}).allowed


def test_an_argument_check_tells_values_apart_by_type() -> None:
    """True is not the allowed 1, and "1" is not 1: an allowed value must match in type too."""
    check = ArgumentCheck(tools=("t",), argument="a", allowed=(1, "cv"))
    assert not check.refuses("t", {"a": 1}) and not check.refuses("t", {"a": "cv"})
    assert check.refuses("t", {"a": True}) and check.refuses("t", {"a": "1"}) and check.refuses("t", {"a": [1]})
    assert not check.refuses("t", {}) and not check.refuses("t", {"a": None}) and not check.refuses("t", {"a": False})
    assert not check.refuses("other", {"a": 2})
