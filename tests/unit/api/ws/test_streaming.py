import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

try:
    from embry0.api.app import create_app
except ImportError:
    pytest.skip("psycopg not available", allow_module_level=True)
from embry0.config import Embry0Config

VALID_API_KEY = "test-secret-key-12345"


def _make_ws_app(*, auth_dev_mode: bool = False, api_key: str = VALID_API_KEY):
    """Create a minimal FastAPI app with the WS streaming router attached."""
    config = Embry0Config(
        _env_file=None,
        auth_dev_mode=auth_dev_mode,
        webhook_dev_mode=True,
        api_key=api_key,
    )
    app = create_app(config)
    from embry0.api.events.bus import EventBus

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
    """Garbage token → close 4001."""
    app = _make_ws_app(auth_dev_mode=False)
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/jobs/job-1/events",
            subprotocols=["embry0.token.not-a-real-token"],
        ):
            pass
    assert exc_info.value.code == 4001


def test_ws_rejects_raw_api_key_subprotocol():
    """RAV-1070: the old embry0.bearer.<api_key> subprotocol is gone — raw keys
    on the handshake no longer authenticate."""
    app = _make_ws_app(auth_dev_mode=False)
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/jobs/job-1/events",
            subprotocols=[f"embry0.bearer.{VALID_API_KEY}"],
        ):
            pass
    assert exc_info.value.code == 4001


def _mint_ws_token(app, job_id: str) -> str:
    from embry0.api.scoped_tokens import mint_scoped_token, signing_material

    token, _ = mint_scoped_token(signing_material(app.state.config), f"ws:{job_id}")
    return token


def test_ws_accepts_scoped_token_subprotocol():
    """Job-scoped token subprotocol → connection accepted."""
    app = _make_ws_app(auth_dev_mode=False)
    client = TestClient(app)
    token = _mint_ws_token(app, "job-1")
    with client.websocket_connect(
        "/ws/jobs/job-1/events",
        subprotocols=[f"embry0.token.{token}"],
    ):
        pass  # successfully connected and closed


def test_ws_scoped_token_is_job_bound():
    """A token minted for one job must not open another job's stream."""
    app = _make_ws_app(auth_dev_mode=False)
    client = TestClient(app)
    token = _mint_ws_token(app, "job-1")
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/jobs/job-OTHER/events",
            subprotocols=[f"embry0.token.{token}"],
        ):
            pass
    assert exc_info.value.code == 4001


def test_ws_auth_dev_mode_skips_subprotocol_check():
    """auth_dev_mode=True → connection accepted without any subprotocol."""
    app = _make_ws_app(auth_dev_mode=True)
    client = TestClient(app)
    with client.websocket_connect("/ws/jobs/job-1/events"):
        pass


def test_ws_no_api_key_configured_still_requires_auth():
    """#518: an empty api_key is not a dev-mode switch. Outside AUTH_DEV_MODE
    the handshake still needs a token (close 4001), even with WEBHOOK_DEV_MODE on."""
    app = _make_ws_app(auth_dev_mode=False, api_key="")
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/jobs/job-1/events"):
            pass
    assert exc_info.value.code == 4001


def test_passes_filter_no_filter_accepts_everything():
    """Empty wanted set means "no filter" — every event passes."""
    from embry0.api.ws.streaming import _passes_filter

    assert _passes_filter({"type": "progress"}, set()) is True
    assert _passes_filter({"type": "cost_update"}, set()) is True
    assert _passes_filter({}, set()) is True  # missing type still passes


def test_passes_filter_allows_wanted_types():
    from embry0.api.ws.streaming import _passes_filter

    wanted = {"progress", "cost_update"}
    assert _passes_filter({"type": "progress"}, wanted) is True
    assert _passes_filter({"type": "cost_update"}, wanted) is True


def test_passes_filter_rejects_unwanted_types():
    from embry0.api.ws.streaming import _passes_filter

    wanted = {"progress"}
    assert _passes_filter({"type": "thinking"}, wanted) is False
    # Missing type is dropped when a filter is active.
    assert _passes_filter({}, wanted) is False
    assert _passes_filter({"type": None}, wanted) is False


class _PagedLogRepo:
    """Fake jobs repo honouring since_seq/limit like JobsRepository.get_log_events.

    On its first call it also queues a live marker event, so a replay that
    stops early still ends with something the test can read up to.
    """

    def __init__(self, app, job_id: str, total: int) -> None:
        self._app = app
        self._job_id = job_id
        self._rows = [{"id": i, "payload": {"type": "progress", "n": i}} for i in range(1, total + 1)]
        self._marked = False

    async def get_log_events(self, job_id, stream="pipeline", limit=500, since_seq=0):
        if not self._marked:
            self._marked = True
            for q in self._app.state.event_bus._subscribers[self._job_id]:
                q.put_nowait({"type": "marker", "event_seq": 10**9})
        return [r for r in self._rows if r["id"] > since_seq][:limit]


def test_ws_replay_sends_every_persisted_event_past_500():
    """#203: replay read one 500-row page, so a long job's newer events never replayed."""
    app = _make_ws_app(auth_dev_mode=True)
    app.state.jobs_repo = _PagedLogRepo(app, "job-long", 1203)
    client = TestClient(app)
    replayed = []
    with client.websocket_connect("/ws/jobs/job-long/events") as ws:
        while True:
            msg = ws.receive_json()
            if msg["type"] == "marker":
                break
            replayed.append(msg["n"])
    assert replayed == list(range(1, 1204)), f"replayed {len(replayed)} of 1203 events"


def test_ws_client_leaving_a_quiet_job_drops_its_subscription():
    """#505: with no event published after the client leaves, the handler sat on
    queue.get() forever and its queue stayed registered on the bus."""
    import time

    app = _make_ws_app(auth_dev_mode=True)
    client = TestClient(app)
    subscribers = app.state.event_bus._subscribers
    with client.websocket_connect("/ws/jobs/job-quiet/events") as ws:
        assert len(subscribers.get("job-quiet", [])) == 1
        # The client leaves; the session (and its event loop) stays open, so
        # only the handler itself can drop the subscription.
        ws.send({"type": "websocket.disconnect", "code": 1000})
        deadline = time.monotonic() + 3
        while subscribers.get("job-quiet") and time.monotonic() < deadline:
            time.sleep(0.05)
        left = len(subscribers.get("job-quiet", []))
        assert left == 0, f"{left} subscriber(s) still registered 3s after the client left"


class _FinishedJobRepo:
    """A job already finished: its log holds the terminal event, its row is terminal."""

    def __init__(self, rows: list[dict], status: str = "completed") -> None:
        self._rows = rows
        self._status = status

    async def get_log_events(self, job_id, stream="pipeline", limit=500, since_seq=0):
        return [r for r in self._rows if r["id"] > since_seq][:limit]

    async def get(self, job_id):
        return {"job_id": job_id, "status": self._status}


class _LiveRepo:
    """A running job whose first replay call injects live events into the bus."""

    def __init__(self, app, job_id: str, live: list[dict], rows: list[dict] | None = None) -> None:
        self._app = app
        self._job_id = job_id
        self._live = live
        self._rows = rows or []
        self._marked = False

    async def get_log_events(self, job_id, stream="pipeline", limit=500, since_seq=0):
        if not self._marked:
            self._marked = True
            for q in self._app.state.event_bus._subscribers[self._job_id]:
                for event in self._live:
                    q.put_nowait(event)
        return [r for r in self._rows if r["id"] > since_seq][:limit]

    async def get(self, job_id):
        return {"job_id": job_id, "status": "running"}


def test_ws_closes_with_1000_after_forwarding_job_finished():
    """spec 3.5: the route closes the socket after the terminal event."""
    app = _make_ws_app(auth_dev_mode=True)
    finished = {
        "type": "job_finished",
        "status": "completed",
        "event_seq": 5,
        "error_code": None,
        "error_message": None,
        "pr_url": None,
        "total_cost_usd": 0.0,
    }
    app.state.jobs_repo = _LiveRepo(app, "job-live", live=[{"type": "progress", "event_seq": 4}, finished])
    client = TestClient(app)
    with client.websocket_connect("/ws/jobs/job-live/events") as ws:
        assert ws.receive_json()["type"] == "progress"
        assert ws.receive_json()["type"] == "job_finished"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1000, f"the server must close with 1000 after job_finished, got {exc.value.code}"


def test_ws_closes_after_job_finished_even_when_the_filter_excludes_it():
    """A client filtering on progress still has its socket closed when the job ends."""
    app = _make_ws_app(auth_dev_mode=True)
    finished = {
        "type": "job_finished",
        "status": "failed",
        "event_seq": 2,
        "error_code": "ERR_X",
        "error_message": "x",
        "pr_url": None,
        "total_cost_usd": 0.0,
    }
    app.state.jobs_repo = _LiveRepo(app, "job-filt", live=[{"type": "progress", "event_seq": 1}, finished])
    client = TestClient(app)
    with client.websocket_connect("/ws/jobs/job-filt/events?event_types=progress") as ws:
        assert ws.receive_json()["type"] == "progress"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1000


def test_ws_replays_then_closes_for_a_job_that_already_finished():
    """A late joiner of a finished job gets the replay and then the close, no live wait."""
    app = _make_ws_app(auth_dev_mode=True)
    rows = [
        {"id": 1, "payload": {"type": "progress", "n": 1}},
        {
            "id": 2,
            "payload": {
                "type": "job_finished",
                "status": "completed",
                "error_code": None,
                "error_message": None,
                "pr_url": None,
                "total_cost_usd": 0.1,
            },
        },
    ]
    app.state.jobs_repo = _FinishedJobRepo(rows)
    client = TestClient(app)
    with client.websocket_connect("/ws/jobs/job-done/events") as ws:
        assert ws.receive_json()["n"] == 1
        assert ws.receive_json()["type"] == "job_finished"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1000


def test_ws_closes_after_replay_when_the_row_is_terminal_but_no_job_finished_was_persisted():
    """Review focus 1: a finished job whose job_finished persist failed, or one that
    finished before the terminal event existed, still closes after replay."""
    app = _make_ws_app(auth_dev_mode=True)
    app.state.jobs_repo = _FinishedJobRepo([{"id": 1, "payload": {"type": "progress", "n": 1}}], status="failed")
    client = TestClient(app)
    with client.websocket_connect("/ws/jobs/job-old/events") as ws:
        assert ws.receive_json()["n"] == 1
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1000


def test_ws_since_seq_replays_only_newer_rows_and_drops_live_duplicates():
    """spec 5: since_seq replay and live dedupe (nothing pinned either before this plan)."""
    app = _make_ws_app(auth_dev_mode=True)
    rows = [{"id": i, "payload": {"type": "progress", "n": i}} for i in range(1, 6)]
    live = [
        {"type": "progress", "n": 5, "event_seq": 5},
        {"type": "progress", "n": 6, "event_seq": 6},
        {"type": "marker", "event_seq": 10**9},
    ]
    app.state.jobs_repo = _LiveRepo(app, "job-seq", live=live, rows=rows)
    client = TestClient(app)
    seen = []
    with client.websocket_connect("/ws/jobs/job-seq/events?since_seq=3") as ws:
        while True:
            msg = ws.receive_json()
            if msg["type"] == "marker":
                break
            seen.append(msg["n"])
    assert seen == [4, 5, 6], f"replay must start after since_seq and the live copy of 5 must be dropped: {seen}"
