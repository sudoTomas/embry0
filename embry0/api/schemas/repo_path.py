"""One repo-path rule for every request schema (spec 3.5).

Two or more slash-separated segments — the rule ``POST /repos`` already
enforced (#162) so a GitLab nested subgroup like ``acme/widgets/shop`` is
expressible wherever a repo is named. GitHub paths remain two segments;
nothing here loosens them. The ``/repos``
routes read the repository with ``{full_repo:repo}``, built from the same
segment rule (``embry0/api/repo_route.py``), so the server side addresses
exactly the repositories a request schema accepts.

Anchored with ``\\Z``, not ``$``: ``$`` also matches before a trailing
newline, so ``"owner/name\\n"`` would pass and then flow into clone URLs and
sanitised container/volume names. ``embry0.mcp_server`` restates this rule
verbatim (it imports nothing else from ``embry0``), so the copied rule is the
strict one.

A segment holds letters, digits, ``_``, ``.`` and ``-``, and starts with a
letter, a digit or a dot. GitHub and GitLab both allow a leading dot in a
repository's name, and GitHub's organization profile repository is called
``.github``; what neither allows, and what would walk up a path, is a
segment that is exactly ``.`` or ``..``, so those two are refused. A leading
``-`` or ``_`` stays refused, as before. Whitespace, a newline or any other
control character is never part of a segment.
"""

from __future__ import annotations

import re

#: One segment of a repository path: not ``.`` or ``..`` exactly, then a letter, digit or dot, then
#: letters, digits, ``_``, ``.`` and ``-``. The ``/repos`` routes' ``repo`` path convertor is built from it too.
SEGMENT = r"(?!\.\.?(?![A-Za-z0-9_.-]))[A-Za-z0-9.][A-Za-z0-9_.-]*"
REPO_PATH_PATTERN = re.compile(rf"^{SEGMENT}(?:/{SEGMENT})+\Z")

REPO_PATH_ERROR = "repo must be 'group/name' or 'group/subgroup/name'"


def validate_repo_path(v: str) -> str:
    """Return ``v`` unchanged, or raise ValueError naming the accepted shapes."""
    if not REPO_PATH_PATTERN.match(v):
        raise ValueError(REPO_PATH_ERROR)
    return v
