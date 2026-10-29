"""The exit codes of the document CLIs, ``embry0 settings`` and ``embry0 progression-pack`` (#237).

Both read a document from a file, check it, diff it against the stored one
and write it through the API, so a script drives them the same way: every
verb of both answers the same outcome with the same code.

    0  done: valid, applied, nothing to change, written
    1  refused: the document has problems (the API's 422 or 413, or
       ``check``), or the answer at the prompt was no
    2  usage: a bad argument (argparse's own exit status), a file that cannot
       be read or written, or a write without a terminal to confirm on and
       without ``--yes``
    3  conflict: the document changed since it was read (409)
    4  unavailable: the orchestrator cannot be reached, refused the key
       (401, 403), or failed

Each command's docstring says what else falls under a code for its own verbs.
Scripts test these numbers, so changing one is a breaking change.
"""

from __future__ import annotations

from typing import Final

OK: Final = 0
REFUSED: Final = 1
USAGE: Final = 2
CONFLICT: Final = 3
UNAVAILABLE: Final = 4

#: The scheme in one line, for each command's ``--help``.
SUMMARY: Final = "Exit codes: 0 done, 1 refused, 2 usage, 3 conflict, 4 orchestrator unavailable."
