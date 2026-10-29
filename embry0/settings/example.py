"""The example settings document: every section, every key at its default, each described.

``docs/examples/settings.yaml`` is this text for the application's sections,
written by::

    python -m embry0.settings.example --write docs/examples/settings.yaml

and ``tests/unit/settings/test_example.py`` fails when the committed file
differs from what the sections render, so the example never drifts from
the registry. ``embry0 settings export --default`` prints the same text.

Every key is written as a comment, its description above it. Applying the
file as it is so sets nothing: a document holds only what an operator
changes, and a key nobody wrote takes its default, including a default a
later release changes. An operator uncomments the keys they set. ``render``
with ``commented=False`` writes the same lines with every key active, which
the test reads to prove each commented value is the key's default.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path
from typing import Any, Final

from embry0.settings.document import dump_yaml
from embry0.settings.registry import MANAGED_BY_FILE, MANAGED_BY_KEY, SCHEMA_VERSION, VERSION_KEY, Catalog, Section

#: The file the example lives in, from the repository root.
EXAMPLE_PATH: Final = "docs/examples/settings.yaml"
COMMAND: Final = f"python -m embry0.settings.example --write {EXAMPLE_PATH}"

_WIDTH: Final = 78

_HEADER: Final = f"""\
The embry0 settings document: every section, every key at its default.

Generated from the sections in embry0/settings/sections/ by
  {COMMAND}
and printed by `embry0 settings export --default`.

Every key below is a comment, so applying this file as it is sets nothing.
Uncomment the keys you change and leave the rest out: a key you do not
write takes its default, including a default a later release changes.
Check a file with `embry0 settings check FILE`, see what it would change
with `embry0 settings diff FILE`, and store it with `embry0 settings apply
FILE`. Each key is described in docs/configuration.md#settings-reference.
"""

_MANAGED_BY: Final = (
    "Uncomment to keep this document in a file, written with `embry0 settings apply`: the dashboard then "
    "shows it read-only. Leave it out to edit the document on the dashboard too."
)


def _comment(text: str, indent: str = "") -> list[str]:
    """``text`` as comment lines, each paragraph wrapped to the file's width."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append(f"{indent}#")
            continue
        lead = len(paragraph) - len(paragraph.lstrip(" "))
        wrapped = textwrap.wrap(
            paragraph.strip(),
            width=_WIDTH - len(indent) - 2 - lead,
            break_long_words=False,
            break_on_hyphens=False,
        )
        lines.extend(f"{indent}# {' ' * lead}{line}" for line in wrapped)
    return lines


def _section(section: Section[Any], *, commented: bool) -> list[str]:
    indent = "  "
    defaults = section.defaults().model_dump(mode="json")
    lines = [*_comment(section.summary), f"{section.name}:"]
    for index, key in enumerate(section.fields):
        if index:
            lines.append("")
        lines.extend(_comment(section.model.model_fields[key].description or "", indent))
        mark = "# " if commented else ""
        lines.extend(f"{indent}{mark}{line}" for line in dump_yaml({key: defaults[key]}).splitlines())
    return lines


def render(catalog: Catalog, *, commented: bool = True) -> str:
    """The example document for ``catalog``: every key commented out, or with ``commented=False`` set."""
    lines = [*_comment(_HEADER.rstrip("\n")), f"{VERSION_KEY}: {SCHEMA_VERSION}", ""]
    lines += [*_comment(_MANAGED_BY), f"# {MANAGED_BY_KEY}: {MANAGED_BY_FILE}"]
    for section in catalog:
        lines += ["", *_section(section, commented=commented)]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from embry0.settings.sections import CATALOG

    parser = argparse.ArgumentParser(description="Write or check the example settings document.")
    parser.add_argument("path", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write the example to the file")
    mode.add_argument("--check", action="store_true", help="exit 1 when the file differs from the sections")
    args = parser.parse_args(argv)
    text = render(CATALOG)
    if args.write:
        args.path.parent.mkdir(parents=True, exist_ok=True)
        args.path.write_text(text, encoding="utf-8")
        return 0
    try:
        current = args.path.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current != text:
        print(f"{args.path}: the example settings document is out of date; run {COMMAND}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
