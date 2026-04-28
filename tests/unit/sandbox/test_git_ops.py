import pytest

from athanor.sandbox.github.git_ops import (
    build_clone_url,
    build_sandbox_credential_config_cmd,
)

_VALID_TOKEN = "a" * 43  # 43 alphanumeric chars — within [40,80] range


def test_build_clone_url():
    url = build_clone_url("owner/repo")
    assert url == "https://github.com/owner/repo.git"


def test_build_sandbox_credential_config_cmd_valid_url():
    cmd = build_sandbox_credential_config_cmd("http://git-proxy:9101", _VALID_TOKEN)
    assert "git config --global credential.helper" in cmd
    assert "http://git-proxy:9101/git-credentials" in cmd
    assert f"Authorization: Bearer {_VALID_TOKEN}" in cmd


def test_build_sandbox_credential_config_cmd_rejects_bad_url():
    with pytest.raises(ValueError, match="http://host:port"):
        build_sandbox_credential_config_cmd("http://evil.com/x; rm -rf /; #", _VALID_TOKEN)

    with pytest.raises(ValueError):
        build_sandbox_credential_config_cmd("", _VALID_TOKEN)

    with pytest.raises(ValueError):
        build_sandbox_credential_config_cmd("https://secure.com:443", _VALID_TOKEN)  # https not http


def test_credential_cmd_with_valid_token():
    token = "a" * 50  # 50 chars, alphanumeric
    cmd = build_sandbox_credential_config_cmd("http://git-proxy:9101", token)
    assert "Authorization: Bearer " in cmd
    assert token in cmd
    assert "http://git-proxy:9101/git-credentials" in cmd


def test_credential_cmd_rejects_short_token():
    with pytest.raises(ValueError, match="sandbox_token must match"):
        build_sandbox_credential_config_cmd("http://git-proxy:9101", "tooshort")


def test_credential_cmd_rejects_token_with_metachars():
    with pytest.raises(ValueError, match="sandbox_token must match"):
        build_sandbox_credential_config_cmd("http://git-proxy:9101", "a" * 30 + "; rm -rf /; #" + "a" * 20)


def test_credential_cmd_still_rejects_bad_url():
    with pytest.raises(ValueError, match="git_proxy_url must match"):
        build_sandbox_credential_config_cmd("not-a-url", "a" * 50)
