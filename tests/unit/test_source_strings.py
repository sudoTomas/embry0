"""What a module spells (tests/fixtures/source_strings.py): the forms of a path and of an import the static tests of
#175 must see, each of which a plain text search or one form of import missed."""

from __future__ import annotations

import importlib
import inspect
import pathlib
from typing import Any

import pytest


def _helper() -> Any:
    try:
        return importlib.import_module("tests.fixtures.source_strings")
    except ImportError:
        pytest.fail("the static tests have no way to read what a module spells")


#: Each way a module could spell the old areas file's path.
_PATHS = {
    "spelled": 'PATH = ".embry0/areas.yml"\n',
    "url-encoded, as GitLab's files API reads it": 'url = f"/projects/{pid}/repository/files/.embry0%2Fareas.yml/raw"\n',
    "url-encoded, pieced together": '_SLASH = "%2F"\nurl = ".embry0" + _SLASH + "areas.yml"\n',
    "pieced together": 'path = ".embry0/" + "are" + "as.yml"\n',
    "in constants": '_DIR = ".embry0"\n_FILE = "areas" + ".yaml"\npath = f"{_DIR}/{_FILE}"\n',
    "joined as a path": 'from pathlib import Path\npath = Path(".embry0") / "areas.yml"\n',
    "joined with os.path": 'import os\npath = os.path.join(".embry0", "areas.yml")\n',
    "joined with a separator": 'path = "/".join([".embry0", "areas.yml"])\n',
    "URL-quoted": 'from urllib.parse import quote\npath = quote(".embry0/" + "areas.yml", safe="")\n',
    "formatted": 'path = "%s/areas.yml" % ".embry0"\n',
    "formatted with format()": 'path = "{}/{}".format(".embry0", "areas.yml")\n',
    "class constants": 'class AreasFile:\n    FOLDER = ".embry0"\n    NAME = "areas"\n\n\npath = f"{AreasFile.FOLDER}/{AreasFile.NAME}.yml"\n',
    "an Enum member's value": (
        'from enum import Enum\n\n\nclass Kind(Enum):\n    AREAS = "areas"\n\n\npath = f".embry0/{Kind.AREAS.value}.yml"\n'
    ),
    "a class constant built from another": (
        'class Paths:\n    FOLDER = ".embry0"\n    AREAS = FOLDER + "/areas.yml"\n\n\npath = Paths.AREAS\n'
    ),
    "joined with os.sep": 'import os\npath = ".embry0" + os.sep + "areas.yml"\n',
}


@pytest.mark.parametrize("form", sorted(_PATHS))
def test_a_path_is_seen_in_every_form(form: str) -> None:
    assert _helper().spells(_PATHS[form], ".embry0/areas"), f"a path {form} is missed"


@pytest.mark.parametrize(
    "source",
    [
        'url = f"/repos/{repo}/con" + "tents/" + path\n',
        'url = "/repos/" + repo + "/contents/" + path\n',
        'BASE = "https://raw.githubusercontent.com"\nurl = f"{BASE}/{repo}/HEAD/{path}"\n',
    ],
)
def test_a_forge_read_is_seen_pieced_together(source: str) -> None:
    assert _helper().spells(source, "/contents", "raw.githubusercontent"), source


def test_what_a_module_does_not_spell_is_not_seen() -> None:
    assert not _helper().spells('x = "areas" + suffix\nname = f"{owner}/areas"\n', ".embry0/areas", "areas.y")


_IMPORTS = {
    "a name from it": ("from embry0.services.forge_files import read_contents\n", ["read_contents"]),
    "the module from its package": ("from embry0.services import forge_files\n", ["<module>"]),
    "the module": ("import embry0.services.forge_files as files\n", ["<module>"]),
    "a name, relatively": ("from .forge_files import read_contents\n", ["read_contents"]),
    "the module, relatively": ("from . import forge_files\n", ["<module>"]),
    "the module, from a sibling package": ("from ..services import forge_files\n", ["<module>"]),
    "by name": ('import importlib\nfiles = importlib.import_module("embry0.services.forge_files")\n', ["<by name>"]),
    "as an attribute of its package": (
        "import embry0.services as services\nservices.forge_files.read_contents(get, repo, path)\n",
        ["<.read_contents>", "<.forge_files>"],
    ),
    "a name passed on by another module": (
        "from embry0.api.v1.proposals import read_contents\n",
        ["read_contents (from embry0.api.v1.proposals)"],
    ),
    "nothing of it": ("from embry0.services import areas_store\nimport embry0.services\n", []),
}


@pytest.mark.parametrize("form", sorted(_IMPORTS))
def test_an_import_is_seen_in_every_form(tmp_path: pathlib.Path, form: str) -> None:
    source, taken = _IMPORTS[form]
    package = tmp_path / "embry0" / ("api" if "sibling" in form else "services")
    package.mkdir(parents=True)
    module = package / "reader.py"
    module.write_text(source)
    imports_of = _helper().imports_of
    if "names" not in inspect.signature(imports_of).parameters:
        pytest.fail("a name of the helper taken from a module that passes it on must be seen")
    assert imports_of(module, tmp_path, "embry0.services.forge_files", ("read_contents",)) == taken, form


def test_a_constant_imported_from_another_module_is_followed(tmp_path: pathlib.Path) -> None:
    """The path's pieces held as constants in another module of the checkout, imported by name, by module, or
    relatively."""
    helper = _helper()
    package = tmp_path / "embry0" / "services"
    package.mkdir(parents=True)
    (tmp_path / "embry0" / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "names.py").write_text('DIR = ".embry0"\nFILE = "are" + "as.yml"\n')
    tree_of = getattr(helper, "Tree", None)
    if tree_of is None:
        pytest.fail("a constant imported from another module of the checkout must be followed there")
    tree = tree_of(tmp_path)
    for source in (
        'from embry0.services.names import DIR, FILE\npath = f"{DIR}/{FILE}"\n',
        'from embry0.services import names\npath = names.DIR + "/" + names.FILE\n',
        'import embry0.services.names as n\npath = n.DIR + "/" + n.FILE\n',
        'from .names import DIR as D, FILE as F\npath = D + "/" + F\n',
    ):
        reader = package / "reader.py"
        reader.write_text(source)
        assert helper.spells(source, ".embry0/areas", tree=tree, path=reader), f"missed: {source!r}"


#: A path under the old configuration folder with a part the code makes when it runs: a helper that takes the file's
#: name, a name read from somewhere. No constant spells the file, so it is refused for its folder (#175).
_UNDER_THE_FOLDER = {
    "a helper function": 'def config_path(name):\n    return ".embry0/" + name + ".yml"\n\n\npath = config_path("areas")\n',
    "an f-string": 'def path_of(root, kind):\n    return f"{root}/.embry0/{kind}.yml"\n',
    "a path join": 'from pathlib import Path\n\n\ndef path_of(kind):\n    return Path(".embry0") / kind\n',
    "os.path.join": 'import os\n\n\ndef path_of(kind):\n    return os.path.join(".embry0", kind)\n',
}


@pytest.mark.parametrize("form", sorted(_UNDER_THE_FOLDER))
def test_a_path_under_the_folder_with_a_part_made_at_run_time_is_seen(form: str) -> None:
    helper = _helper()
    assert helper.spells(_UNDER_THE_FOLDER[form], ".embry0/" + helper.UNKNOWN), f"a path {form} is missed"


def test_a_class_and_everything_imported_with_a_star_are_followed(tmp_path: pathlib.Path) -> None:
    helper = _helper()
    package = tmp_path / "embry0" / "services"
    package.mkdir(parents=True)
    (tmp_path / "embry0" / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "names.py").write_text(
        'BASE = ".embry0"\n\n\nclass AreasFile:\n    FOLDER = BASE\n    NAME = "are" + "as"\n'
    )
    tree_of = getattr(helper, "Tree", None)
    if tree_of is None:
        pytest.fail("a constant imported from another module of the checkout must be followed there")
    tree = tree_of(tmp_path)
    for source in (
        'from embry0.services.names import AreasFile\npath = f"{AreasFile.FOLDER}/{AreasFile.NAME}.yml"\n',
        'from embry0.services import names\npath = f"{names.AreasFile.FOLDER}/{names.AreasFile.NAME}.yml"\n',
        'from embry0.services.names import *  # noqa: F403\npath = f"{BASE}/{AreasFile.NAME}.yml"\n',
    ):
        reader = package / "reader.py"
        reader.write_text(source)
        assert helper.spells(source, ".embry0/areas", tree=tree, path=reader), f"missed: {source!r}"
