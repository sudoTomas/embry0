"""Parse /answer N: <text> and /skip N directives out of a comment body.

Used by both the GitHub issue_comment webhook handler and the Telegram
reply-to-message handler. Pure function; no I/O.
"""

from __future__ import annotations

import re

# A directive line: "/answer 12: ..." or "/skip 3". Sequence is positive int.
_DIRECTIVE_RE = re.compile(
    r"^\s*/(answer|skip)\s+(\d+)\s*(?::\s*(.*))?\s*$",
    re.IGNORECASE,
)


def parse_answer_directives(body: str) -> list[tuple[int, str, str]]:
    """Parse /answer N: <text> and /skip N directives from a comment body.

    Returns a list of (sequence_number, action, value) tuples in the order
    they appear. action is "answer" or "skip". value is the text after the
    colon for "answer" (may span multiple lines until the next directive),
    or "" for "skip". sequence_number is a positive integer (zero/negative
    are silently dropped).

    Lines that don't match the directive pattern are kept as continuation
    of the most recent answer's value.
    """
    if not body:
        return []

    lines = body.replace("\r\n", "\n").split("\n")
    out: list[tuple[int, str, str]] = []
    current: list[str] | None = None  # accumulator for the current answer

    def _flush() -> None:
        if current is not None and out:
            seq, action, _ = out[-1]
            if action == "answer":
                out[-1] = (seq, action, "\n".join(current).strip())

    for line in lines:
        m = _DIRECTIVE_RE.match(line)
        if m:
            # Only flush when a *next* directive closes the prior answer.
            # Trailing non-directive lines after the final directive are
            # treated as conversational text, not part of the answer value.
            _flush()
            action = m.group(1).lower()
            seq = int(m.group(2))
            value = (m.group(3) or "").strip()
            if seq <= 0:
                current = None
                continue
            out.append((seq, action, value))
            current = [value] if action == "answer" else None
        elif current is not None:
            # Continuation of the previous answer's value (only kept if a
            # subsequent directive flushes the accumulator).
            current.append(line)

    return out
