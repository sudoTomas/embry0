"""Every section of the settings document, in one list.

Each section lives in a module of this package that imports only the
registry, pydantic and the standard library, and is named in ``CATALOG``
below. A test fails when a module here is missing from ``CATALOG``, so the
set of sections never depends on which feature modules happen to be
imported: validation, the reference docs and the API all read this list.
"""

from __future__ import annotations

from embry0.settings.registry import Catalog

CATALOG: Catalog = Catalog(())
