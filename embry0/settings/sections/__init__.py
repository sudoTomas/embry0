"""Every section of the settings document, in one list.

Each section lives in a module of this package that imports only the
registry, pydantic and the standard library, and is named in ``CATALOG``
below. A test fails when a module here is missing from ``CATALOG``, so the
set of sections never depends on which feature modules happen to be
imported: validation, the reference docs and the API all read this list.

``CATALOG`` holds one section per line, sorted by section name, and a test
keeps it so: two changes that each add a section then add different lines
instead of editing the same one.
"""

from __future__ import annotations

from embry0.settings.registry import Catalog
from embry0.settings.sections.asks import ASKS
from embry0.settings.sections.scout import SCOUT

CATALOG: Catalog = Catalog(
    [
        ASKS,
        SCOUT,
    ]
)
