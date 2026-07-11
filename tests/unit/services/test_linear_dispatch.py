"""Unit tests for the operator dispatch module.

Pure-unit: httpx is monkeypatched; no network, no DB.
"""

import pytest

from embry0.services import linear_dispatch as ld


def _issue(**overrides):
    fields = {
        "identifier": "ENG-700",
        "title": "Fix widget pricing rounding",
        "description": "Round to 2 decimals in the pricing node.",
        "url": "https://linear.app/example/issue/ENG-700/fix-widget",
    }
    fields.update(overrides)
    return ld.LinearIssue(**fields)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_compose_task_contains_identifier_title_description_and_url():
    task = ld.compose_task(_issue())
    assert task.startswith("[ENG-700] Fix widget pricing rounding")
    assert "Round to 2 decimals in the pricing node." in task
    assert "https://linear.app/example/issue/ENG-700/fix-widget" in task


def test_compose_task_omits_blank_description():
    task = ld.compose_task(_issue(description="   "))
    assert "\n\n\n" not in task
    assert task.count("\n\n") == 1  # title block + url block only


def test_additional_context_carries_the_embry0_addendum():
    ctx = ld.compose_additional_context("ENG-700")
    assert ".e0/dev.yaml" in ctx
    assert "(ENG-700)" in ctx  # commit-subject convention
    assert "embry0" in ctx  # label + autonomy note
    assert "main" in ctx  # PR target branch


def test_build_job_payload_shape():
    payload = ld.build_job_payload(_issue(), repo="acme/widgets", profile="dev-python")
    assert payload["repo"] == "acme/widgets"
    assert payload["sandbox_profile"] == "dev-python"
    assert payload["task"] == ld.compose_task(_issue())
    assert payload["additional_context"] == ld.compose_additional_context("ENG-700")
    assert set(payload) == {"repo", "task", "sandbox_profile", "additional_context"}


def test_fetch_linear_issue_sends_bare_api_key_header(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse(
            {
                "data": {
                    "issue": {
                        "identifier": "ENG-700",
                        "title": "Fix widget pricing rounding",
                        "description": None,
                        "url": "https://linear.app/example/issue/ENG-700/fix-widget",
                    }
                }
            }
        )

    monkeypatch.setattr(ld.httpx, "post", fake_post)
    issue = ld.fetch_linear_issue("ENG-700", api_key="lin_api_abc123")
    assert captured["url"] == ld.LINEAR_GRAPHQL_URL
    # Linear personal API keys are sent bare — NO "Bearer " prefix.
    assert captured["headers"]["Authorization"] == "lin_api_abc123"
    assert captured["json"]["variables"] == {"id": "ENG-700"}
    assert issue.identifier == "ENG-700"
    assert issue.description == ""  # None normalized to empty string


def test_fetch_linear_issue_raises_when_issue_missing(monkeypatch):
    monkeypatch.setattr(ld.httpx, "post", lambda *a, **k: _FakeResponse({"data": {"issue": None}}))
    with pytest.raises(ValueError, match="ENG-999"):
        ld.fetch_linear_issue("ENG-999", api_key="k")


def test_dispatch_job_posts_bearer_to_jobs_endpoint(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse({"job_id": "job-123", "status": "pending"})

    monkeypatch.setattr(ld.httpx, "post", fake_post)
    result = ld.dispatch_job({"repo": "o/r"}, base_url="http://localhost:8200/", api_key="sekret")
    assert captured["url"] == "http://localhost:8200/api/v1/jobs"
    assert captured["headers"]["Authorization"] == "Bearer sekret"
    assert captured["json"] == {"repo": "o/r"}
    assert result["job_id"] == "job-123"


def test_main_dry_run_prints_payload_without_posting(monkeypatch, capsys):
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_x")
    monkeypatch.setattr(ld, "fetch_linear_issue", lambda issue_id, api_key: _issue())

    def boom(*a, **k):
        raise AssertionError("dispatch_job must not be called in --dry-run")

    monkeypatch.setattr(ld, "dispatch_job", boom)
    rc = ld.main(["ENG-700", "--repo", "acme/widgets", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"repo": "acme/widgets"' in out
    assert '"sandbox_profile": "dev-python"' in out


def test_main_repo_defaults_from_env(monkeypatch, capsys):
    """EMBRY0_DEFAULT_REPO supplies the target repo when --repo is omitted."""
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_x")
    monkeypatch.setenv("EMBRY0_DEFAULT_REPO", "acme/widgets")
    monkeypatch.setattr(ld, "fetch_linear_issue", lambda issue_id, api_key: _issue())
    rc = ld.main(["ENG-700", "--dry-run"])
    assert rc == 0
    assert '"repo": "acme/widgets"' in capsys.readouterr().out


def test_main_fails_fast_without_repo(monkeypatch, capsys):
    """No --repo and no EMBRY0_DEFAULT_REPO → clear error, exit 2."""
    monkeypatch.delenv("EMBRY0_DEFAULT_REPO", raising=False)
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_x")
    rc = ld.main(["ENG-700", "--dry-run"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--repo" in err
    assert "EMBRY0_DEFAULT_REPO" in err


def test_main_dispatches_and_prints_console_url(monkeypatch, capsys):
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_x")
    monkeypatch.setenv("EMBRY0_API_KEY", "sekret")
    monkeypatch.setenv("EMBRY0_URL", "http://embry0.test:8200")
    monkeypatch.setattr(ld, "fetch_linear_issue", lambda issue_id, api_key: _issue())
    monkeypatch.setattr(ld, "dispatch_job", lambda payload, base_url, api_key: {"job_id": "job-9"})
    rc = ld.main(["ENG-700", "--repo", "acme/widgets", "--profile", "dev-python"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "job-9" in out
    assert "http://embry0.test:8200/jobs/job-9" in out


def test_main_prints_repo_filtered_board_url(monkeypatch, capsys):
    """After the per-job console URL, main prints a Console board deep link
    with the repo URL-encoded (the slash must not survive raw)."""
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_x")
    monkeypatch.setenv("EMBRY0_API_KEY", "sekret")
    monkeypatch.setenv("EMBRY0_URL", "http://embry0.test:8200")
    monkeypatch.setattr(ld, "fetch_linear_issue", lambda issue_id, api_key: _issue())
    monkeypatch.setattr(ld, "dispatch_job", lambda payload, base_url, api_key: {"job_id": "job-9"})
    rc = ld.main(["ENG-700", "--repo", "acme/widgets"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "board: http://embry0.test:8200/console?repo=acme%2Fwidgets" in out
    # Board line comes after the per-job console line.
    assert out.index("console:") < out.index("board:")


def test_main_fails_fast_without_linear_key(monkeypatch, capsys):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    rc = ld.main(["ENG-700", "--repo", "acme/widgets", "--dry-run"])
    assert rc == 2
    assert "LINEAR_API_KEY" in capsys.readouterr().err
