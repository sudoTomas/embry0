"""The settings reference in ``docs/configuration.md``, generated from the sections.

One table per section: each key with its type, default, allowed values,
the layers that may override it, the variable it was imported from and its
description. The block between the two markers is written by::

    python -m embry0.settings.reference --write docs/configuration.md

and ``tests/unit/docs/test_settings_reference.py`` fails when the committed
block differs from what the sections render, so a section field can never be
undocumented. ``--check`` says the same from the command line.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

from embry0.settings.registry import Catalog, Section

START: Final = "<!-- settings-reference:start -->"
END: Final = "<!-- settings-reference:end -->"
COMMAND: Final = "python -m embry0.settings.reference --write docs/configuration.md"

_TYPES: Final = {
    "string": "text",
    "integer": "whole number",
    "number": "number",
    "boolean": "true or false",
    "array": "list",
    "object": "mapping",
    "null": "null",
}


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _resolve(prop: Any, defs: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """``prop`` with every ``$ref`` (an Enum, a nested model) replaced by the definition it names."""
    if not isinstance(prop, dict) or depth > 20:
        return {}
    out = {k: v for k, v in prop.items() if k not in ("$ref", "allOf")}
    targets = [prop["$ref"]] if "$ref" in prop else []
    targets += [part["$ref"] for part in prop.get("allOf", []) if isinstance(part, dict) and "$ref" in part]
    for target in targets:
        out = {**_resolve(defs.get(str(target).rsplit("/", 1)[-1], {}), defs, depth + 1), **out}
    if "anyOf" in out:
        out["anyOf"] = [_resolve(option, defs, depth + 1) for option in out["anyOf"]]
    if "items" in out:
        out["items"] = _resolve(out["items"], defs, depth + 1)
    return out


def _type(prop: dict[str, Any]) -> str:
    if "anyOf" in prop:
        return " or ".join(_type(option) for option in prop["anyOf"])
    if isinstance(prop.get("const"), bool):
        return json.dumps(prop["const"])
    if "enum" in prop or "const" in prop:
        return "text" if all(isinstance(v, str) for v in prop.get("enum", [prop.get("const")])) else "value"
    kind = prop.get("type")
    if kind == "array":
        items = prop.get("items")
        return f"list of {_type(items)}" if isinstance(items, dict) and items else "list"
    return _TYPES.get(str(kind), str(kind or "value"))


def _allowed(prop: dict[str, Any]) -> str:
    parts: list[str] = []
    for option in prop.get("anyOf", [prop]):
        if "enum" in option:
            parts.append("one of " + ", ".join(f"`{json.dumps(v, ensure_ascii=False)}`" for v in option["enum"]))
        if "const" in option:
            parts.append(f"`{json.dumps(option['const'], ensure_ascii=False)}`")
        for word, sign in (
            ("minimum", "≥"),
            ("exclusiveMinimum", ">"),
            ("maximum", "≤"),
            ("exclusiveMaximum", "<"),
        ):
            if word in option:
                parts.append(f"{sign} {option[word]}")
        for word, label in (("minLength", "at least {} characters"), ("maxLength", "at most {} characters")):
            if word in option:
                parts.append(label.format(option[word]))
        for word, label in (("minItems", "at least {} entries"), ("maxItems", "at most {} entries")):
            if word in option:
                parts.append(label.format(option[word]))
        if "pattern" in option:
            parts.append(f"matches `{option['pattern']}`")
        items = option.get("items")
        if isinstance(items, dict) and (inner := _allowed(items)):
            parts.append(f"each {inner}")
    return _cell(", ".join(parts))


def _section_table(section: Section[Any]) -> list[str]:
    from embry0.settings.env import env_name

    schema = section.model.model_json_schema()
    props: dict[str, Any] = schema.get("properties", {})
    defs: dict[str, Any] = schema.get("$defs", {})
    defaults = section.defaults().model_dump(mode="json")
    imports = section.imports()
    lines = [
        f"#### `{section.name}`",
        "",
        _cell(section.summary),
        "",
        "| Key | Type | Default | Allowed | Overridable by | Imported from | Description |",
        "|---|---|---|---|---|---|---|",
    ]
    for key in section.fields:
        prop = _resolve(props.get(key, {}), defs)
        layers = [
            f"{name}, entry by entry" if key in section.merged_keys else name
            for name, keys in (("organization", section.org_keys), ("repository", section.repo_keys))
            if key in keys
        ]
        imported = imports.get(key)
        cells = [
            f"`{key}`",
            _type(prop),
            f"`{_cell(json.dumps(defaults.get(key), ensure_ascii=False))}`",
            _allowed(prop),
            ", ".join(layers),
            f"`{env_name(imported.attr)}`" if imported is not None else "",
            _cell(section.model.model_fields[key].description or ""),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def render(catalog: Catalog) -> str:
    """The reference block, markers included."""
    lines = [START, ""]
    if len(catalog) == 0:
        lines += ["No section is registered yet.", ""]
    for section in catalog:
        lines += _section_table(section)
    lines.append(END)
    return "\n".join(lines)


def splice(document: str, block: str) -> str:
    """``document`` with its reference block replaced by ``block``."""
    start = document.find(START)
    end = document.find(END)
    if start < 0 or end < start:
        raise ValueError(f"the markers {START} and {END} are missing or out of order")
    return document[:start] + block + document[end + len(END) :]


def committed_block(document: str) -> str:
    """The reference block as it stands in ``document``, markers included."""
    start = document.find(START)
    end = document.find(END)
    if start < 0 or end < start:
        raise ValueError(f"the markers {START} and {END} are missing or out of order")
    return document[start : end + len(END)]


def main(argv: list[str] | None = None) -> int:
    from embry0.settings.sections import CATALOG

    parser = argparse.ArgumentParser(description="Write or check the settings reference in a Markdown file.")
    parser.add_argument("path", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="rewrite the block between the markers")
    mode.add_argument("--check", action="store_true", help="exit 1 when the block differs from the sections")
    args = parser.parse_args(argv)
    text = args.path.read_text(encoding="utf-8")
    block = render(CATALOG)
    if args.write:
        args.path.write_text(splice(text, block), encoding="utf-8")
        return 0
    if committed_block(text) != block:
        print(f"{args.path}: the settings reference is out of date; run {COMMAND}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
