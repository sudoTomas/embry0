"""Run embry0's sandbox git scripts against a local repository, in a test.

The production prelude requires embry0's git config at
``/run/embry0/git/config``, owned by root. A test can create neither, so
:func:`localize` swaps in the same prelude pointed at a config the test
wrote and at the test's own uid. Everything else in the script is what
production runs.
"""

from __future__ import annotations

import os
from pathlib import Path

from embry0.execution import sandbox_git

IDENTITY = '[user]\n\tname = "embry0"\n\temail = "embry0@example.com"\n'


def local_config(root: Path, text: str = IDENTITY) -> Path:
    """embry0's git config for a test, under ``root``, with the modes the prelude requires."""
    config = root / "run" / "embry0" / "git" / "config"
    config.parent.mkdir(parents=True, exist_ok=True)
    for d in (config.parent, config.parent.parent):
        d.chmod(0o755)
    config.write_text(text)
    config.chmod(0o644)
    return config


def localize(argv: list[str], config: Path) -> list[str]:
    """``argv`` (from sandbox_git.script or .command) with its prelude pointed at ``config`` and this uid."""
    production = sandbox_git.prelude()
    assert argv[:2] == ["bash", "-c"] and argv[2].startswith(production), "not a sandbox_git argv"
    local = sandbox_git.prelude(config_path=str(config), owner_uid=os.getuid())
    return [argv[0], argv[1], local + argv[2][len(production) :], *argv[3:]]


def body(argv: list[str]) -> str:
    """The part of a sandbox_git argv after its prelude: the repository check and the step's own shell."""
    production = sandbox_git.prelude()
    assert argv[:2] == ["bash", "-c"] and argv[2].startswith(production), f"not a sandbox_git argv: {argv[:3]}"
    return argv[2][len(production) :]


def readable(cmd: list[str]) -> str:
    """A docker exec argv as one line, the way a person reads the git in it: what a test's fake matches on.

    The prelude is left out (its pins name ``push``, ``commit`` and
    ``status``), and each rendered call reads as plain ``git``.
    """
    production = sandbox_git.prelude()
    words = [str(w).replace(production, "") for w in cmd]
    line = " ".join(words)
    for rendered, plain in (
        ('embry0_git_add "$@" embry0-git ', "git add "),
        ('embry0_git "$@" embry0-git ', "git "),
        ("embry0_git_add ", "git add "),
        ("embry0_git ", "git "),
    ):
        line = line.replace(rendered, plain)
    return line
