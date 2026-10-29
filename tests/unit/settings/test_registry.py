"""A section is checked when it is declared, and the catalog is the one list of them (spec 2026-10-28)."""

from __future__ import annotations

import ast
import importlib
import pkgutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

import embry0.settings.sections as sections_package
from embry0.settings.registry import SCHEMA_VERSION, Catalog, Section, SectionError, SectionModel
from embry0.settings.sections import CATALOG
from tests.fixtures.settings_sections import OTHER, SAMPLE, TEST_CATALOG, SampleSettings


class _Good(SectionModel):
    limit: int = Field(default=1, ge=0, description="A limit.")


def _refusal(**kwargs: Any) -> str:
    """Why the section is refused, or a sentence saying it was not."""
    try:
        Section(**{"name": "good", "model": _Good, "summary": "A section.", **kwargs})
    except SectionError as exc:
        return str(exc)
    return "it registered without a refusal"


def test_the_test_section_registers_and_keeps_its_declarations() -> None:
    assert SAMPLE.fields == ("enabled", "per_tick", "remind_hours", "mode", "labels", "zone")
    assert SAMPLE.org_keys == frozenset({"per_tick", "remind_hours"}), "org_keys are kept as declared"
    assert SAMPLE.imports()["per_tick"].attr == "proposal_briefs_per_tick", "a plain string is the config attribute"
    assert SAMPLE.defaults() == SampleSettings(), "defaults() is the model with nothing written"


def test_a_field_without_a_default_is_refused() -> None:
    class NoDefault(SectionModel):
        limit: int = Field(description="A limit.")

    assert "field 'limit' needs a default" in _refusal(model=NoDefault)


def test_a_field_without_a_description_is_refused() -> None:
    class Undocumented(SectionModel):
        limit: int = 1

    assert "field 'limit' needs a description" in _refusal(model=Undocumented)


def test_a_field_that_looks_secret_is_refused() -> None:
    class Secretive(SectionModel):
        api_token: str = Field(default="", description="A token.")

    assert "field 'api_token' looks like a secret" in _refusal(model=Secretive)


@pytest.mark.parametrize("label", ["org_keys", "repo_keys", "env"])
def test_a_declaration_that_names_no_field_is_refused(label: str) -> None:
    value: Any = {"limt": "leads_enabled"} if label == "env" else frozenset({"limt"})
    assert f"{label} names no field of the model: ['limt']" in _refusal(**{label: value})


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("version", "'version' is a reserved name"),
        ("managed_by", "'managed_by' is a reserved name"),
        ("Bad-Name", "lowercase letters, digits"),
        # FastAPI's error parser drops a first loc segment with these names.
        ("body", "'body' is a reserved name"),
        ("query", "'query' is a reserved name"),
        # YAML 1.1 reads these words as true, false or null, not as a name.
        ("on", "'on' is a reserved name"),
        ("null", "'null' is a reserved name"),
        ("api_tokens", "its name looks like a secret"),
    ],
)
def test_a_reserved_or_malformed_name_is_refused(name: str, expected: str) -> None:
    assert expected in _refusal(name=name)


def test_a_field_named_with_a_yaml_word_is_refused() -> None:
    class Wordy(SectionModel):
        on: bool = Field(default=False, description="Whether it is on.")

    assert "field 'on' is a word YAML reads as true, false or null" in _refusal(model=Wordy)


def test_a_secret_looking_field_of_a_nested_model_is_refused() -> None:
    class Login(SectionModel):
        password: str = Field(default="", description="A password.")

    class Nested(SectionModel):
        login: Login = Field(default_factory=Login, description="A login.")

    assert "field 'password' looks like a secret" in _refusal(model=Nested)


def test_a_nested_model_that_is_not_a_section_model_is_refused() -> None:
    class Loose(BaseModel):
        limit: int = 1

    class Nested(SectionModel):
        loose: Loose = Field(default_factory=Loose, description="A loose model.")

    assert "the model of field 'loose' must be a SectionModel" in _refusal(model=Nested)


def test_a_field_that_turns_strict_off_is_refused() -> None:
    class Lenient(SectionModel):
        limit: int = Field(default=1, strict=False, description="A limit.")

    assert "field 'limit' turns strict off" in _refusal(model=Lenient)


def test_a_field_with_an_alias_is_refused() -> None:
    """A stored key is the field's own name; an aliased field would read as a key the section does not have."""

    class Aliased(SectionModel):
        limit: int = Field(default=1, alias="max", description="A limit.")

    assert "field 'limit' has an alias" in _refusal(model=Aliased)


def test_a_default_outside_its_own_bounds_is_refused() -> None:
    class OutOfBounds(SectionModel):
        limit: int = Field(default=0, ge=1, description="A limit.")

    assert "its defaults are refused by its own checks" in _refusal(model=OutOfBounds)


def test_a_model_that_is_not_a_section_model_is_refused() -> None:
    class Plain(BaseModel):
        limit: int = Field(default=1, description="A limit.")

    assert "its model must be a SectionModel" in _refusal(model=Plain)


def test_a_model_that_loosens_extra_or_strict_is_refused() -> None:
    class Loose(SectionModel):
        model_config = ConfigDict(extra="ignore")
        limit: int = Field(default=1, description="A limit.")

    assert "must keep SectionModel's extra='forbid' and strict=True" in _refusal(model=Loose)


def test_a_catalog_refuses_a_section_twice() -> None:
    with pytest.raises(SectionError, match="registered twice"):
        Catalog((SAMPLE, OTHER, SAMPLE))


def test_the_schema_holds_every_section_the_version_and_what_may_override() -> None:
    schema = TEST_CATALOG.json_schema()
    properties = schema["properties"]
    assert set(properties) == {"version", "managed_by", "sample", "other"}
    assert properties["managed_by"]["enum"] == ["file", None], (
        "an editor that validates against the schema must accept managed_by: file and nothing else"
    )
    assert properties["version"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": SCHEMA_VERSION,
        "description": properties["version"]["description"],
    }
    assert schema["additionalProperties"] is False, "an unknown section is not a valid document"
    assert schema["x-embry0-sections"]["sample"] == {
        "summary": "A section that exists only in tests.",
        "org_keys": ["per_tick", "remind_hours"],
        "repo_keys": ["mode"],
    }
    sample = schema["$defs"]["SampleSettings"]["properties"]["per_tick"]
    assert (sample["minimum"], sample["maximum"], sample["default"]) == (1, 20, 3)


# ---- the one list --------------------------------------------------------------


def _section_modules() -> list[ModuleType]:
    path = [str(Path(sections_package.__file__).parent)]
    return [importlib.import_module(f"{sections_package.__name__}.{m.name}") for m in pkgutil.iter_modules(path)]


def missing_from(catalog: Catalog, modules: list[ModuleType]) -> list[str]:
    """Sections a module declares that the catalog does not hold."""
    listed = {id(section) for section in catalog}
    return [
        f"{module.__name__}.{name}"
        for module in modules
        for name, value in vars(module).items()
        if isinstance(value, Section) and id(value) not in listed
    ]


def test_the_check_for_a_section_left_out_of_the_catalog_can_fail() -> None:
    module = ModuleType("embry0.settings.sections.forgotten")
    module.SECTION = OTHER  # type: ignore[attr-defined]
    assert missing_from(Catalog((SAMPLE,)), [module]) == ["embry0.settings.sections.forgotten.SECTION"]


def test_every_section_module_is_in_the_catalog() -> None:
    missing = missing_from(CATALOG, _section_modules())
    assert missing == [], f"add these to CATALOG in embry0/settings/sections/__init__.py: {missing}"


def unsorted(catalog: Catalog) -> list[str]:
    """The catalog's section names in the order they should have, or ``[]`` when they have it."""
    names = list(catalog.names)
    return [] if names == sorted(names) else sorted(names)


def crowded(source: str) -> list[str]:
    """Why the list handed to ``Catalog`` for ``CATALOG`` in ``source`` is not one section name per line.

    Two changes that each add a section then add a line each, instead of
    both editing the one line that holds the list.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if not (isinstance(target, ast.Name) and target.id == "CATALOG"):
            continue
        if (
            not isinstance(value, ast.Call)
            or len(value.args) != 1
            or not isinstance(value.args[0], ast.List | ast.Tuple)
        ):
            return ["CATALOG is not Catalog([...]) with its sections written out"]
        listed = value.args[0]
        lines = [listed.lineno, listed.end_lineno] + [entry.lineno for entry in listed.elts]
        return [
            f"line {entry.lineno}: {ast.unparse(entry)} is not a section name on a line of its own"
            for entry in listed.elts
            if not isinstance(entry, ast.Name) or lines.count(entry.lineno) > 1
        ]
    return ["no CATALOG = Catalog([...])"]


def test_the_checks_for_the_catalog_order_and_layout_can_fail() -> None:
    assert unsorted(Catalog((SAMPLE, OTHER))) == ["other", "sample"]
    assert unsorted(Catalog((OTHER, SAMPLE))) == []
    assert crowded("CATALOG: Catalog = Catalog((ASKS,))\n") == [
        "line 1: ASKS is not a section name on a line of its own"
    ], "an entry on the line of a bracket counts"
    assert crowded("CATALOG = Catalog(\n    [\n        ASKS, OTHER,\n        SAMPLE,\n    ]\n)\n") == [
        "line 3: ASKS is not a section name on a line of its own",
        "line 3: OTHER is not a section name on a line of its own",
    ]
    assert crowded("CATALOG = Catalog(\n    [\n        sections.ASKS,\n    ]\n)\n") == [
        "line 3: sections.ASKS is not a section name on a line of its own"
    ]
    assert crowded("CATALOG = Catalog(SECTIONS)\n") == ["CATALOG is not Catalog([...]) with its sections written out"]
    assert crowded("CATALOGUE = Catalog([])\n") == ["no CATALOG = Catalog([...])"]
    assert crowded("CATALOG: Catalog = Catalog(\n    [\n        ASKS,\n        OTHER,\n    ]\n)\n") == []


def test_the_catalog_lists_one_section_per_line_sorted_by_name() -> None:
    source = Path(sections_package.__file__).read_text(encoding="utf-8")
    assert crowded(source) == [], "write CATALOG in embry0/settings/sections/__init__.py one section per line"
    assert unsorted(CATALOG) == [], "sort CATALOG in embry0/settings/sections/__init__.py by section name"


_ALLOWED_IMPORTS = frozenset({"__future__", "pydantic", "embry0.settings.registry"})
#: The package itself: ``CATALOG`` imports each section module, and those are held to the same rule.
_SECTIONS = "embry0.settings.sections"


def _imported_names(node: ast.AST) -> list[str]:
    """The modules an import statement names, a relative one resolved inside the sections package."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    if node.level == 0:
        return [node.module or ""]
    if node.level > 1:
        return ["." * node.level + (node.module or "")]
    if node.module:
        return [f"{_SECTIONS}.{node.module}"]
    return [f"{_SECTIONS}.{alias.name}" for alias in node.names]


def impure_imports(source: str) -> list[str]:
    """Imports a section module may not have: anything but the registry, pydantic, the
    standard library and a sibling section module."""
    out: list[str] = []
    for node in ast.walk(ast.parse(source)):
        for name in _imported_names(node):
            top = name.split(".")[0]
            allowed = (
                name in _ALLOWED_IMPORTS
                or name.startswith(("pydantic.", f"{_SECTIONS}."))
                or (top in sys.stdlib_module_names and top != "embry0")
            )
            if not allowed:
                out.append(name)
    return out


def test_the_check_for_an_impure_section_module_can_fail() -> None:
    source = (
        "from embry0.config import Embry0Config\nimport yaml\nfrom zoneinfo import ZoneInfo\n"
        "import pydantic_settings\nfrom pydantic.fields import FieldInfo\n"
        "from embry0.settings.sections.asks import ASKS\nfrom .other import OTHER\n"
        "from embry0.settings.store import SettingsStore\nfrom embry0.settings.sections import CATALOG\n"
    )
    assert impure_imports(source) == [
        "embry0.config",
        "yaml",
        "pydantic_settings",
        "embry0.settings.store",
        "embry0.settings.sections",
    ], "only pydantic and its submodules pass, not a package whose name starts with it; a sibling section passes"


def test_section_modules_import_only_the_registry_pydantic_and_the_standard_library() -> None:
    root = Path(sections_package.__file__).parent
    found = {
        path.name: impure
        for path in sorted(root.glob("*.py"))
        if (impure := impure_imports(path.read_text(encoding="utf-8")))
    }
    assert found == {}, f"a section module must not import these (import cycles): {found}"
