"""`embry0 build-qa-image` API-client wiring (EMB-42).

The CLI is a thin, long-timeout client of POST /api/v1/qa/images/build —
the build's dependencies only exist inside the running compose stack.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from embry0.cli.__main__ import _resolve_api_key, run_build_qa_image_via_api


def _resp(status_code: int, payload: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=payload or {})
    resp.text = text
    return resp


def test_posts_payload_with_bearer(monkeypatch):
    monkeypatch.setenv("EMBRY0_API_KEY", "sekrit")
    monkeypatch.delenv("EMBRY0_API_URL", raising=False)
    with patch("httpx.post", return_value=_resp(200, {"status": "built"})) as post:
        outcome = run_build_qa_image_via_api(repo="org/repo", branch="dev", force=True)
    assert outcome == "built"
    args, kwargs = post.call_args
    assert args[0] == "http://localhost:8200/api/v1/qa/images/build"
    assert kwargs["json"] == {"repo": "org/repo", "branch": "dev", "force": True}
    assert kwargs["headers"]["Authorization"] == "Bearer sekrit"
    assert kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"


def test_api_url_override(monkeypatch):
    monkeypatch.delenv("EMBRY0_API_KEY", raising=False)
    with patch("httpx.post", return_value=_resp(200, {"status": "skipped"})) as post:
        outcome = run_build_qa_image_via_api(repo="o/r", branch="main", force=False, api_url="http://box:9999/")
    assert outcome == "skipped"
    assert post.call_args[0][0] == "http://box:9999/api/v1/qa/images/build"


def test_401_exits_with_guidance(monkeypatch, capsys):
    monkeypatch.setenv("EMBRY0_API_KEY", "wrong")
    with patch("httpx.post", return_value=_resp(401)), pytest.raises(SystemExit) as exc:
        run_build_qa_image_via_api(repo="o/r", branch="main", force=False)
    assert exc.value.code == 2
    assert "EMBRY0_API_KEY" in capsys.readouterr().err


def test_connect_error_exits_with_stack_hint(monkeypatch, capsys):
    monkeypatch.setenv("EMBRY0_API_KEY", "k")
    with (
        patch("httpx.post", side_effect=httpx.ConnectError("boom")),
        pytest.raises(SystemExit) as exc,
    ):
        run_build_qa_image_via_api(repo="o/r", branch="main", force=False)
    assert exc.value.code == 2
    assert "is the stack running" in capsys.readouterr().err


def test_resolve_api_key_precedence(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EMBRY0_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    assert _resolve_api_key("explicit") == "explicit"
    assert _resolve_api_key(None) is None
    (tmp_path / ".env").write_text("OTHER=1\nAPI_KEY=from-dotenv\n")
    assert _resolve_api_key(None) == "from-dotenv"
    monkeypatch.setenv("API_KEY", "from-env")
    assert _resolve_api_key(None) == "from-env"
    monkeypatch.setenv("EMBRY0_API_KEY", "from-embry0-env")
    assert _resolve_api_key(None) == "from-embry0-env"
