"""Ring-3 rules on what a shell command does, read from its parse instead of its text.

A regular expression cannot tell ``docker ps`` from ``cat docker-compose.yml``,
or ``cat .env`` from ``cat .env.example``, and the forms that run a command or
read a file are too many to list (substitutions, runners, ``-c`` strings,
quotes, variables, globs). ``embry0.safety.bash_paths`` parses the command and
reports every command it runs, at any depth, and every path it reads; these
rules match those by name:

- a command whose name (its last ``/``-part, after NFKC, any case) is ``docker``
  or ``dockerd``, wherever it is, or starts with ``docker-`` (``docker-compose``,
  ``docker-buildx``, ``docker-credential-*``) when it is run by its bare name
  (found through ``PATH``) or from the image's system directories: the
  container clients and daemon. A ``docker-*`` script run by a relative path or
  from the workspace is the repository's own;
- a path read whose name is ``.env`` (any case, or an editor's backup of it,
  ``.env~``), or a glob that could match ``.env`` in any case under any glob
  options (``dotglob`` and ``GLOBIGNORE`` let a glob match a leading ``.``,
  ``nocaseglob`` makes it ignore case, and a setting can outlast one command):
  the file of secrets a project keeps out of version control.

Both rules are best effort. What the parse cannot see these rules cannot
either: a command or a name only known at run time, a Docker client copied or
linked under another name, a program that runs text
it is given without being listed as a runner, commands piped into a shell, an
interpreter's or a program's own file access, the files under a directory a
recursive read reaches, a file copied or renamed before the copy is read, and
a copy another tool keeps. Ring 1 is the boundary for those.
"""

from __future__ import annotations

import fnmatch
import itertools
import os
import re
import unicodedata
from collections.abc import Iterable
from typing import Final

from embry0.safety.path_patterns import Lit, One, Part, PathRef, Star, Wild, could_match

# Commands refused wherever a shell command runs them; a "*" matches the rest of a name, and a
# name with one is matched only when the program is the image's (SYSTEM_DIRS, or found through PATH).
DANGEROUS_COMMANDS: Final[tuple[str, ...]] = ("docker", "dockerd", "docker-*")
# Where the image installs programs (/usr/libexec/docker/cli-plugins included).
SYSTEM_DIRS: Final[tuple[str, ...]] = ("/usr", "/bin", "/sbin", "/opt", "/snap")
# Files refused wherever a shell command reads them.
SECRET_FILES: Final[tuple[str, ...]] = (".env",)


def command_name(text: str) -> str:
    """A command word's name as these rules compare it: its last ``/``-part, NFKC-normalized, lower case."""
    return os.path.basename(unicodedata.normalize("NFKC", text)).lower()


def runs_any(commands: Iterable[str], names: Iterable[str]) -> str | None:
    """The first command in ``commands`` whose name matches one of ``names``, if any.

    A name with a ``*`` matches only a program the image installs: one run by its bare name,
    which ``PATH`` finds, or by a path under ``SYSTEM_DIRS``. A relative path, or one in the
    workspace, is a script of the repository's.
    """
    patterns = [n.lower() for n in names]
    for command in commands:
        name = command_name(command)
        if any(fnmatch.fnmatchcase(name, p) and ("*" not in p or _installed(command)) for p in patterns):
            return command
    return None


def _installed(command: str) -> bool:
    """Whether ``command`` runs a program the image installs: a bare name, or a path under SYSTEM_DIRS."""
    text = unicodedata.normalize("NFKC", command)
    if "/" not in text:
        return True
    if not text.startswith("/"):
        return False
    path = os.path.normpath("/" + text.lstrip("/"))
    return any(path == d or path.startswith(d + "/") for d in SYSTEM_DIRS)


def reads_any(reads: Iterable[PathRef], names: Iterable[str]) -> PathRef | None:
    """The first path in ``reads`` named one of ``names`` (any case, or a backup of one), or a glob that
    could match one under any glob options.

    A path whose name holds text only known at run time (the files under a
    directory a recursive read reaches, a substitution's output) is not
    compared: what it names is not in the command.
    """
    names = [n.lower() for n in names]
    backups = [re.compile(re.escape(n) + r"[~.]*") for n in names]
    spellings = [s for n in names for s in _cases(n)]
    for ref in reads:
        name = _name(ref)
        if name is None:
            continue
        if name.is_exact:
            if any(b.fullmatch(name.path[1:].lower()) for b in backups):
                return ref
        elif any(could_match(_loosened(name), "/" + s) for s in spellings):
            return ref
    return None


def shown(ref: PathRef) -> str:
    """``ref`` as a refusal shows it: a glob a glob option may have widened, as it is written."""
    parts: list[Part] = []
    for part in ref.parts:
        parts.extend(part.glob if isinstance(part, Wild) and part.glob is not None else (part,))
    return PathRef(tuple(parts)).path


def _cases(name: str) -> list[str]:
    """Every spelling of ``name`` in upper and lower case: ``nocaseglob`` matches any of them."""
    return ["".join(c) for c in itertools.product(*({ch.lower(), ch.upper()} for ch in name))]


def _loosened(ref: PathRef) -> PathRef:
    """A glob as ``dotglob`` or ``GLOBIGNORE`` reads it: a leading ``.`` matched like any character."""
    return PathRef(
        tuple(Star() if isinstance(p, Star) else One(p.chars) if isinstance(p, One) else p for p in ref.parts)
    )


def _name(ref: PathRef) -> PathRef | None:
    """The last ``/``-part of the paths ``ref`` names, as a path under ``/``; None when run-time text is in it."""
    parts = list(ref.parts)
    if parts and isinstance(parts[-1], Lit) and parts[-1].text.endswith("/"):
        text = parts[-1].text.rstrip("/")
        parts[-1:] = [Lit(text)] if text else []
    name: list[Part] = []
    for part in reversed(parts):
        if isinstance(part, Wild) and part.glob is not None and not any(isinstance(p, Wild) for p in part.glob):
            name[:0] = part.glob  # a glob a glob option may have widened: as the default options read it
            continue
        if isinstance(part, Wild):
            return None
        if isinstance(part, Lit) and "/" in part.text:
            rest = part.text.rpartition("/")[2]
            return PathRef((Lit("/"), *([Lit(rest)] if rest else []), *name))
        name.insert(0, part)
    return None
