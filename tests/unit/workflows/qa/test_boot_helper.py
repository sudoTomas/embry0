"""run_boot_phase: deterministic boot orchestration with hard timeout.

Post-Phase-4 fix: ready_check probes execute INSIDE the sandbox via
`docker exec python3 -c '...probe...'`. The probe emits stdout in the
shape `STATUS=<int>\\n---\\n<body>`. Tests mock `docker.run_cmd` with
side_effects so the boot command call (first call, returns
"boot_command_backgrounded") is distinguished from probe calls (return
the STATUS=...---...body shape).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.workflows.qa.boot import run_boot_phase


def _make_qa_yaml(boot_timeout: int = 30, ready_checks: list | None = None) -> dict:
    if ready_checks is None:
        ready_checks = [{"http": "http://gateway:8080/health", "expect_status": 200}]
    return {
        "mode": "dind",
        "startup": {
            "command": "cd infra && docker compose up -d gateway",
            "ready_checks": ready_checks,
            "boot_timeout_seconds": boot_timeout,
        },
        "frontend_url": "http://gateway:8080",
    }


def _probe_response(status: int, body: str = "") -> str:
    """Build a probe-protocol response string (STATUS=<n>\\n---\\n<body>)."""
    return f"STATUS={status}\n---\n{body}"


def _make_docker(probe_returns):
    """Construct a docker mock whose run_cmd:
      - returns 'boot_command_backgrounded' for the first call (boot command)
      - then walks ``probe_returns`` for each subsequent call (ready checks)

    ``probe_returns`` is either a list of strings (one per probe call) OR a
    single string (returned for every probe call).
    """
    docker = MagicMock()
    if isinstance(probe_returns, list):
        side_effects = ["boot_command_backgrounded"] + probe_returns
        docker.run_cmd = AsyncMock(side_effect=side_effects)
    else:
        # Single value: return the boot ack on first call, then the same
        # probe response forever.
        call_count = {"n": 0}

        async def _run_cmd(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "boot_command_backgrounded"
            return probe_returns

        docker.run_cmd = AsyncMock(side_effect=_run_cmd)
    return docker


@pytest.mark.asyncio
async def test_all_checks_pass_immediately():
    docker = _make_docker([_probe_response(200, "OK")])

    result = await run_boot_phase(
        qa_yaml=_make_qa_yaml(),
        container_id="C",
        docker=docker,
        sleep_seconds=0,
    )
    assert result.outcome == "passed"
    assert result.attempts == 1
    # 1 boot launch + 1 probe = 2 docker.run_cmd calls
    assert docker.run_cmd.await_count == 2


@pytest.mark.asyncio
async def test_startup_command_fails_returns_startup_failed():
    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=RuntimeError("compose error"))

    result = await run_boot_phase(
        qa_yaml=_make_qa_yaml(),
        container_id="C",
        docker=docker,
        sleep_seconds=0,
    )
    assert result.outcome == "startup_failed"
    assert "compose error" in result.error_message


@pytest.mark.asyncio
async def test_polls_until_pass():
    docker = _make_docker(
        [
            _probe_response(503, "boot in progress"),
            _probe_response(503, "boot in progress"),
            _probe_response(200, "OK"),
        ]
    )

    result = await run_boot_phase(
        qa_yaml=_make_qa_yaml(boot_timeout=10),
        container_id="C",
        docker=docker,
        sleep_seconds=0,
    )
    assert result.outcome == "passed"
    assert result.attempts == 3
    # 1 boot + 3 probes = 4 calls
    assert docker.run_cmd.await_count == 4


@pytest.mark.asyncio
async def test_hits_hard_timeout():
    """Permanent 503 → outcome=timeout after boot_timeout_seconds elapsed."""
    docker = _make_docker(_probe_response(503, "never ready"))

    result = await run_boot_phase(
        qa_yaml=_make_qa_yaml(boot_timeout=1),
        container_id="C",
        docker=docker,
        sleep_seconds=0.05,
    )
    assert result.outcome == "timeout"
    assert result.attempts > 5
    assert result.duration_ms >= 1000
    assert "gateway" in result.failed_checks[0]


@pytest.mark.asyncio
async def test_multiple_ready_checks_all_must_pass():
    """If qa.yaml has 2 ready_checks, both must return 200 in the same attempt."""
    docker = _make_docker(
        [
            # Attempt 1: gateway 200, frontend 503 → fails
            _probe_response(200, "OK"),
            _probe_response(503, "boot"),
            # Attempt 2: both 200 → passes
            _probe_response(200, "OK"),
            _probe_response(200, "OK"),
        ]
    )

    qa_yaml = _make_qa_yaml(
        boot_timeout=10,
        ready_checks=[
            {"http": "http://gateway:8080/health", "expect_status": 200},
            {"http": "http://frontend:3000/", "expect_status": 200},
        ],
    )
    result = await run_boot_phase(
        qa_yaml=qa_yaml,
        container_id="C",
        docker=docker,
        sleep_seconds=0,
    )
    assert result.outcome == "passed"
    assert result.attempts == 2
    # 1 boot + 4 probes
    assert docker.run_cmd.await_count == 5


@pytest.mark.asyncio
async def test_expect_body_regex_must_match():
    """ready_check supports expect_body_regex; mismatch counts as fail."""
    docker = _make_docker(
        [
            _probe_response(200, '{"status":"DOWN"}'),
            _probe_response(200, '{"status":"UP"}'),
        ]
    )

    qa_yaml = _make_qa_yaml(
        boot_timeout=10,
        ready_checks=[
            {"http": "http://gateway:8080/health", "expect_status": 200, "expect_body_regex": '"status":"UP"'},
        ],
    )
    result = await run_boot_phase(
        qa_yaml=qa_yaml,
        container_id="C",
        docker=docker,
        sleep_seconds=0,
    )
    assert result.outcome == "passed"
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_records_per_check_diagnostics_on_timeout():
    """Even on timeout, the result carries a list of which checks failed."""
    docker = _make_docker(_probe_response(502, "bad gateway"))

    qa_yaml = _make_qa_yaml(
        boot_timeout=0.3,
        ready_checks=[
            {"http": "http://gateway:8080/health", "expect_status": 200},
            {"http": "http://frontend:3000/", "expect_status": 200},
        ],
    )
    result = await run_boot_phase(
        qa_yaml=qa_yaml,
        container_id="C",
        docker=docker,
        sleep_seconds=0.05,
    )
    assert result.outcome == "timeout"
    assert len(result.failed_checks) == 2
    assert any("gateway" in c for c in result.failed_checks)
    assert any("frontend" in c for c in result.failed_checks)


@pytest.mark.asyncio
async def test_empty_ready_checks_warns_then_passes():
    """No ready_checks defined → boot passes after startup command but logs a warning."""
    import structlog.testing

    docker = _make_docker([])  # no probes expected

    qa_yaml = _make_qa_yaml(boot_timeout=10, ready_checks=[])

    with structlog.testing.capture_logs() as captured:
        result = await run_boot_phase(
            qa_yaml=qa_yaml,
            container_id="C",
            docker=docker,
            sleep_seconds=0,
        )

    assert result.outcome == "passed"
    assert result.attempts == 1
    # only the boot launch — no probes
    assert docker.run_cmd.await_count == 1
    assert any(entry.get("event") == "boot_no_ready_checks" for entry in captured), (
        f"Expected boot_no_ready_checks warning, got: {captured}"
    )


@pytest.mark.asyncio
async def test_connect_failed_treated_as_failure_not_crash():
    """When the dev server isn't bound yet, the in-sandbox probe returns
    STATUS=0 with a body containing ERR:<type>:<msg>. That's a normal
    failure (count it as failed_check, keep polling), NOT outcome=timeout
    on first attempt or outcome=startup_failed."""
    docker = _make_docker(
        [
            # Attempt 1: connect-failed (server not yet up)
            _probe_response(0, "ERR:URLError:[Errno 111] Connection refused"),
            # Attempt 2: server up
            _probe_response(200, "OK"),
        ]
    )

    result = await run_boot_phase(
        qa_yaml=_make_qa_yaml(boot_timeout=10),
        container_id="C",
        docker=docker,
        sleep_seconds=0,
    )
    assert result.outcome == "passed"
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_expect_status_list_accepts_any_listed_code():
    """expect_status as a list — any matching code passes the check
    (introduced 2026-05-06 for auth-gated apps that 403 on the dev server's
    root path until the user logs in)."""
    docker = _make_docker([_probe_response(403, "Forbidden")])

    qa_yaml = _make_qa_yaml(
        boot_timeout=10,
        ready_checks=[
            {"http": "http://app:3000/", "expect_status": [200, 401, 403]},
        ],
    )
    result = await run_boot_phase(
        qa_yaml=qa_yaml,
        container_id="C",
        docker=docker,
        sleep_seconds=0,
    )
    assert result.outcome == "passed"
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_expect_status_list_rejects_unlisted_code():
    """A 500 must NOT pass when the list is [200, 401, 403]."""
    docker = _make_docker(_probe_response(500, "boom"))

    qa_yaml = _make_qa_yaml(
        boot_timeout=0.3,
        ready_checks=[
            {"http": "http://app:3000/", "expect_status": [200, 401, 403]},
        ],
    )
    result = await run_boot_phase(
        qa_yaml=qa_yaml,
        container_id="C",
        docker=docker,
        sleep_seconds=0.05,
    )
    assert result.outcome == "timeout"
    assert "got 500" in result.failed_checks[0]
    assert "[200, 401, 403]" in result.failed_checks[0]


# -------- target: deployed — empty boot command (EMB-27) --------


@pytest.mark.asyncio
async def test_empty_command_skips_launch_and_polls_checks():
    """Deployed-target apps have no boot command: run_boot_phase must not
    exec a nohup launch, only the ready_check probes against the external
    URL. (No _make_docker here — that helper models the boot-ack first
    call, which this path must never make.)"""
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value=_probe_response(200, "OK"))

    qa_yaml = _make_qa_yaml()
    qa_yaml["startup"]["command"] = ""
    result = await run_boot_phase(
        qa_yaml=qa_yaml,
        container_id="C",
        docker=docker,
        sleep_seconds=0,
    )
    assert result.outcome == "passed"
    # ONLY the probe call — no boot-launch exec.
    assert docker.run_cmd.await_count == 1
    probe_cmd = docker.run_cmd.await_args_list[0]
    assert "nohup" not in str(probe_cmd)


@pytest.mark.asyncio
async def test_empty_command_times_out_when_external_target_down():
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value=_probe_response(0, "ERR:URLError:Connection refused"))

    qa_yaml = _make_qa_yaml(boot_timeout=0)
    qa_yaml["startup"]["command"] = ""
    result = await run_boot_phase(
        qa_yaml=qa_yaml,
        container_id="C",
        docker=docker,
        sleep_seconds=0,
    )
    assert result.outcome == "timeout"
    assert result.failed_checks
