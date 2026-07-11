"""Tests for BodySizeMiddleware."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from embry0.api.middleware.body_size import DEFAULT_MAX_BYTES, BodySizeMiddleware


def _app(max_bytes: int = DEFAULT_MAX_BYTES) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodySizeMiddleware, max_bytes=max_bytes)

    @app.post("/api/v1/webhook")
    async def webhook():
        return {"ok": True}

    @app.post("/api/v1/telegram/callback")
    async def telegram():
        return {"ok": True}

    @app.post("/api/v1/jobs")
    async def jobs():
        return {"ok": True}

    return app


def test_oversized_body_returns_413():
    client = TestClient(_app(max_bytes=1024))
    resp = client.post("/api/v1/webhook", content=b"x" * 2048)
    assert resp.status_code == 413


def test_under_cap_passes():
    client = TestClient(_app(max_bytes=1024))
    resp = client.post("/api/v1/webhook", content=b"x" * 100)
    assert resp.status_code == 200


def test_unprotected_path_unaffected():
    """Body-size cap only applies to webhook + telegram paths."""
    client = TestClient(_app(max_bytes=10))
    # /jobs is not protected, so a 100-byte POST is fine
    resp = client.post("/api/v1/jobs", content=b"x" * 100)
    assert resp.status_code == 200


def test_telegram_path_protected():
    client = TestClient(_app(max_bytes=10))
    resp = client.post("/api/v1/telegram/callback", content=b"x" * 100)
    assert resp.status_code == 413


def test_get_unaffected():
    """Cap only applies to POST."""
    app = FastAPI()
    app.add_middleware(BodySizeMiddleware, max_bytes=10)

    @app.get("/api/v1/webhook")
    async def get_webhook():
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/api/v1/webhook")
    assert resp.status_code == 200
