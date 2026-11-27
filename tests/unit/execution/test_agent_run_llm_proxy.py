"""A run whose provider is reached through the llm-proxy (ADR 0033): its record points there, the runner is told so by a
flag, and an image whose runner predates the route is refused before any call (embry0.execution.agent_runner)."""

from __future__ import annotations

import json
from typing import Any

import pytest
import structlog.testing

from embry0.execution import agent_runner
from tests.unit.execution.test_agent_run_result import _FINAL, _STARTED, _calls, _proc, _refused_as_stale, _runner


def _record(name: str, runtime: str = "chat_completions") -> dict[str, Any]:
    return {
        "name": name,
        "runtime": runtime,
        "upstream_model": "m",
        "rates": [1.0, 2.0],
        "cache_read_factor": 0.25,
        "cache_write_factor": 1.25,
        "base_url": "",
        "api_root": f"https://api.{name}.example/v1" if runtime == "chat_completions" else "",
        "key_env": f"EMBRY0_LLM_{name.upper()}_API_KEY",
        "base_url_env": "",
        "request_extras": {},
        "context_window": None,
        "resolved_at": "2026-11-26T00:00:00+00:00",
        "via_proxy": False,
    }


async def _run(procs: list[Any], record: dict[str, Any] | None) -> tuple[Any, list[dict[str, Any]], Any, list[str]]:
    runner, stale = _runner(procs)
    config: dict[str, Any] = {"agent_type": "developer", "prompt": "fix it"}
    if record is not None:
        config["provider_record"] = record
    with structlog.testing.capture_logs() as logs:
        output = await runner.run(container="c", config=config)
    return output, logs, runner, stale


def _sent(runner: Any) -> tuple[list[str], dict[str, Any]]:
    call = _calls(runner)[0]
    return list(call["command"]), json.loads(call["stdin_data"])


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openrouter", "google", "groq", "deepseek"])
async def test_a_hosted_provider_s_run_is_routed_through_the_llm_proxy(provider: str) -> None:
    output, _, runner, _ = await _run([_proc([_STARTED, _FINAL])], _record(provider))

    command, config = _sent(runner)
    record = config["provider_record"]
    assert (record["api_root"], record["via_proxy"]) == (f"http://llm-proxy:9108/p/{provider}/v1", True), (
        f"the run's record does not point at the proxy: {record}"
    )
    assert "--llm-proxy" in command, f"the runner is not told the run is routed: {command}"
    assert output.is_error is False, output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        pytest.param(_record("custom"), id="custom"),
        pytest.param(_record("anthropic", runtime="sdk"), id="anthropic"),
        pytest.param(_record("xai", runtime="sdk"), id="xai"),
        pytest.param(None, id="no-record"),
    ],
)
async def test_any_other_run_is_left_as_it_was(record: dict[str, Any] | None) -> None:
    """Custom keeps the root its sandbox's env names (ADR 0032); the SDK providers do not pass the llm-proxy."""
    _, _, runner, _ = await _run([_proc([_STARTED, _FINAL])], record)
    command, config = _sent(runner)
    assert "--llm-proxy" not in command, f"a run not routed was flagged: {command}"
    assert config.get("provider_record") == record, "a record not routed was rewritten"


@pytest.mark.asyncio
async def test_an_image_whose_runner_predates_the_route_is_refused() -> None:
    usage = (
        b"usage: runner.py [-h] (--config CONFIG | --config-stdin) [--session-blob SESSION_BLOB]\n"
        b"                 [--session-id SESSION_ID] [--result-nonce]\n"
        b"runner.py: error: unrecognized arguments: --llm-proxy\n"
    )
    output, logs, runner, stale = await _run(
        [_proc([], returncode=2, stderr=usage), _proc([_STARTED, _FINAL])], _record("openai")
    )

    assert len(_calls(runner)) == 1, "the image's runner refused the route and was run again without it"
    _refused_as_stale(output, logs, stale, "llm_proxy_missing", "--llm-proxy")


@pytest.mark.asyncio
async def test_the_route_refusal_needs_the_route_flag_named() -> None:
    """A runner that refuses another argument is no lack of the route."""
    usage = b"usage: runner.py [-h]\nrunner.py: error: unrecognized arguments: --zz-other\n"
    output, _, _, stale = await _run([_proc([], returncode=2, stderr=usage)], _record("openai"))
    assert stale == [], f"a runner refusing another argument was taken for one without the route: {stale}"
    assert output.error_code != "ERR_SANDBOX_IMAGE_STALE", output


def test_the_runner_takes_the_route_flag() -> None:
    from embry0.sandbox import runner

    try:
        config, blob, session = runner._read_config(["--config", "{}", "--result-nonce", "--llm-proxy"])
    except SystemExit:
        pytest.fail("the runner refuses the route's flag")
    assert (config, blob, session) == ({}, None, None)
    assert getattr(agent_runner, "LLM_PROXY_FLAG", None) == "--llm-proxy", "the orchestrator names the flag otherwise"
