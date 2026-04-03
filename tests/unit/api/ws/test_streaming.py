from legion.api.app import create_app
from legion.config import LegionConfig
from starlette.testclient import TestClient


def test_websocket_connect():
    """WebSocket connects successfully."""
    config = LegionConfig(_env_file=None, dev_mode=True)
    app = create_app(config)
    app.state.event_subscribers = {}

    client = TestClient(app)
    with client.websocket_connect("/ws/jobs/job-123/events") as ws:
        # Connection established - send a close to cleanly disconnect
        pass  # TestClient auto-closes
