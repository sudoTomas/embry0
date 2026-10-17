"""`embry0 onboard` without --branch analyzes the repo's base branch (#29).

The CLI used to send the literal "main", so a repo whose default branch is
anything else was analyzed on a branch that may not exist. It now leaves the
branch out and lets the orchestrator resolve it, the same as the dashboard.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

import embry0.cli.__main__ as cli


def _parse(argv: list[str]) -> argparse.Namespace:
    with patch.object(cli, "cmd_onboard") as cmd, patch("sys.argv", ["embry0", *argv]):
        cli.main()
    return cmd.call_args.args[0]


def test_branch_defaults_to_unset() -> None:
    args = _parse(["onboard", "group/proj"])
    assert args.branch is None, f"--branch still defaults to {args.branch!r}; the repo's own branch is never asked"


def test_explicit_branch_is_kept() -> None:
    assert _parse(["onboard", "group/proj", "--branch", "dev"]).branch == "dev"


def _resp(status_code: int, payload: dict) -> MagicMock:
    resp = MagicMock(status_code=status_code, text="")
    resp.json = MagicMock(return_value=payload)
    return resp


def test_reports_the_branch_the_orchestrator_chose(monkeypatch, capsys) -> None:
    monkeypatch.setenv("EMBRY0_API_KEY", "k")
    args = argparse.Namespace(repo="group/proj", branch=None, no_smoke=False, api_url=None, api_key=None)
    created = {"job_id": "job-1", "pipeline_config": {"branch": "develop"}}
    with (
        patch("httpx.post", return_value=_resp(201, created)) as post,
        patch("httpx.get", return_value=_resp(200, {"status": "completed"})),
        patch("time.sleep"),
    ):
        cli.cmd_onboard(args)
    assert post.call_args.kwargs["json"]["branch"] is None
    out = capsys.readouterr()
    assert "group/proj@develop" in out.out + out.err, out


def test_failure_still_exits_nonzero(monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_API_KEY", "k")
    args = argparse.Namespace(repo="group/proj", branch="dev", no_smoke=True, api_url=None, api_key=None)
    with (
        patch("httpx.post", return_value=_resp(201, {"job_id": "job-1"})),
        patch("httpx.get", return_value=_resp(200, {"status": "failed", "error_message": "smoke failed"})),
        patch("time.sleep"),
        pytest.raises(SystemExit) as exc,
    ):
        cli.cmd_onboard(args)
    assert exc.value.code == 1
