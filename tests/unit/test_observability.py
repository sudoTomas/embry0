"""Unit tests for the observability module."""

import io
import json

import structlog

from athanor.observability import configure_structlog


def test_configure_structlog_json_produces_json_output() -> None:
    """JSON mode: a log statement after configuration produces valid JSON."""
    configure_structlog(json=True)
    output = io.StringIO()
    # Capture structlog output by using a custom factory
    structlog.configure(
        logger_factory=structlog.PrintLoggerFactory(file=output),
    )
    logger = structlog.get_logger("test")
    logger.info("test_event", key="value")

    line = output.getvalue().strip()
    assert line, "Expected log output but got empty string"
    # Should be valid JSON
    parsed = json.loads(line)
    assert parsed.get("key") == "value"
    assert "test_event" in str(parsed)


def test_configure_structlog_dev_mode_does_not_raise() -> None:
    """Dev mode (ConsoleRenderer) configures without error."""
    configure_structlog(json=False)
    # Just confirm no exception is raised; ConsoleRenderer output format is
    # human-readable and not tested for exact shape.
    logger = structlog.get_logger("test_dev")
    logger.info("dev_event", key="value")  # should not raise


def test_configure_structlog_default_is_dev_mode() -> None:
    """Default (json=False) is dev mode — safe for local runs without docker."""
    configure_structlog()  # should not raise
