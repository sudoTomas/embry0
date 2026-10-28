"""What every document an operator writes may not use, whatever it configures.

The settings document (#160) and the progression content pack (#176) are
both YAML (or JSON) an operator writes by hand and embry0 stores. Both
refuse the same constructs, with the same words, from this one module:

- an **alias** (``*name``): a few of them expand a small text into a very
  large value;
- a **merge key** (``<<``): it hides where a value comes from, and needs no
  alias to work (``<<: {a: 1}``);
- a **key that is not text** (a mapping or a list used as a key);
- a **key written twice** in one mapping: YAML and JSON both keep only the
  second, silently, so a section written twice in a long document loses
  the first without a word.

The checks walk the composed YAML without recursion, so a deeply nested
text is reported by the caller's own depth check rather than crashing
here. Pure: no database and no web framework, so the offline CLI checks
import it too. Each caller turns a :class:`StructureProblem` into its own
problem type, whose shape (``loc``, ``type``, ``msg``) is the same.
"""

from __future__ import annotations

import difflib
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

import yaml

Loc = tuple[str | int, ...]

_MERGE_TAG: Final = "tag:yaml.org,2002:merge"


@dataclass(frozen=True)
class StructureProblem:
    """One construct a document may not use: where, what kind, and why. Never the value."""

    loc: Loc
    type: str
    msg: str


def _line(node: yaml.Node) -> int:
    return int(getattr(node.start_mark, "line", -1)) + 1


def structure_problems(root: yaml.Node | None, *, what: str = "a document") -> list[StructureProblem]:
    """What ``root`` uses that a document may not: aliases, merge keys, keys that are not text, keys written twice.

    ``root`` is the composed YAML (``yaml.compose``); ``what`` names the
    document in the alias message ("a settings document"). Problems come in
    document order, so an alias is reported where it is used.
    """
    problems: list[StructureProblem] = []
    seen: set[int] = set()
    queue: deque[tuple[yaml.Node, Loc]] = deque() if root is None else deque([(root, ())])
    while queue:
        node, loc = queue.popleft()
        if id(node) in seen:
            problems.append(StructureProblem(loc, "yaml_alias", f"Aliases (*name) are not supported in {what}."))
            continue
        seen.add(id(node))
        if isinstance(node, yaml.MappingNode):
            first: dict[str, int] = {}
            for key, value in node.value:
                if key.tag == _MERGE_TAG:
                    problems.append(
                        StructureProblem(loc, "yaml_merge", f"Line {_line(key)}: merge keys (<<) are not supported.")
                    )
                    continue
                if not isinstance(key, yaml.ScalarNode):
                    problems.append(StructureProblem(loc, "key_type", f"Line {_line(key)}: a key must be text."))
                    continue
                name = str(key.value)
                if name in first:
                    problems.append(
                        StructureProblem(
                            (*loc, name),
                            "duplicate_key",
                            f"'{name}' is written twice, on lines {first[name]} and {_line(key)}; YAML would keep "
                            "only the second. Keep one.",
                        )
                    )
                else:
                    first[name] = _line(key)
                queue.append((value, (*loc, name)))
        elif isinstance(node, yaml.SequenceNode):
            queue.extend((item, (*loc, index)) for index, item in enumerate(node.value))
    return problems


class DuplicateKey(ValueError):
    """A JSON object names one key twice; ``key`` is the name."""

    def __init__(self, key: str) -> None:
        super().__init__(f"'{key}' is written twice in one object; JSON would keep only the second. Keep one.")
        self.key = key


def refuse_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    """``json.loads``'s ``object_pairs_hook``: an object, or :class:`DuplicateKey` when it names a key twice."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise DuplicateKey(key)
        out[key] = value
    return out


#: The line git writes after a text's last line when the text has no final newline.
NO_NEWLINE: Final = "\\ No newline at end of file"
#: Marks, inside the diff, a last line with no final newline; the NUL character, which no
#: stored or accepted text holds.
_UNTERMINATED: Final = "\x00"


def _diff_lines(text: str) -> list[str]:
    lines = text.splitlines()
    if lines and not text.endswith(("\n", "\r")):
        lines[-1] += _UNTERMINATED
    return lines


def text_diff(before: str, after: str, *, before_name: str, after_name: str) -> str:
    """A unified diff of two texts, one line per line, as git writes it; empty when they are the same.

    A text with no final newline (text typed into a form often has none) is
    followed, after its last line, by git's own line ``\\ No newline at end of
    file``, with no prefix. A last line that differs only by its final
    newline shows as removed and added, as in git, and the hunk counts do not
    count the marker. Both dashboard tabs that show a document's diff read
    this one format.
    """
    out: list[str] = []
    for line in difflib.unified_diff(
        _diff_lines(before), _diff_lines(after), before_name, after_name, n=2, lineterm=""
    ):
        if line.endswith(_UNTERMINATED):
            out += [line[: -len(_UNTERMINATED)], NO_NEWLINE]
        else:
            out.append(line)
    return "\n".join(out) + "\n" if out else ""
