import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

try:
    from athanor.api.app import create_app
except ImportError:
    pytest.skip("psycopg not available", allow_module_level=True)
from athanor.config import AthanorConfig

VALID_API_KEY = "test-secret-key-12345"


def _make_ws_app(*, auth_dev_mode: bool = False, api_key: str = VALID_API_KEY):
    """Create a minimal FastAPI app with the WS streaming router attached."""
    config = AthanorConfig(
        _env_file=None,
        auth_dev_mode=auth_dev_mode,
        webhook_dev_mode=True,
        api_key=api_key,
    )
    app = create_app(config)
    from athanor.api.events.bus import EventBus

    app.state.event_bus = EventBus()
    return app


def test_websocket_connect():
    """WebSocket connects successfully (auth_dev_mode bypasses subprotocol check)."""
    app = _make_ws_app(auth_dev_mode=True)
    client = TestClient(app)
    with client.websocket_connect("/ws/jobs/job-123/events"):
        pass  # TestClient auto-closes


def test_ws_rejects_missing_subprotocol():
    """No Sec-WebSocket-Protocol header → close 4001."""
    app = _make_ws_app(auth_dev_mode=False)
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/jobs/job-1/events"):
            pass
    assert exc_info.value.code == 4001


def test_ws_rejects_wrong_subprotocol():
    """Wrong bearer token → close 4001."""
    app = _make_ws_app(auth_dev_mode=False)
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/jobs/job-1/events",
            subprotocols=["athanor.bearer.wrong-key"],
        ):
            pass
    assert exc_info.value.code == 4001


def test_ws_accepts_correct_subprotocol():
    """Correct bearer subprotocol → connection accepted."""
    app = _make_ws_app(auth_dev_mode=False)
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/jobs/job-1/events",
        subprotocols=[f"athanor.bearer.{VALID_API_KEY}"],
    ):
        pass  # successfully connected and closed


def test_ws_auth_dev_mode_skips_subprotocol_check():
    """auth_dev_mode=True → connection accepted without any subprotocol."""
    app = _make_ws_app(auth_dev_mode=True)
    client = TestClient(app)
    with client.websocket_connect("/ws/jobs/job-1/events"):
        pass


def test_ws_no_api_key_configured_skips_auth():
    """Empty api_key on server → auth skipped, connection accepted without subprotocol."""
    app = _make_ws_app(auth_dev_mode=False, api_key="")
    client = TestClient(app)
    with client.websocket_connect("/ws/jobs/job-1/events"):
        pass


def test_passes_filter_no_filter_accepts_everything():
    """Empty wanted set means "no filter" — every event passes."""
    from athanor.api.ws.streaming import _passes_filter

    assert _passes_filter({"type": "progress"}, set()) is True
    assert _passes_filter({"type": "cost_update"}, set()) is True
    assert _passes_filter({}, set()) is True  # missing type still passes


def test_passes_filter_allows_wanted_types():
    from athanor.api.ws.streaming import _passes_filter

    wanted = {"progress", "cost_update"}
    assert _passes_filter({"type": "progress"}, wanted) is True
    assert _passes_filter({"type": "cost_update"}, wanted) is True


def test_passes_filter_rejects_unwanted_types():
    from athanor.api.ws.streaming import _passes_filter

    wanted = {"progress"}
    assert _passes_filter({"type": "thinking"}, wanted) is False
    # Missing type is dropped when a filter is active.
    assert _passes_filter({}, wanted) is False
    assert _passes_filter({"type": None}, wanted) is False
