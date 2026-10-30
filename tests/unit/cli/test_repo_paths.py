"""The paths the CLI prints name a repository's QA config after the ``/-/`` separator (#257).

Without it, ``/api/v1/repos/acme/products/qa-config/qa-config`` reads well
but the old path ``/api/v1/repos/acme/products/qa-config`` is the
repository's own address too, and the orchestrator refuses a path that names
a registered repository in two ways.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import embry0.cli.__main__ as cli

REPO = "acme/products/qa-config"
BASE = "http://embry0.test"
CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "qa-yaml-corpus"


def _resp(status_code: int, payload: dict) -> MagicMock:
    resp = MagicMock(status_code=status_code, text="")
    resp.json = MagicMock(return_value=payload)
    return resp


def test_onboard_points_at_the_stored_config_after_the_separator(monkeypatch, capsys) -> None:
    monkeypatch.setenv("EMBRY0_API_KEY", "k")
    args = argparse.Namespace(repo=REPO, branch="dev", no_smoke=True, api_url=BASE, api_key=None)
    with (
        patch("httpx.post", return_value=_resp(201, {"job_id": "job-1"})),
        patch("httpx.get", return_value=_resp(200, {"status": "completed"})),
        patch("time.sleep"),
    ):
        cli.cmd_onboard(args)
    out = capsys.readouterr().out
    assert f"GET {BASE}/api/v1/repos/{REPO}/-/qa-config" in out, f"onboard printed no path with /-/: {out!r}"


def test_init_points_at_the_stored_config_after_the_separator(monkeypatch, capsys) -> None:
    monkeypatch.setenv("EMBRY0_API_KEY", "k")
    args = argparse.Namespace(
        repo=REPO,
        force=False,
        app=None,
        boot_command=None,
        frontend_url=None,
        ready_check_url=None,
        sandbox_profile=None,
        api_url=BASE,
        api_key=None,
    )
    result = {"repo": REPO, "steps": [], "qa_required": "never"}
    with patch("httpx.post", return_value=_resp(200, result)):
        cli.cmd_init(args)
    out = capsys.readouterr().out
    assert f"PUT /api/v1/repos/{REPO}/-/qa-config" in out, f"init printed no path with /-/: {out!r}"


def test_migrate_qa_config_names_the_path_after_the_separator(tmp_path: Path, capsys) -> None:
    qa = tmp_path / "demo-repo" / ".embry0" / "qa.yaml"
    qa.parent.mkdir(parents=True)
    shutil.copy(CORPUS / "v1" / "single-app.yaml", qa)
    cli.cmd_migrate_qa_config(argparse.Namespace(qa_path=qa, app_name=None, write=True, dry_run=False))
    out = capsys.readouterr().out
    assert "PUT /api/v1/repos/<owner>/<repo>/-/qa-config" in out, f"migrate-qa-config printed no /-/: {out!r}"
