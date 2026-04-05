"""Stdout event emission from inside the sandbox container."""

import json
import sys
from datetime import UTC, datetime
from typing import Any


def emit_event(event_type: str, **kwargs: Any) -> None:
    """Write a JSON event line to stdout."""
    event: dict[str, Any] = {
        "type": event_type,
        "timestamp": datetime.now(UTC).isoformat(),
        **kwargs,
    }
    sys.stdout.write(json.dumps(event, default=str) + "\n")
    sys.stdout.flush()
