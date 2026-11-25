"""What a module spells (tests/fixtures/source_strings.py): the forms of a path and of an import the static tests of
#175 must see, each of which a plain text search or one form of import missed."""

from __future__ import annotations

import importlib
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
    "nothing of it": ("from embry0.services import areas_store\nimport embry0.services\n", []),
}


@pytest.mark.parametrize("form", sorted(_IMPORTS))
def test_an_import_is_seen_in_every_form(tmp_path: pathlib.Path, form: str) -> None:
    source, taken = _IMPORTS[form]
    package = tmp_path / "embry0" / ("api" if "sibling" in form else "services")
    package.mkdir(parents=True)
    module = package / "reader.py"
    module.write_text(source)
    assert _helper().imports_of(module, tmp_path, "embry0.services.forge_files") == taken, form
