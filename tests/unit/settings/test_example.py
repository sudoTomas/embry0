"""The example settings document, docs/examples/settings.yaml, is the sections at their defaults (#162).

It is generated, so it can never drift from the registry: the committed
file must equal what the catalog renders. It passes ``check``, sets nothing
as it stands (every key is a comment), and each commented value is that
key's default, which is proven by rendering the same lines with every key
set and reading them back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from embry0.settings.document import check, load_yaml
from embry0.settings.example import COMMAND, EXAMPLE_PATH, main, render
from embry0.settings.registry import Catalog
from embry0.settings.sections import CATALOG
from tests.fixtures.settings_sections import TEST_CATALOG

ROOT = Path(__file__).resolve().parents[3]

#: A line of a section's body that is not a comment: a key, or a line of a key's value.
_SET_LINE = re.compile(r"^(\s+)(?!#)(\S)")


def _comment_out(text: str) -> str:
    """``text`` with every indented line that is not a comment written as one, as the example writes keys."""
    return "\n".join(_SET_LINE.sub(r"\1# \2", line) for line in text.split("\n"))


def test_the_committed_example_is_what_the_sections_render() -> None:
    committed = (ROOT / EXAMPLE_PATH).read_text(encoding="utf-8")
    assert committed == render(CATALOG), f"{EXAMPLE_PATH} is out of date; run {COMMAND}"


def test_the_check_fails_when_the_example_is_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The comparison above passes only if it can fail."""
    stale = tmp_path / "settings.yaml"
    stale.write_text(render(Catalog(())), encoding="utf-8")
    monkeypatch.setattr("embry0.settings.sections.CATALOG", TEST_CATALOG)
    assert main([str(stale), "--check"]) == 1
    assert main([str(stale), "--write"]) == 0
    assert main([str(stale), "--check"]) == 0
    assert main([str(tmp_path / "missing.yaml"), "--check"]) == 1


@pytest.mark.parametrize("catalog", [CATALOG, TEST_CATALOG], ids=["application", "tests"])
def test_the_example_passes_check_and_sets_nothing(catalog: Catalog) -> None:
    checked = check(catalog, load_yaml(render(catalog)), stored={})
    assert checked.data == {}, "every key is a comment: applying the example as it is must set nothing"
    assert checked.unknown_sections == ()


@pytest.mark.parametrize("catalog", [CATALOG, TEST_CATALOG], ids=["application", "tests"])
def test_each_commented_value_is_the_default_of_its_key(catalog: Catalog) -> None:
    every_key_set = render(catalog, commented=False)
    checked = check(catalog, load_yaml(every_key_set), stored={})
    for section in catalog:
        defaults = section.defaults().model_dump(mode="json")
        assert checked.data.get(section.name) == defaults, f"{section.name}: the example's values are not its defaults"
    assert render(catalog) == _comment_out(every_key_set), (
        "the example must be the document with every key set, each key line commented out and nothing else changed"
    )


def test_each_key_is_described_above_it() -> None:
    text = render(TEST_CATALOG)
    for section in TEST_CATALOG:
        for key in section.fields:
            description = section.model.model_fields[key].description or ""
            above = text.split(f"\n  # {key}:", 1)[0].rsplit("\n\n", 1)[-1]
            words = " ".join(line.strip().removeprefix("#").strip() for line in above.splitlines())
            assert description in words, f"{section.name}.{key} is not described above its line"


def test_uncommenting_managed_by_keeps_the_document_in_a_file() -> None:
    text = render(TEST_CATALOG).replace("# managed_by: file", "managed_by: file")
    assert check(TEST_CATALOG, load_yaml(text), stored={}).data == {"managed_by": "file"}
