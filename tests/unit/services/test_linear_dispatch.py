"""Unit tests for the operator dispatch module (INT-655 W1b).

Pure-unit: httpx is monkeypatched; no network, no DB.
"""

import pytest

from athanor.services import linear_dispatch as ld


def _issue(**overrides):
    fields = {
        "identifier": "INT-700",
        "title": "Fix widget pricing rounding",
        "description": "Round to 2 decimals in the pricing node.",
        "url": "https://linear.app/ravencargo/issue/INT-700/fix-widget",
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
    assert task.startswith("[INT-700] Fix widget pricing rounding")
    assert "Round to 2 decimals in the pricing node." in task
    assert "https://linear.app/ravencargo/issue/INT-700/fix-widget" in task


def test_compose_task_omits_blank_description():
    task = ld.compose_task(_issue(description="   "))
    assert "\n\n\n" not in task
    assert task.count("\n\n") == 1  # title block + url block only


def test_additional_context_carries_the_embry0_addendum():
    ctx = ld.compose_additional_context("INT-700")
    assert ".e0/dev.yaml" in ctx
    assert "(INT-700)" in ctx  # commit-subject convention
    assert "embry0" in ctx  # label + autonomy note
    assert "main" in ctx  # PR target branch


def test_build_job_payload_shape():
    payload = ld.build_job_payload(_issue(), repo="client-project/ai-quoting", profile="dev-python")
    assert payload["repo"] == "client-project/ai-quoting"
    assert payload["sandbox_profile"] == "dev-python"
    assert payload["task"] == ld.compose_task(_issue())
    assert payload["additional_context"] == ld.compose_additional_context("INT-700")
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
                        "identifier": "INT-700",
                        "title": "Fix widget pricing rounding",
                        "description": None,
                        "url": "https://linear.app/ravencargo/issue/INT-700/fix-widget",
                    }
                }
            }
        )

    monkeypatch.setattr(ld.httpx, "post", fake_post)
    issue = ld.fetch_linear_issue("INT-700", api_key="lin_api_abc123")
    assert captured["url"] == ld.LINEAR_GRAPHQL_URL
    # Linear personal API keys are sent bare — NO "Bearer " prefix.
    assert captured["headers"]["Authorization"] == "lin_api_abc123"
    assert captured["json"]["variables"] == {"id": "INT-700"}
    assert issue.identifier == "INT-700"
    assert issue.description == ""  # None normalized to empty string


def test_fetch_linear_issue_raises_when_issue_missing(monkeypatch):
    monkeypatch.setattr(ld.httpx, "post", lambda *a, **k: _FakeResponse({"data": {"issue": None}}))
    with pytest.raises(ValueError, match="INT-999"):
        ld.fetch_linear_issue("INT-999", api_key="k")
