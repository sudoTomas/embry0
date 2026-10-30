"""How a heading an operator names matches a markdown heading in a ticket (#182).

One rule, for every reader of a ticket's sections (``ticket_section`` in
``embry0.workflows.issue_to_pr.evidence``) and for the ``done_when`` section's
check of the headings an operator lists:

- **Case** does not matter (``casefold``: ``STRASSE`` is ``Straße``).
- **Trailing ``#`` marks and spaces** do not count, on either side. A
  ticket's ``## Done in C#`` reads as ``Done in C`` to a markdown parser that
  drops a heading's closing marks, so the named ``Done in C#`` must drop them
  too.
- A heading matches a ticket heading that **starts with its words, at a word
  boundary**: ``Acceptance criteria`` matches ``Acceptance criteria (must)``
  and ``Acceptance criteria:``, while ``A`` does not match ``Approach`` nor
  ``Done when`` ``Done whenever``.

Pure and standard library only, so the settings sections import it.
"""

from __future__ import annotations


def heading_key(text: str) -> str:
    """``text`` as headings are compared: trimmed, without trailing ``#`` marks, case-folded."""
    return text.strip().rstrip("# \t").casefold()


def _word(char: str) -> bool:
    return char.isalnum() or char == "_"


def heading_matches(found: str, wanted: str) -> bool:
    """Whether a ticket's heading ``found`` starts with the words of ``wanted``, at a word boundary.

    An empty ``wanted`` matches nothing.
    """
    have, want = heading_key(found), heading_key(wanted)
    if not want or not have.startswith(want):
        return False
    if len(have) == len(want):
        return True
    return not (_word(want[-1]) and _word(have[len(want)]))
