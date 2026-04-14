import pytest

from legion.sandbox.github.git_ops import (
    build_clone_url,
    build_credential_helper_script,
    build_sandbox_credential_config_cmd,
)


def test_build_clone_url():
    url = build_clone_url("owner/repo")
    assert url == "https://github.com/owner/repo.git"


def test_build_credential_helper_script():
    script = build_credential_helper_script("http://git-proxy:8081")
    assert "curl" in script
    assert "http://git-proxy:8081/git-credentials" in script


def test_build_sandbox_credential_config_cmd_valid_url():
    cmd = build_sandbox_credential_config_cmd("http://host.docker.internal:9101")
    assert "git config --global credential.helper" in cmd
    assert "curl -sf http://host.docker.internal:9101/git-credentials" in cmd


def test_build_sandbox_credential_config_cmd_rejects_bad_url():
    with pytest.raises(ValueError, match="http://host:port"):
        build_sandbox_credential_config_cmd("http://evil.com/x; rm -rf /; #")

    with pytest.raises(ValueError):
        build_sandbox_credential_config_cmd("")

    with pytest.raises(ValueError):
        build_sandbox_credential_config_cmd("https://secure.com:443")  # https not http
