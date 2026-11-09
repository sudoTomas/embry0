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
  the first without a word;
- a **whole number longer than the interpreter converts** to or from text
  (:func:`sys.get_int_max_str_digits`, 4,300 digits by default): reading
  one raised a plain ``ValueError`` past the readers' "invalid" path, and a
  YAML hex number that long reads, then cannot be stored (#424);
- a **number, true or false, or date YAML cannot read** (``0x_``,
  ``!!int abc``, ``!!float ""``, ``!!bool maybe``, ``!!timestamp x``): the
  safe loader's constructors raise ``ValueError``, ``IndexError``,
  ``KeyError`` or ``AttributeError`` on them, not a ``YAMLError``.

The checks walk the composed YAML without recursion, so a deeply nested
text is reported by the caller's own depth check rather than crashing
here. Pure: no database and no web framework, so the offline CLI checks
import it too. Each caller turns a :class:`StructureProblem` into its own
problem type, whose shape (``loc``, ``type``, ``msg``) is the same.
"""

from __future__ import annotations

import difflib
import json
import sys
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

import yaml

Loc = tuple[str | int, ...]

_MERGE_TAG: Final = "tag:yaml.org,2002:merge"
_INT_TAG: Final = "tag:yaml.org,2002:int"


@dataclass(frozen=True)
class StructureProblem:
    """One construct a document may not use: where, what kind, and why. Never the value."""

    loc: Loc
    type: str
    msg: str


def _line(node: yaml.Node) -> int:
    return int(getattr(node.start_mark, "line", -1)) + 1


def number_too_long(loc: Loc, *, key: bool = False) -> StructureProblem:
    """The problem of a whole number at ``loc`` with more digits than the interpreter converts to or from text.

    ``key``: the number is a key of the mapping at ``loc``, which is not echoed back.
    """
    limit = sys.get_int_max_str_digits()
    what = (
        f"A key is a whole number of more than {limit} digits."
        if key
        else f"A whole number can have at most {limit} digits."
    )
    return StructureProblem(loc, "number_too_long", what)


#: The scalar types YAML reads into something other than text, and the words for one it cannot read.
_TYPED_SCALARS: Final = {
    _INT_TAG: ("invalid_number", "a number"),
    "tag:yaml.org,2002:float": ("invalid_number", "a number"),
    "tag:yaml.org,2002:bool": ("invalid_value", "a true or false"),
    "tag:yaml.org,2002:timestamp": ("invalid_value", "a date"),
}


def _decimal_digits(text: str) -> int:
    """How many digits ``text`` has when it is a plain decimal whole number (sign and ``_`` aside), else 0."""
    digits = text.replace("_", "").lstrip("+-")
    return len(digits) if digits.isdigit() else 0


def _scalar_problem(
    node: yaml.ScalarNode, constructor: yaml.constructor.SafeConstructor, loc: Loc, *, key: bool = False
) -> StructureProblem | None:
    """Why the safe loader could not read the typed scalar ``node`` (a number, a true or false, a date), or None.

    Each is read as the safe loader reads it, so the loader that runs after these checks never meets one it
    cannot read: its constructor raises a plain ``ValueError``, ``IndexError`` (``!!int ""``), ``KeyError``
    (``!!bool abc``) or ``AttributeError`` (``!!timestamp abc``), past every reader's "invalid" path. A whole
    number that reads but has more digits than the interpreter writes back as text (a long hex number) cannot
    be stored either. The value is never echoed.
    """
    kind = _TYPED_SCALARS.get(str(node.tag))
    if kind is None:
        return None
    try:
        value = constructor.yaml_constructors[node.tag](constructor, node)
    except Exception:
        limit = sys.get_int_max_str_digits()
        if node.tag == _INT_TAG and limit and _decimal_digits(str(node.value)) > limit:
            return number_too_long(loc, key=key)
        type_, noun = kind
        return StructureProblem(
            loc, type_, f"A key is not {noun} YAML can read." if key else f"Not {noun} YAML can read."
        )
    if isinstance(value, int) and not isinstance(value, bool):
        try:
            str(value)
        except ValueError:
            return number_too_long(loc, key=key)
    return None


def structure_problems(root: yaml.Node | None, *, what: str = "a document") -> list[StructureProblem]:
    """What ``root`` uses that a document may not: aliases, merge keys, keys that are not text, keys written twice,
    and a number, a true or false or a date YAML cannot read (:func:`_scalar_problem`).

    ``root`` is the composed YAML (``yaml.compose``); ``what`` names the
    document in the alias message ("a settings document"). Problems come in
    document order, so an alias is reported where it is used.
    """
    problems: list[StructureProblem] = []
    seen: set[int] = set()
    constructor = yaml.constructor.SafeConstructor()
    queue: deque[tuple[yaml.Node, Loc]] = deque() if root is None else deque([(root, ())])
    while queue:
        node, loc = queue.popleft()
        if id(node) in seen:
            problems.append(StructureProblem(loc, "yaml_alias", f"Aliases (*name) are not supported in {what}."))
            continue
        seen.add(id(node))
        if isinstance(node, yaml.ScalarNode):
            scalar = _scalar_problem(node, constructor, loc)
            if scalar is not None:
                problems.append(scalar)
        elif isinstance(node, yaml.MappingNode):
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
                key_problem = _scalar_problem(key, constructor, loc, key=True)
                if key_problem is not None:
                    problems.append(key_problem)
                    continue
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


class _TooLong:
    """Stands, in what :func:`load_json` decodes, for a whole number it could not convert; never leaves it."""


_TOO_LONG: Final = _TooLong()


def _whole_number(text: str) -> int | _TooLong:
    try:
        return int(text)
    except ValueError:
        return _TOO_LONG


def load_json(text: str, **options: Any) -> tuple[Any, list[StructureProblem]]:
    """``text`` as the JSON of a document an operator writes, and the whole numbers in it too long to convert.

    A key written twice raises :class:`DuplicateKey`; text that is not JSON,
    or nested past the recursion limit, raises as ``json.loads`` does. A
    whole number with more digits than the interpreter converts
    (:func:`sys.get_int_max_str_digits`) does not raise a plain ``ValueError``
    from inside the decoder: each is reported by :func:`number_too_long` at
    its location, and the value holds ``None`` there. ``options`` go to
    ``json.loads`` (``parse_constant``, say).
    """
    value = json.loads(text, object_pairs_hook=refuse_duplicate_keys, parse_int=_whole_number, **options)
    problems: list[StructureProblem] = []
    stack: list[tuple[Any, Loc, Any, Any]] = [(value, (), None, None)]
    while stack:
        current, loc, parent, slot = stack.pop()
        if current is _TOO_LONG:
            problems.append(number_too_long(loc))
            if parent is None:
                value = None
            else:
                parent[slot] = None
        elif isinstance(current, dict):
            stack.extend((inner, (*loc, key), current, key) for key, inner in current.items())
        elif isinstance(current, list):
            stack.extend((inner, (*loc, index), current, index) for index, inner in enumerate(current))
    return value, sorted(problems, key=lambda p: [str(part) for part in p.loc])


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
