"""The one rule for a branch, tag or sha that embry0 hands to git.

Every place that takes a branch or a ref checks it here: a job's
``context.ref``, the base-branch preference, a skill source's ref, the
diff base, the clone's base fetch and the git-proxy's push branches. A copy
of the pattern elsewhere drifts: one that still took a leading ``-`` stored
a base branch the clone then dropped for ``main``.
"""

from __future__ import annotations

import re

#: 1-255 characters from ``[A-Za-z0-9._/-]``, not starting with ``-``: git takes
#: no branch or tag name that starts with one. Applied with ``fullmatch``.
_REF = re.compile(r"^[A-Za-z0-9._/][A-Za-z0-9._/-]{0,254}$")

#: The rule in words, for refusal messages.
REF_RULE = "1-255 characters from [A-Za-z0-9._/-], not starting with '-'"


def valid_ref(ref: object) -> bool:
    """True when ``ref`` is a string the rule accepts.

    ``fullmatch``, not ``match``: ``$`` also matches before a final newline,
    so ``match`` would take ``main\\n``.
    """
    return isinstance(ref, str) and _REF.fullmatch(ref) is not None
