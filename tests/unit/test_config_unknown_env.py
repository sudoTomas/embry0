"""Spec 1A §3.8: unknown embry0-prefixed env keys, and a sane GITLAB_URL."""

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from embry0.config import Embry0Config, unknown_prefixed_env_keys


def test_reports_an_unknown_key_under_an_embry0_prefix():
    keys = unknown_prefixed_env_keys({"GITLAB_POLL_INTERVAL": "60", "GITLAB_URL": "https://gitlab.com"})
    assert keys == ["GITLAB_POLL_INTERVAL"]


def test_declared_fields_are_never_reported():
    env = {
        "GITHUB_TOKEN": "x",
        "GITLAB_URL": "https://gitlab.com",
        "LINEAR_API_KEY": "x",
        "DEFAULT_FORGE": "github",
    }
    assert unknown_prefixed_env_keys(env) == []


def test_keys_outside_the_four_prefixes_are_ignored():
    """Third-party env noise is real — extra='ignore' exists for a reason."""
    assert unknown_prefixed_env_keys({"HOME": "/root", "PATH": "/usr/bin", "DOCKER_HOST": "x"}) == []


def test_per_owner_forge_tokens_are_a_family_not_a_typo():
    env = {"GITHUB_TOKEN__ACME": "x", "GITLAB_TOKEN__ACME": "y"}
    assert unknown_prefixed_env_keys(env) == []


def test_orchestrator_owned_reserved_keys_are_not_a_typo():
    """EMBRY0_GITLAB_PROXY_URL is injected by the orchestrator, never declared."""
    assert unknown_prefixed_env_keys({"EMBRY0_GITLAB_PROXY_URL": "http://gitlab-proxy:8080"}) == []


def test_embry0_prefixed_sandbox_vars_are_reported_when_undeclared():
    assert unknown_prefixed_env_keys({"EMBRY0_TYPOED_KNOB": "1"}) == ["EMBRY0_TYPOED_KNOB"]


def test_result_is_sorted_and_deduplicated():
    env = {"GITLAB_ZZZ": "1", "GITHUB_AAA": "1", "LINEAR_MMM": "1"}
    assert unknown_prefixed_env_keys(env) == ["GITHUB_AAA", "GITLAB_ZZZ", "LINEAR_MMM"]


# --- GITLAB_URL ------------------------------------------------------------


def test_default_gitlab_url_is_accepted():
    with patch.dict(os.environ, {}, clear=True):
        assert Embry0Config(_env_file=None).gitlab_url == "https://gitlab.com"  # type: ignore[call-arg]


def test_self_managed_https_url_is_accepted():
    with patch.dict(os.environ, {"GITLAB_URL": "https://gitlab.example.com"}, clear=True):
        assert Embry0Config(_env_file=None).gitlab_url == "https://gitlab.example.com"  # type: ignore[call-arg]


def test_self_managed_url_with_a_port_and_path_is_accepted():
    """A reverse-proxied instance can legitimately live on a port, under a path."""
    with patch.dict(os.environ, {"GITLAB_URL": "https://git.internal.example:8443/gitlab"}, clear=True):
        cfg = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert cfg.gitlab_url == "https://git.internal.example:8443/gitlab"


def test_trailing_slash_is_normalised_away():
    with patch.dict(os.environ, {"GITLAB_URL": "https://gitlab.example.com/"}, clear=True):
        assert Embry0Config(_env_file=None).gitlab_url == "https://gitlab.example.com"  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "bad",
    [
        "http://gitlab.example.com",  # plaintext — the PAT would ride it
        "gitlab.example.com",  # no scheme; urljoin would mangle every path
        "https://",  # no hostname
        "not a url at all",
    ],
)
def test_bad_gitlab_url_refuses_startup(bad):
    with patch.dict(os.environ, {"GITLAB_URL": bad}, clear=True):
        with pytest.raises(ValidationError, match="GITLAB_URL"):
            Embry0Config(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("value", "wrong"),
    [
        ("http://oauth2:tok-000000000007@gitlab.example.com", "it is plaintext http"),
        ("http://tok-000000000007@gitlab.example.com", "it is plaintext http"),
        ("oauth2:tok-000000000007@gitlab.example.com", "it does not start with https://"),
        ("https://", "it has no hostname"),
    ],
    ids=["password", "bare-user", "no-scheme", "no-host"],
)
def test_the_refusal_says_what_is_wrong_without_repeating_the_value(value, wrong):
    """A URL can carry a token in its userinfo, so the validator's own message names the
    problem and never the value. (pydantic's own echo of it is off: ``hide_input_in_errors``.)"""
    with patch.dict(os.environ, {"GITLAB_URL": value}, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Embry0Config(_env_file=None)  # type: ignore[call-arg]
    (error,) = excinfo.value.errors()
    assert wrong in error["msg"], error["msg"]
    assert "tok-000000000007" not in error["msg"], "the refusal repeats the token the value carries"


def test_empty_gitlab_url_means_the_default_not_a_refusal():
    """An operator commenting the line out must not brick the boot."""
    with patch.dict(os.environ, {"GITLAB_URL": ""}, clear=True):
        assert Embry0Config(_env_file=None).gitlab_url == "https://gitlab.com"  # type: ignore[call-arg]
