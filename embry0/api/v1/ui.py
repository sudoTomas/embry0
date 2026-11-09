"""The dashboard's settings in effect (the ``ui`` section, #177), and the gate on the game's routes.

- ``GET /config/ui``: ``{"game": true, "home": "guild"}``, the section's
  values as they stand for the caller. The dashboard reads it before it
  draws its navigation and home page, so it never shows the game for a
  moment while the game is off. Any signed-in caller may read it: it holds no
  secret, and a key or a session that may read the settings document may
  read this.
- :func:`require_game`: a dependency of the game's own routers (``/world``
  and ``/progression``), which answer 404 while ``ui.game`` is false for the
  caller.

Whose values (ADR 0029, :func:`for_caller`): a caller whose API key has a
repository allowlist reads the values of the organizations its list covers,
each organization's layer over the deployment's document. It sees the game
when any of them has it on, and opens on the page those with the game on
agree on, else on the deployment's. Every other caller, every SSO session,
the master key and every key without a list, reads the deployment's values:
nothing it carries names an organization. The Decisions page's words, and
the finished-job cards the chat feeds hand out (``GET /asks/due`` and
``GET /asks/of``), read :func:`ui_settings` too, so the API and the
dashboard never disagree.

Each read takes the settings store's snapshot (one query that checks whether
any document changed), so a change applies on the next request with no
restart. Reviewing a job (``/jobs/{job_id}/…``, and its deprecated aliases
under ``/world/quests/{job_id}/…``) and the content pack's routes are not the
game's reads and are never gated.

The game is refused only when the section says it is off. An app built
without its lifespan has no settings store, and a store that never managed
a first read cannot say: both read as the defaults, the game on, which is
what every deployment showed before the section.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final

import structlog
from fastapi import APIRouter, HTTPException, Request

from embry0.settings.scope import organization_of
from embry0.settings.sections.ui import UI, UiSettings
from embry0.settings.store import SettingsStore, Snapshot

logger = structlog.get_logger(__name__)
router = APIRouter()

#: Why a route of the game answers 404 while the game is off.
GAME_OFF: Final = "The game layer is off: ui.game is false in the settings document."


def for_caller(snapshot: Snapshot, allowed_repos: Iterable[str] | None) -> UiSettings:
    """The ``ui`` section for a caller whose key's repository allowlist is ``allowed_repos``.

    ``None``, a caller no list restricts, reads the deployment's values. A
    list reads the values of each organization it covers (the owner of each
    entry's current slug, ``REPO_RENAMES`` applied, as a repository's layer
    is found), each over the deployment's: the game is on when it is on for
    any of them, and the home is the one the organizations with the game on
    agree on (all of them while it is off for all), else the deployment's. A
    list that names no repository covers no organization and reads the
    deployment's.
    """
    deployment = snapshot.get(UI)
    if allowed_repos is None:
        return deployment
    owners = {organization_of(entry) for entry in allowed_repos if entry.strip()}
    values = [snapshot.get(UI, owner=owner) for owner in sorted(owners)]
    if not values:
        return deployment
    playing = [value for value in values if value.game]
    homes = {value.home for value in playing or values}
    return UiSettings(game=bool(playing), home=homes.pop() if len(homes) == 1 else deployment.home)


async def ui_settings(request: Request) -> UiSettings:
    """The ``ui`` section as it stands now for the caller (:func:`for_caller`); its defaults when it cannot be
    read."""
    store: SettingsStore | None = getattr(request.app.state, "settings", None)
    if store is None:
        return UI.defaults()
    principal = getattr(request.state, "principal", None)
    try:
        return for_caller(await store.snapshot(), getattr(principal, "allowed_repos", None))
    except Exception:  # noqa: BLE001 - the dashboard's shape never fails a request
        logger.warning("ui_settings_unread", exc_info=True)
        return UI.defaults()


@router.get("/config/ui")
async def read_ui(request: Request) -> dict[str, Any]:
    """The dashboard's settings in effect for the caller: whether the game layer shows, and the page it opens on.

    A key with a repository allowlist reads the organizations it covers (the
    game when any of them has it on); every other caller, every SSO session
    among them, the deployment's. While ``game`` is false the dashboard opens
    on the Overview, whatever ``home`` says.
    """
    return (await ui_settings(request)).model_dump(mode="json")


async def require_game(request: Request) -> None:
    """Refuse a route of the game with 404 while ``ui.game`` is false for the caller."""
    if not (await ui_settings(request)).game:
        raise HTTPException(status_code=404, detail=GAME_OFF)
