from legion.sandbox.github.git_ops import build_clone_url, build_credential_helper_script


def test_build_clone_url():
    url = build_clone_url("owner/repo")
    assert url == "https://github.com/owner/repo.git"


def test_build_credential_helper_script():
    script = build_credential_helper_script("http://git-proxy:8081")
    assert "curl" in script
    assert "http://git-proxy:8081/git-credentials" in script
