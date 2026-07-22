"""Tests for the external per-repo QA config store (EMB-48 Phase 1)."""

from __future__ import annotations

import pytest

from embry0.workflows.qa.qa_config_store import (
    QA_CONFIG_DIR_ENV,
    external_qa_config_path,
    load_external_qa_yaml,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(QA_CONFIG_DIR_ENV, str(tmp_path))
    return tmp_path


def test_disabled_when_env_unset(monkeypatch):
    monkeypatch.delenv(QA_CONFIG_DIR_ENV, raising=False)
    assert external_qa_config_path("acme/widgets") is None
    assert load_external_qa_yaml("acme/widgets") is None


def test_miss_returns_none(store):
    assert load_external_qa_yaml("acme/widgets") is None


def test_hit_returns_text(store):
    d = store / "acme__widgets"
    d.mkdir()
    (d / "qa.yaml").write_text("version: 2\n", encoding="utf-8")
    assert load_external_qa_yaml("acme/widgets") == "version: 2\n"


def test_path_uses_double_underscore_layout(store):
    path = external_qa_config_path("acme/widgets")
    assert path == store / "acme__widgets" / "qa.yaml"


@pytest.mark.parametrize(
    "repo",
    [
        "../etc/passwd",
        "acme/../../etc",
        "acme/widgets/extra",
        "acme",
        "",
        "acme/wid gets",
    ],
)
def test_unsafe_repo_shapes_are_refused(store, repo):
    assert external_qa_config_path(repo) is None
    assert load_external_qa_yaml(repo) is None


def test_unreadable_existing_file_propagates(store):
    """A present-but-broken config must fail loudly, not fall back silently."""
    d = store / "acme__widgets"
    d.mkdir()
    (d / "qa.yaml").mkdir()  # a directory named qa.yaml is not a file → miss
    assert load_external_qa_yaml("acme/widgets") is None
    # An actual unreadable file raises (is_file passes, read_text fails).
    bad = store / "acme__broken"
    bad.mkdir()
    f = bad / "qa.yaml"
    f.write_bytes(b"\xff\xfe invalid utf8 \xff")
    with pytest.raises(UnicodeDecodeError):
        load_external_qa_yaml("acme/broken")
