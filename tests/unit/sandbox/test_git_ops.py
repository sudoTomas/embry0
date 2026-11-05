"""The git-proxy is the sandbox's remote for the job's repository (#521).

``proxy_route`` checks every part of the route against a strict format, since
it ends up in shell scripts and git config files. The last tests run the
agent's config script for real, in a throwaway HOME with the bearer on stdin,
and read back what git stored.
"""

import os
import shutil
import subprocess

import pytest

from embry0.execution import sandbox_git
from embry0.sandbox.github.git_ops import check_sandbox_token, proxy_route
from tests.fixtures.sandbox_git_local import local_config, localize

PROXY = "http://git-proxy:9101"
TOKEN = "a" * 43  # 43 URL-safe chars — within the [40,80] range
ORIGIN = "https://github.com"
REPO = "acme/widgets"


def _route(**overrides: str):
    args = {"git_proxy_url": PROXY, "origin": ORIGIN, "repo": REPO, **overrides}
    return proxy_route(args["git_proxy_url"], origin=args["origin"], repo=args["repo"])


def test_the_route_rewrites_the_repo_to_the_proxy_and_sends_the_bearer_there():
    route = _route()
    assert route.rewrite_key == "url.http://git-proxy:9101/git/acme/widgets.git.insteadOf"
    assert route.rewrite_value == "https://github.com/acme/widgets.git"
    assert route.header_key == "http.http://git-proxy:9101/git/.extraHeader"


def test_the_agents_config_script_removes_a_credential_helper_and_holds_no_bearer():
    body = sandbox_git.agent_config_body(_route(), None)
    assert "--unset-all credential.helper" in body, "a helper left by an older orchestrator must be removed"
    assert "/git-credentials" not in body, "the sandbox must not ask the proxy for the forge token"
    assert TOKEN not in body and "Bearer $embry0_bearer" in body, "the bearer comes from stdin"


@pytest.mark.parametrize(
    "bad_url",
    ["http://evil.com/x; rm -rf /; #", "", "https://secure.com:443", "not-a-url", "http://git-proxy:9101/"],
)
def test_rejects_a_bad_proxy_url(bad_url):
    with pytest.raises(ValueError, match="git_proxy_url must match"):
        _route(git_proxy_url=bad_url)


@pytest.mark.parametrize("bad_token", ["tooshort", "a" * 30 + "; rm -rf /; #" + "a" * 20, "a" * 81, "a" * 39 + "'"])
def test_rejects_a_bad_token(bad_token):
    with pytest.raises(ValueError, match="sandbox_token must match"):
        check_sandbox_token(bad_token)


@pytest.mark.parametrize(
    "bad_origin",
    ["github.com", "ftp://github.com", "https://github.com/", "https://github.com/a b", "https://x.com';id;'", ""],
)
def test_rejects_a_bad_origin(bad_origin):
    with pytest.raises(ValueError, match="origin must be"):
        _route(origin=bad_origin)


@pytest.mark.parametrize("bad_repo", ["widgets", "acme/../other", "acme/./w", "acme//w", "acme/w'", "/acme/w", "a/b c"])
def test_rejects_a_bad_repo(bad_repo):
    with pytest.raises(ValueError, match="repo must be"):
        _route(repo=bad_repo)


def test_accepts_a_self_managed_origin_and_a_nested_project():
    route = _route(origin="https://git.example.com:8443/gitlab", repo="group/sub/project")
    assert route.rewrite_value == "https://git.example.com:8443/gitlab/group/sub/project.git"


needs_git = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="needs git and bash")


def _env(home: str) -> dict[str, str]:
    return {"HOME": home, "PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1"}


def _run(tmp_path, home: str, token: str) -> subprocess.CompletedProcess[str]:
    argv = sandbox_git.script(sandbox_git.agent_config_body(_route(), None), repo=None)
    return subprocess.run(
        localize(argv, local_config(tmp_path)),
        env=_env(home),
        input=f"{token}\n",
        capture_output=True,
        text=True,
        check=False,
    )


def _config(home: str) -> list[str]:
    listing = subprocess.run(["git", "config", "--global", "--list"], env=_env(home), capture_output=True, text=True)
    return listing.stdout.splitlines()


@needs_git
def test_running_the_script_twice_leaves_one_rewrite_one_header_and_no_helper(tmp_path):
    """A resume rewrites the bearer, and a pre-built image carries an older config: neither may stack."""
    home = str(tmp_path / "home")
    os.mkdir(home)
    old_helper = "!f() { curl -sf http://git-proxy:9101/git-credentials; }; f"
    subprocess.run(["git", "config", "--global", "credential.helper", old_helper], env=_env(home), check=True)

    first = _run(tmp_path, home, TOKEN)
    assert first.returncode == 0, first.stderr
    second = _run(tmp_path, home, "b" * 43)
    assert second.returncode == 0, second.stderr

    lines = _config(home)
    assert not [line for line in lines if line.startswith("credential.helper")], lines
    rewrites = [line for line in lines if ".insteadof=" in line]
    assert rewrites == ["url.http://git-proxy:9101/git/acme/widgets.git.insteadof=https://github.com/acme/widgets.git"]
    headers = [line for line in lines if "extraheader" in line]
    assert headers == [f"http.http://git-proxy:9101/git/.extraheader=Authorization: Bearer {'b' * 43}"]


@needs_git
def test_the_script_succeeds_on_a_fresh_home_with_no_gitconfig(tmp_path):
    home = str(tmp_path / "home")
    os.mkdir(home)
    result = _run(tmp_path, home, TOKEN)
    assert result.returncode == 0, f"unset of an absent helper must not fail the chain: {result.stderr}"
