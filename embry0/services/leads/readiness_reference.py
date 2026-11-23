"""The readiness table in ``docs/leads.md``, generated from the ``leads`` section's defaults (#168).

What a backlog ticket's readiness reads before any document says otherwise:
each signal with its points, the labels of each role, the done phrases and
the epic title words. The block between the two markers is written by::

    python -m embry0.services.leads.readiness_reference --write docs/leads.md

and ``tests/unit/docs/test_readiness_reference.py`` fails when the committed
block differs from what the defaults render, so the page can never say other
than the code does. ``--check`` says the same from the command line.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

from embry0.services.leads.readiness import DESCRIPTION_CHARS, FULL_DESCRIPTION_CHARS, SHORT_TITLE_WORDS
from embry0.settings.reference import committed_block, splice
from embry0.settings.sections.leads import LABEL_ROLES, SIGNALS, LabelRole, LeadsSettings, Signal

START: Final = "<!-- readiness-table:start -->"
END: Final = "<!-- readiness-table:end -->"
MARKERS: Final = (START, END)
COMMAND: Final = "python -m embry0.services.leads.readiness_reference --write docs/leads.md"

#: When each signal counts, said with the section's values.
_WHEN: Final[Mapping[Signal, Callable[[LeadsSettings], str]]] = {
    "description_short": lambda _: f"a description shorter than {DESCRIPTION_CHARS} characters, not blank",
    "description": lambda _: f"a description of {DESCRIPTION_CHARS} to {FULL_DESCRIPTION_CHARS - 1} characters",
    "description_full": lambda _: f"a description of {FULL_DESCRIPTION_CHARS} characters or more",
    "paths": lambda _: "the title or the description names a file path",
    "error": lambda _: "the title or the description quotes error text (a traceback, an exception, an HTTP 5xx)",
    "done": lambda _: "the description holds a done phrase",
    "ready_label": lambda _: "a `ready` label",
    "small": lambda s: f"a `small` label, or an estimate of at most {_number(s.small_estimate_max)}",
    "large": lambda s: f"a `large` label, or an estimate of at least {_number(s.large_estimate_min)}",
    "not_ready": lambda _: "a `not_ready` label, or else an epic by its title",
    "short_title": lambda _: f"a title of {SHORT_TITLE_WORDS} words or fewer",
}
#: What each label role does.
_ROLE: Final[Mapping[LabelRole, str]] = {
    "ready": "reads as ready to start",
    "small": "reads as small",
    "large": "reads as large",
    "not_ready": "reads as not ready",
    "urgent": "makes the lead critical when the tracker gives no priority",
    "high": "makes the lead high when the tracker gives no priority",
}


def _number(value: float) -> str:
    return f"{value:g}"


def _code(items: tuple[str, ...]) -> str:
    return ", ".join(f"`{item}`" for item in items) if items else "none"


def render(settings: LeadsSettings | None = None) -> str:
    """The readiness block for ``settings`` (the defaults when None), markers included."""
    policy = settings or LeadsSettings()
    lines = [START, "", "| Signal | Points | When |", "| --- | --- | --- |"]
    for signal in SIGNALS:
        points = policy.readiness_weights.get(signal, 0)
        lines.append(f"| `{signal}` | {points:+d} | {_WHEN[signal](policy)} |")
    lines += ["", "| Role | Labels | A ticket with one |", "| --- | --- | --- |"]
    for role in LABEL_ROLES:
        lines.append(f"| `{role}` | {_code(policy.readiness_labels.get(role, ()))} | {_ROLE[role]} |")
    lines += [
        "",
        f"Done phrases (`done_phrases`): {_code(policy.done_phrases)}.",
        "",
        f"Epic title words (`epic_title_words`): {_code(policy.epic_title_words)}.",
        "",
        END,
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write or check the readiness table in a Markdown file.")
    parser.add_argument("path", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="rewrite the block between the markers")
    mode.add_argument("--check", action="store_true", help="exit 1 when the block differs from the defaults")
    args = parser.parse_args(argv)
    text = args.path.read_text(encoding="utf-8")
    block = render()
    if args.write:
        args.path.write_text(splice(text, block, markers=MARKERS), encoding="utf-8")
        return 0
    if committed_block(text, markers=MARKERS) != block:
        print(f"{args.path}: the readiness table is out of date; run {COMMAND}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
