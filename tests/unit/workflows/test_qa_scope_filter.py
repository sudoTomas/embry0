import pytest
from athanor.workflows.issue_to_pr.nodes import _filter_user_env_for_sandbox


def test_app_scope_always_passed():
    out = _filter_user_env_for_sandbox(
        [{"key": "DB_URL", "value": "x", "scope": "app"}],
        qa_active=False,
    )
    assert out == {"DB_URL": "x"}


def test_qa_scope_dropped_when_qa_inactive():
    out = _filter_user_env_for_sandbox(
        [
            {"key": "DB_URL", "value": "x", "scope": "app"},
            {"key": "QA_TEST_USER", "value": "y", "scope": "qa"},
        ],
        qa_active=False,
    )
    assert "QA_TEST_USER" not in out
    assert out["DB_URL"] == "x"


def test_qa_scope_included_when_qa_active():
    out = _filter_user_env_for_sandbox(
        [
            {"key": "DB_URL", "value": "x", "scope": "app"},
            {"key": "QA_TEST_USER", "value": "y", "scope": "qa"},
        ],
        qa_active=True,
    )
    assert out == {"DB_URL": "x", "QA_TEST_USER": "y"}


def test_reserved_keys_dropped_in_both_modes():
    """Defense in depth — even if someone bypasses the API and stores a
    reserved key, sandbox injection drops it."""
    out = _filter_user_env_for_sandbox(
        [
            {"key": "GITHUB_TOKEN", "value": "leaked", "scope": "app"},
            {"key": "DB_URL", "value": "ok", "scope": "app"},
        ],
        qa_active=False,
    )
    assert "GITHUB_TOKEN" not in out
    assert out["DB_URL"] == "ok"


def test_reserved_prefixes_dropped_in_both_modes():
    """Same defense in depth applies to RESERVED_ENV_PREFIXES."""
    out = _filter_user_env_for_sandbox(
        [
            {"key": "DOCKER_BUILDKIT", "value": "1", "scope": "app"},
            {"key": "QA_ARTIFACT_BUCKET", "value": "evil", "scope": "qa"},
            {"key": "OK", "value": "fine", "scope": "app"},
        ],
        qa_active=True,
    )
    assert "DOCKER_BUILDKIT" not in out
    assert "QA_ARTIFACT_BUCKET" not in out
    assert out == {"OK": "fine"}


def test_legacy_dict_input_still_works():
    """Backwards compatibility — if anything still passes a plain dict, treat
    every key as scope='app'."""
    out = _filter_user_env_for_sandbox(
        {"DB_URL": "x", "OTHER": "y"},
        qa_active=False,
    )
    assert out == {"DB_URL": "x", "OTHER": "y"}
