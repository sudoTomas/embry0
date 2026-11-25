"""A note's body as embry0 posts it to GitLab: words, never quick actions.

GitLab runs a quick action (``/merge``, ``/approve``, ``/close``, ``/assign @someone``) written at the start of a
line of a note, with the rights of the account that posted it, which for embry0 is a forge credential. A note
embry0 posts carries words it did not choose (a person's note, an agent's review), so every line that starts with a
slash, after any spaces, tabs or carriage returns, gets a zero-width space before the slash. GitLab's quick-action
parser deletes carriage returns, then matches a slash at the very start of a line only: a line ``\r/merge`` is
``/merge`` to it. The note reads the same. Leaf module: imports nothing of embry0.
"""

from __future__ import annotations

import re
from typing import Final

#: A slash at the start of a line, after any spaces, tabs or carriage returns: where GitLab looks for a quick action
#: once it has deleted the carriage returns.
_LINE_START_SLASH: Final = re.compile(r"^([ \t\r]*)/", re.MULTILINE)
#: Put before such a slash: invisible in the rendered note, and not a slash.
ZERO_WIDTH_SPACE: Final = "\u200b"


def without_quick_actions(body: str) -> str:
    """``body`` with no line GitLab would run as a quick action; otherwise unchanged. Idempotent."""
    return _LINE_START_SLASH.sub(lambda m: f"{m.group(1)}{ZERO_WIDTH_SPACE}/", body)
