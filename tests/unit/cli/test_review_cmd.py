"""`embry0 review <repo> <number>` is a thin client of POST /api/v1/jobs (#30)."""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from embry0.cli.__main__ import cmd_review


def _resp(status_code: int, payload: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=payload or {})
    resp.text = text
    return resp


def test_it_posts_a_pr_review_job(monkeypatch, capsys) -> None:
    monkeypatch.setenv("EMBRY0_API_KEY", "sekrit")
    monkeypatch.delenv("EMBRY0_API_URL", raising=False)
    with patch("httpx.post", return_value=_resp(201, {"job_id": "job-9"})) as post:
        cmd_review(Namespace(repo="acme/widgets", number=7, api_url=None, api_key=None))
    args, kwargs = post.call_args
    assert args[0] == "http://localhost:8200/api/v1/jobs"
    assert kwargs["json"] == {"pipeline": "pr-review", "repo": "acme/widgets", "number": 7}
    assert kwargs["headers"]["Authorization"] == "Bearer sekrit"
    captured = capsys.readouterr()
    assert "job-9" in captured.out + captured.err


def test_a_refusal_exits_2_with_the_reason(monkeypatch, capsys) -> None:
    monkeypatch.setenv("EMBRY0_API_KEY", "k")
    with (
        patch("httpx.post", return_value=_resp(409, text='{"detail":"review not started: duplicate"}')),
        pytest.raises(SystemExit) as exc,
    ):
        cmd_review(Namespace(repo="acme/widgets", number=7, api_url=None, api_key=None))
    assert exc.value.code == 2
    assert "duplicate" in capsys.readouterr().err
