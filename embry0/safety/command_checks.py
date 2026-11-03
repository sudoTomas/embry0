"""Ring-3 rules on what a shell command does, read from its parse instead of its text.

A regular expression cannot tell ``docker ps`` from ``cat docker-compose.yml``,
or ``cat .env`` from ``cat .env.example``, and the forms that run a command or
read a file are too many to list (substitutions, runners, ``-c`` strings,
quotes, variables, globs). ``embry0.safety.bash_paths`` parses the command and
reports every command it runs, at any depth, and every path it reads; these
rules match those by name:

- a command whose name (its last ``/``-part, after NFKC, any case) is ``docker``,
  ``dockerd`` or starts with ``docker-`` (``docker-compose``, ``docker-buildx``,
  ``docker-credential-*``): the container clients and daemon;
- a path read whose name is ``.env`` (any case, or an editor's backup of it,
  ``.env~``), or a glob that can match ``.env``: the file of secrets a project
  keeps out of version control.

Both rules are best effort. What the parse cannot see these rules cannot
either: a command or a name only known at run time, a program that runs text
it is given without being listed as a runner, commands piped into a shell, an
interpreter's or a program's own file access, the files under a directory a
recursive read reaches, a file copied or renamed before the copy is read, and
a copy another tool keeps. Ring 1 is the boundary for those.
"""

from __future__ import annotations

import fnmatch
import os
import re
import unicodedata
from collections.abc import Iterable
from typing import Final

from embry0.safety.path_patterns import Lit, Part, PathRef, Wild, could_match

# Commands refused wherever a shell command runs them; a "*" matches the rest of a name.
DANGEROUS_COMMANDS: Final[tuple[str, ...]] = ("docker", "dockerd", "docker-*")
# Files refused wherever a shell command reads them.
SECRET_FILES: Final[tuple[str, ...]] = (".env",)


def command_name(text: str) -> str:
    """A command word's name as these rules compare it: its last ``/``-part, NFKC-normalized, lower case."""
    return os.path.basename(unicodedata.normalize("NFKC", text)).lower()


def runs_any(commands: Iterable[str], names: Iterable[str]) -> str | None:
    """The first command in ``commands`` whose name matches one of ``names``, if any."""
    patterns = [n.lower() for n in names]
    for command in commands:
        name = command_name(command)
        if any(fnmatch.fnmatchcase(name, p) for p in patterns):
            return command
    return None


def reads_any(reads: Iterable[PathRef], names: Iterable[str]) -> PathRef | None:
    """The first path in ``reads`` named one of ``names`` (any case, or a backup of one), or a glob that can match one.

    A path whose name holds text only known at run time (the files under a
    directory a recursive read reaches, a substitution's output) is not
    compared: what it names is not in the command.
    """
    names = [n.lower() for n in names]
    backups = [re.compile(re.escape(n) + r"[~.]*") for n in names]
    for ref in reads:
        name = _name(ref)
        if name is None:
            continue
        if name.is_exact:
            if any(b.fullmatch(name.path[1:].lower()) for b in backups):
                return ref
        elif any(could_match(name, "/" + n) or could_match(name, "/" + n.upper()) for n in names):
            return ref
    return None


def _name(ref: PathRef) -> PathRef | None:
    """The last ``/``-part of the paths ``ref`` names, as a path under ``/``; None when run-time text is in it."""
    parts = list(ref.parts)
    if parts and isinstance(parts[-1], Lit) and parts[-1].text.endswith("/"):
        text = parts[-1].text.rstrip("/")
        parts[-1:] = [Lit(text)] if text else []
    name: list[Part] = []
    for part in reversed(parts):
        if isinstance(part, Wild):
            return None
        if isinstance(part, Lit) and "/" in part.text:
            rest = part.text.rpartition("/")[2]
            return PathRef((Lit("/"), *([Lit(rest)] if rest else []), *name))
        name.insert(0, part)
    return None
