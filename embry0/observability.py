"""Observability configuration for the embry0 orchestrator.

Call configure_structlog() once at application startup, before any logging
occurs. In production (json=True), structlog emits newline-delimited JSON
compatible with the Docker json-file log driver and standard log aggregators.
In development (json=False), it emits human-readable coloured output.
"""

import logging

import structlog


def configure_structlog(json: bool = False) -> None:
    """Configure structlog processors for production or development output.

    Args:
        json: If True, use JSONRenderer (production). If False, use
              ConsoleRenderer (local development with readable output).
    """
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
