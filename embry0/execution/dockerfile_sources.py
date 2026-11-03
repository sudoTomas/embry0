"""The build-context paths a Dockerfile reads, from the Dockerfile itself (#320).

The self-build path decides whether its sandbox image is stale from a hash of
what the image is built from. A hand-kept list of those files drifted from the
Dockerfile: it left out modules the image copies, so a change to one of them
kept the old image. :func:`context_sources` reads the list from the Dockerfile
instead: every ``COPY`` and ``ADD`` source taken from the build context, and
every ``RUN --mount=type=bind`` source taken from it. A source taken from
another stage or image (``--from``, ``from=``) is not in the context, and a
URL is not either.

The parser is strict: a form it cannot follow (a heredoc, a bind mount with
no source, an instruction too short to name a source) raises ``ValueError``
rather than leave a source out without a word. The real sandbox Dockerfiles
are parsed in the unit tests, so such a form fails CI before it ships.
"""

from __future__ import annotations

import json
import re
import shlex

_URL_PREFIXES = ("http://", "https://", "git@", "git://")
#: A RUN instruction's flags (``--mount=...``), before its command.
_LEADING_FLAGS = re.compile(r"(?:--\S+\s*)*")


def instructions(text: str) -> list[str]:
    """A Dockerfile's instructions, one per entry: comment lines dropped, line continuations joined.

    Docker drops a comment line inside a continued instruction too, as it does here.
    """
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    joined = "\n".join(lines).replace("\\\n", " ")
    return [" ".join(line.split()) for line in joined.splitlines() if line.strip()]


def _copy_sources(keyword: str, rest: str) -> list[str]:
    if rest.startswith("["):
        args = [str(a) for a in json.loads(rest)]
        flags: list[str] = []
    else:
        words = shlex.split(rest)
        flags = [w for w in words if w.startswith("--")]
        args = [w for w in words if not w.startswith("--")]
    if any(f.startswith("--from=") for f in flags):
        return []  # another stage or image, not the build context
    if len(args) < 2:
        raise ValueError(f"{keyword} names no source and destination: {keyword} {rest}")
    sources = args[:-1]
    if any(s.startswith("<<") for s in sources):
        raise ValueError(f"{keyword} with a heredoc is not followed by the build hash: {keyword} {rest}")
    if keyword == "ADD":
        sources = [s for s in sources if not s.startswith(_URL_PREFIXES)]
    return sources


def _mount_sources(rest: str) -> list[str]:
    flags = _LEADING_FLAGS.match(rest)
    sources: list[str] = []
    for word in (flags.group(0) if flags else "").split():
        if not word.startswith("--mount="):
            continue
        options: dict[str, str] = {}
        for part in word.removeprefix("--mount=").split(","):
            key, _, value = part.partition("=")
            options[key] = value
        if options.get("type", "bind") != "bind" or "from" in options:
            continue
        source = options.get("source") or options.get("src")
        if not source:
            raise ValueError(f"a bind mount of the whole build context is not followed by the build hash: {word}")
        sources.append(source)
    return sources


def context_sources(text: str) -> list[str]:
    """Every build-context path the Dockerfile ``text`` reads, in order, each once, as written (``embry0/agents/``)."""
    found: list[str] = []
    for instruction in instructions(text):
        keyword, _, rest = instruction.partition(" ")
        keyword = keyword.upper()
        if keyword in ("COPY", "ADD"):
            found += _copy_sources(keyword, rest)
        elif keyword == "RUN":
            found += _mount_sources(rest)
    return list(dict.fromkeys(found))
