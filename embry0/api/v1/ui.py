"""The dashboard's settings in effect (the ``ui`` section, #177), and the gate on the game's routes.

- ``GET /config/ui``: ``{"game": true, "home": "guild"}``, the section's
  values as they stand now. The dashboard reads it before it draws its
  navigation and home page, so it never shows the game for a moment while
  the game is off. Any signed-in caller may read it: it holds no secret, and
  a key or a session that may read the settings document may read this.
- :func:`require_game`: a dependency of the game's own routers (``/world``
  and ``/progression``), which answer 404 while ``ui.game`` is false.

Both read the section on every request, from the settings store's snapshot
(one query that checks whether any document changed), so a change applies
with no restart. Reviewing a job (``/jobs/{job_id}/…``, and its deprecated
aliases under ``/world/quests/{job_id}/…``) and the content pack's routes are
not the game's reads and are never gated.

The game is refused only when the section says it is off. An app built
without its lifespan has no settings store, and a store that never managed
a first read cannot say: both read as the defaults, the game on, which is
what every deployment showed before the section.
"""

from __future__ import annotations

from typing import Any, Final

import structlog
from fastapi import APIRouter, HTTPException, Request

from embry0.settings.sections.ui import UI, UiSettings
from embry0.settings.store import SettingsStore

logger = structlog.get_logger(__name__)
router = APIRouter()

#: Why a route of the game answers 404 while the game is off.
GAME_OFF: Final = "The game layer is off: ui.game is false in the settings document."


async def ui_settings(request: Request) -> UiSettings:
    """The ``ui`` section as it stands now; its defaults when it cannot be read."""
    store: SettingsStore | None = getattr(request.app.state, "settings", None)
    if store is None:
        return UI.defaults()
    try:
        return (await store.snapshot()).get(UI)
    except Exception:  # noqa: BLE001 - the dashboard's shape never fails a request
        logger.warning("ui_settings_unread", exc_info=True)
        return UI.defaults()


@router.get("/config/ui")
async def read_ui(request: Request) -> dict[str, Any]:
    """The dashboard's settings in effect: whether the game layer shows, and the page it opens on.

    While ``game`` is false the dashboard opens on the Overview, whatever
    ``home`` says.
    """
    return (await ui_settings(request)).model_dump(mode="json")


async def require_game(request: Request) -> None:
    """Refuse a route of the game with 404 while ``ui.game`` is false."""
    if not (await ui_settings(request)).game:
        raise HTTPException(status_code=404, detail=GAME_OFF)
