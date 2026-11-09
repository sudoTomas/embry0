"""Whose ``ui`` values a caller reads (ADR 0029, #177): the organizations its key's allowlist covers, else the
deployment's.

- A caller with no allowlist (every SSO session, the master key, a key
  without a list, AUTH_DEV_MODE) reads the deployment's values, whatever any
  organization's layer says.
- A key whose allowlist covers one organization reads that organization's
  layer over the deployment's document; an owner with no layer reads the
  deployment's.
- A key whose list covers several sees the game when any of them has it on,
  and opens on the page those with the game on agree on, else on the
  deployment's.
- An organization is found from each entry's current slug, as a
  repository's layer is: case and ``REPO_RENAMES`` do not hide it.

Each test reads through :func:`embry0.api.v1.ui.ui_settings`, the one reader
``GET /config/ui``, the game's routes and the Decisions page's words share,
over a real snapshot of the stored documents.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from embry0.api.deps import READS_NOTHING, Principal
from embry0.api.v1.ui import ui_settings
from embry0.settings.scope import DEPLOYMENT
from embry0.settings.sections import CATALOG
from embry0.settings.store import build_snapshot
from embry0.storage import repo_renames

#: A key restricted to one repository of acme, one of globex, both, and one of an owner with no layer.
ACME, GLOBEX, BOTH, ELSEWHERE = (
    ("acme/widgets",),
    ("globex/gizmos",),
    ("acme/widgets", "globex/gizmos"),
    ("initech/app",),
)


class Store:
    """The settings store, holding ``deployment``'s ui section and each organization's."""

    def __init__(self, deployment: dict[str, Any], **organizations: dict[str, Any]) -> None:
        rows = [{"scope": DEPLOYMENT, "data": {"ui": deployment}, "generation": 1, "updated_at": None}]
        rows += [{"scope": f"organization:{owner}", "data": {"ui": ui}} for owner, ui in organizations.items()]
        self._snapshot = build_snapshot(CATALOG, rows, token=(), log=False)

    async def snapshot(self) -> Any:
        return self._snapshot


async def _read(store: Store, principal: Principal | None) -> dict[str, Any]:
    """The ui section a caller authenticated as ``principal`` reads (None: AUTH_DEV_MODE, no principal)."""
    state = SimpleNamespace() if principal is None else SimpleNamespace(principal=principal)
    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=store)), state=state)
    return (await ui_settings(request)).model_dump(mode="json")


def _key(allowed_repos: tuple[str, ...] | None) -> Principal:
    return Principal(kind="key", tier="read", key_id="key-000000000001", allowed_repos=allowed_repos)


#: Every caller nothing restricts: they read the deployment's values.
UNRESTRICTED: dict[str, Principal | None] = {
    "an SSO session": Principal(kind="session", tier="admin", email="alex@example.com"),
    "the master key": Principal(kind="master", tier="admin"),
    "a key without a list": _key(None),
    "AUTH_DEV_MODE": None,
}


@pytest.mark.parametrize("caller", list(UNRESTRICTED))
async def test_a_caller_no_list_restricts_reads_the_deployment_s_values(caller: str) -> None:
    every_organization_off = Store({}, acme={"game": False}, globex={"game": False, "home": "overview"})
    assert await _read(every_organization_off, UNRESTRICTED[caller]) == {"game": True, "home": "guild"}, (
        f"{caller} names no organization, so it reads the deployment's values whatever an organization's layer says"
    )
    deployment_off = Store({"game": False}, acme={"game": True})
    assert (await _read(deployment_off, UNRESTRICTED[caller]))["game"] is False, (
        f"{caller}: the deployment's own switch stays a switch for everyone it does not restrict"
    )


async def test_a_key_over_one_organization_reads_its_layer_over_the_deployment_s() -> None:
    store = Store({"home": "overview"}, acme={"game": False})
    assert await _read(store, _key(ACME)) == {"game": False, "home": "overview"}, (
        "a key over acme's repositories reads acme's game: false, and the deployment's home it does not set"
    )
    assert await _read(store, _key(GLOBEX)) == {"game": True, "home": "overview"}, (
        "a key over globex, which set nothing, reads the deployment's values"
    )


async def test_an_organization_turns_the_game_on_for_its_people_while_the_deployment_has_it_off() -> None:
    store = Store({"game": False}, globex={"game": True, "home": "overview"})
    assert await _read(store, _key(GLOBEX)) == {"game": True, "home": "overview"}, (
        "globex turns the game on, on the Overview, for the keys over its repositories"
    )
    assert (await _read(store, _key(ACME)))["game"] is False, "acme inherits the deployment's game: false"


async def test_a_key_over_several_organizations_sees_the_game_when_any_has_it_on() -> None:
    one_off = Store({}, acme={"game": False})
    assert (await _read(one_off, _key(BOTH)))["game"] is True, "globex keeps the game on, so the key sees it"
    both_off = Store({}, acme={"game": False}, globex={"game": False})
    assert (await _read(both_off, _key(BOTH)))["game"] is False, "off for every organization it covers: off"


async def test_a_key_over_several_organizations_opens_on_the_page_those_with_the_game_on_agree_on() -> None:
    agree = Store({}, acme={"home": "overview"}, globex={"home": "overview"})
    assert (await _read(agree, _key(BOTH)))["home"] == "overview", "both open on the Overview: so does the key"
    differ = Store({}, acme={"home": "overview"})
    assert (await _read(differ, _key(BOTH)))["home"] == "guild", "acme's Overview, globex's guild: the deployment's"
    differ_deployment = Store({"home": "overview"}, globex={"home": "guild"})
    assert (await _read(differ_deployment, _key(BOTH)))["home"] == "overview", (
        "acme inherits the Overview and globex sets the guild: the deployment's home, the Overview"
    )
    only_playing = Store({}, acme={"game": False, "home": "guild"}, globex={"home": "overview"})
    assert await _read(only_playing, _key(BOTH)) == {"game": True, "home": "overview"}, (
        "acme has the game off, so only globex, which plays it, decides the page the game opens on"
    )


async def test_a_key_over_an_owner_with_no_layer_or_over_nothing_reads_the_deployment_s_values() -> None:
    store = Store({"game": False}, acme={"game": True})
    assert (await _read(store, _key(ELSEWHERE)))["game"] is False, "initech has no layer: the deployment's"
    covering_nothing = Store({}, acme={"game": False})
    assert (await _read(covering_nothing, _key(())))["game"] is True, (
        "a list naming no repository covers no organization: the deployment's game, on"
    )
    assert (await _read(covering_nothing, READS_NOTHING))["game"] is True, "nor does a stream token that reads nothing"


async def test_the_organization_is_found_as_a_repository_s_layer_is(monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store({}, acme={"game": False})
    assert (await _read(store, _key(("ACME/Widgets",))))["game"] is False, "a slug's case does not hide its owner"
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {"oldco/widgets": "acme/widgets"})
    assert (await _read(store, _key(("oldco/widgets",))))["game"] is False, (
        "a list naming a repository by its old slug reads the organization it belongs to now"
    )
