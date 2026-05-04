"""run_boot_phase: deterministic boot orchestration with hard timeout."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.workflows.qa.boot import run_boot_phase


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


@pytest.mark.asyncio
async def test_all_checks_pass_immediately():
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="started")
    http = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.text = "OK"
    http.get = AsyncMock(return_value=response)

    result = await run_boot_phase(
        qa_yaml=_make_qa_yaml(),
        container_id="C",
        docker=docker,
        http=http,
        sleep_seconds=0,
    )
    assert result.outcome == "passed"
    assert result.attempts == 1
    docker.run_cmd.assert_awaited_once()
    assert http.get.await_count == 1


@pytest.mark.asyncio
async def test_startup_command_fails_returns_startup_failed():
    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=RuntimeError("compose error"))
    http = MagicMock()
    http.get = AsyncMock()  # never called

    result = await run_boot_phase(
        qa_yaml=_make_qa_yaml(),
        container_id="C",
        docker=docker,
        http=http,
        sleep_seconds=0,
    )
    assert result.outcome == "startup_failed"
    assert "compose error" in result.error_message
    http.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_polls_until_pass():
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="started")
    http = MagicMock()

    responses = [
        MagicMock(status_code=503, text="boot in progress"),
        MagicMock(status_code=503, text="boot in progress"),
        MagicMock(status_code=200, text="OK"),
    ]
    http.get = AsyncMock(side_effect=responses)

    result = await run_boot_phase(
        qa_yaml=_make_qa_yaml(boot_timeout=10),
        container_id="C",
        docker=docker,
        http=http,
        sleep_seconds=0,
    )
    assert result.outcome == "passed"
    assert result.attempts == 3
    assert http.get.await_count == 3


@pytest.mark.asyncio
async def test_hits_hard_timeout():
    """Permanent 503 → outcome=timeout after boot_timeout_seconds elapsed."""
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="started")
    http = MagicMock()
    http.get = AsyncMock(return_value=MagicMock(status_code=503, text="never ready"))

    result = await run_boot_phase(
        qa_yaml=_make_qa_yaml(boot_timeout=1),
        container_id="C",
        docker=docker,
        http=http,
        sleep_seconds=0.05,
    )
    assert result.outcome == "timeout"
    assert result.attempts > 5
    assert result.duration_ms >= 1000
    assert "gateway" in result.failed_checks[0]


@pytest.mark.asyncio
async def test_multiple_ready_checks_all_must_pass():
    """If qa.yaml has 2 ready_checks, both must return 200 in the same attempt."""
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="started")
    http = MagicMock()

    response_200 = MagicMock(status_code=200, text="OK")
    response_503 = MagicMock(status_code=503, text="boot")
    http.get = AsyncMock(
        side_effect=[
            response_200,
            response_503,
            response_200,
            response_200,
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
        http=http,
        sleep_seconds=0,
    )
    assert result.outcome == "passed"
    assert result.attempts == 2
    assert http.get.await_count == 4


@pytest.mark.asyncio
async def test_expect_body_regex_must_match():
    """ready_check supports expect_body_regex; mismatch counts as fail."""
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="started")
    http = MagicMock()

    http.get = AsyncMock(
        side_effect=[
            MagicMock(status_code=200, text='{"status":"DOWN"}'),
            MagicMock(status_code=200, text='{"status":"UP"}'),
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
        http=http,
        sleep_seconds=0,
    )
    assert result.outcome == "passed"
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_records_per_check_diagnostics_on_timeout():
    """Even on timeout, the result carries a list of which checks failed."""
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="started")
    http = MagicMock()
    http.get = AsyncMock(return_value=MagicMock(status_code=502, text="bad gateway"))

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
        http=http,
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

    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="started")
    http = MagicMock()
    http.get = AsyncMock()  # never called

    qa_yaml = _make_qa_yaml(boot_timeout=10, ready_checks=[])

    with structlog.testing.capture_logs() as captured:
        result = await run_boot_phase(
            qa_yaml=qa_yaml,
            container_id="C",
            docker=docker,
            http=http,
            sleep_seconds=0,
        )

    assert result.outcome == "passed"
    assert result.attempts == 1
    http.get.assert_not_awaited()
    assert any(entry.get("event") == "boot_no_ready_checks" for entry in captured), (
        f"Expected boot_no_ready_checks warning, got: {captured}"
    )
