"""Parse /answer <ref>: <text> and /skip <ref> directives out of a comment body.

Used by the GitHub issue_comment webhook handler. Pure function; no I/O.

``<ref>`` is either a stable ``issue_inputs`` id (``inp-a1b2c3d4e5f6``) — the
canonical form since #253 — or the 1-based position the question had in the
comment being replied to. Resolving either is the caller's job; this module
only hands back the raw token.
"""

from __future__ import annotations

import re

# A directive line: "/answer inp-abc123: ..." / "/answer 12: ..." / "/skip 3".
# The reference token is deliberately narrow (the id alphabet plus digits) so
# a prose line starting with "/answer" can never be mistaken for a directive.
_DIRECTIVE_RE = re.compile(
    r"^\s*/(answer|skip)\s+(-?[A-Za-z0-9_-]+)\s*(?::\s*(.*))?\s*$",
    re.IGNORECASE,
)
_INTEGER_RE = re.compile(r"^-?\d+$")


def is_directive_line(line: str) -> bool:
    """True when :func:`parse_answer_directives` would read ``line`` as a
    directive.

    Exported so an outbound renderer can tell whether a line it is about to
    echo into a comment would come straight back through the ``issue_comment``
    webhook as a directive — agent-authored question text is rendered verbatim,
    so embry0 must not be able to answer its own question (#251/#253).
    """
    return _DIRECTIVE_RE.match(line) is not None


def parse_answer_directives(body: str) -> list[tuple[str, str, str]]:
    """Parse /answer <ref>: <text> and /skip <ref> directives from a body.

    Returns a list of (reference, action, value) tuples in the order they
    appear. action is "answer" or "skip". value is the text after the colon
    for "answer" (may span multiple lines until the next directive), or ""
    for "skip". reference is the raw token — an input id, or a positive
    integer as a string (zero/negative are silently dropped).

    Lines that don't match the directive pattern are kept as continuation
    of the most recent answer's value.
    """
    if not body:
        return []

    lines = body.replace("\r\n", "\n").split("\n")
    out: list[tuple[str, str, str]] = []
    current: list[str] | None = None  # accumulator for the current answer

    def _flush() -> None:
        if current is not None and out:
            ref, action, _ = out[-1]
            if action == "answer":
                out[-1] = (ref, action, "\n".join(current).strip())

    for line in lines:
        m = _DIRECTIVE_RE.match(line)
        if m:
            # Only flush when a *next* directive closes the prior answer.
            # Trailing non-directive lines after the final directive are
            # treated as conversational text, not part of the answer value.
            _flush()
            action = m.group(1).lower()
            ref = m.group(2)
            value = (m.group(3) or "").strip()
            if _INTEGER_RE.fullmatch(ref) and int(ref) <= 0:
                current = None
                continue
            out.append((ref, action, value))
            current = [value] if action == "answer" else None
        elif current is not None:
            # Continuation of the previous answer's value (only kept if a
            # subsequent directive flushes the accumulator).
            current.append(line)

    return out
