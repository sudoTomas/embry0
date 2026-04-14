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


def test_passes_filter_no_filter_accepts_everything():
    """Empty wanted set means "no filter" — every event passes."""
    from legion.api.ws.streaming import _passes_filter

    assert _passes_filter({"type": "progress"}, set()) is True
    assert _passes_filter({"type": "cost_update"}, set()) is True
    assert _passes_filter({}, set()) is True  # missing type still passes


def test_passes_filter_allows_wanted_types():
    from legion.api.ws.streaming import _passes_filter

    wanted = {"progress", "cost_update"}
    assert _passes_filter({"type": "progress"}, wanted) is True
    assert _passes_filter({"type": "cost_update"}, wanted) is True


def test_passes_filter_rejects_unwanted_types():
    from legion.api.ws.streaming import _passes_filter

    wanted = {"progress"}
    assert _passes_filter({"type": "thinking"}, wanted) is False
    # Missing type is dropped when a filter is active.
    assert _passes_filter({}, wanted) is False
    assert _passes_filter({"type": None}, wanted) is False
