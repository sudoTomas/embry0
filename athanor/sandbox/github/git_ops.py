"""Git operations inside the sandbox container.

Uses the git credential proxy for authentication — tokens never
appear in sandbox process memory or CLI arguments.
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path

import structlog

from athanor.sandbox.events import EventType, emit_event

logger = structlog.get_logger(__name__)

GIT_AUTHOR_NAME = "Athanor"
GIT_AUTHOR_EMAIL = "[removed]"


def build_clone_url(repo: str) -> str:
    """Build HTTPS clone URL for a GitHub repo."""
    return f"https://github.com/{repo}.git"


def build_credential_helper_script(git_proxy_url: str) -> str:
    """Build a git credential helper script that queries the git proxy."""
    return f'#!/bin/sh\ncurl -sf "{git_proxy_url}/git-credentials"\n'


# Strict format for git proxy URLs: http://host:port (no path, no query).
# ProxyManager now produces Docker DNS names (e.g. http://git-proxy:9101) that
# resolve inside the sandbox-restricted DinD network. We validate defensively so
# that a future change to the source of the URL can't introduce shell injection
# via the `git config` command we build from it.
_GIT_PROXY_URL_RE = re.compile(r"^http://[a-zA-Z0-9.\-]+:\d+$")


def build_sandbox_credential_config_cmd(git_proxy_url: str) -> str:
    """Build the bash snippet that configures git inside a sandbox container
    to use the credential proxy.

    Used by the orchestrator-side init node to set up git via ``docker exec``.
    The returned string is a single ``git config --global credential.helper ...``
    command (no trailing semicolon) suitable for chaining with ``&&``.

    Raises:
        ValueError: if ``git_proxy_url`` does not match ``http://host:port``.
    """
    if not _GIT_PROXY_URL_RE.fullmatch(git_proxy_url):
        raise ValueError(f"git_proxy_url must match http://host:port (got: {git_proxy_url!r})")
    return f'git config --global credential.helper "!f() {{ curl -sf {git_proxy_url}/git-credentials; }}; f"'


def configure_git_credentials(git_proxy_url: str) -> str:
    """Write a git credential helper script and configure git to use it.

    Returns the path to the helper script (caller should clean up).
    """
    fd, path = tempfile.mkstemp(prefix="git-cred-", suffix=".sh")
    with os.fdopen(fd, "w") as f:
        f.write(build_credential_helper_script(git_proxy_url))
    os.chmod(path, 0o700)

    subprocess.run(
        ["git", "config", "--global", "credential.helper", path],
        check=True,
        capture_output=True,
    )
    return path


def clone_repo(repo: str, dest: Path, git_proxy_url: str, branch: str | None = None) -> Path:
    """Clone a repo into dest using credential proxy."""
    cred_path = configure_git_credentials(git_proxy_url)
    try:
        url = build_clone_url(repo)
        cmd = ["git", "clone", "--depth=1"]
        if branch:
            cmd.extend(["--branch", branch])
        cmd.extend([url, str(dest)])
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        emit_event(EventType.GIT_OPERATION, op="clone", repo=repo)
        return dest
    finally:
        os.unlink(cred_path)


def commit_and_push(
    repo_path: Path,
    branch_name: str,
    message: str,
    repo: str,
    git_proxy_url: str,
) -> None:
    """Create branch, commit all changes, and push."""
    cred_path = configure_git_credentials(git_proxy_url)
    try:
        for cmd in [
            ["git", "config", "user.email", GIT_AUTHOR_EMAIL],
            ["git", "config", "user.name", GIT_AUTHOR_NAME],
            ["git", "checkout", "-b", branch_name],
            ["git", "add", "-A", "--", "."],
            ["git", "commit", "-m", message],
        ]:
            subprocess.run(cmd, cwd=repo_path, check=True, capture_output=True, text=True)

        emit_event(EventType.GIT_OPERATION, op="commit", message=message)

        remote = build_clone_url(repo)
        subprocess.run(
            ["git", "push", "--force", remote, branch_name],
            cwd=repo_path,
            check=True,
            capture_output=True,
            text=True,
        )
        emit_event(EventType.GIT_OPERATION, op="push", branch=branch_name)
    finally:
        os.unlink(cred_path)
