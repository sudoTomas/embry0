import pytest
from starlette.testclient import TestClient

try:
    from legion.api.app import create_app
except ImportError:
    pytest.skip("psycopg not available", allow_module_level=True)
from legion.config import LegionConfig


def test_websocket_connect():
    """WebSocket connects successfully."""
    config = LegionConfig(_env_file=None, dev_mode=True)
    app = create_app(config)
    from legion.api.events.bus import EventBus

    app.state.event_bus = EventBus()

    client = TestClient(app)
    with client.websocket_connect("/ws/jobs/job-123/events"):
        # Connection established - send a close to cleanly disconnect
        pass  # TestClient auto-closes
