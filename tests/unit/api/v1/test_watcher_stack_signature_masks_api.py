"""A stack's signature masks through ``PUT /watcher/stacks`` (#172).

Admin-supplied regular expressions run on every line of every tick, so a mask
that does not compile, matches nothing but the empty string, or comes in
thousands is refused at write with a 422 naming ``signature_masks`` and the
entry, before anything is stored.
"""

from collections.abc import Sequence
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.services.watcher_stacks import WatcherStack

MASK = {"pattern": r"\brec_[a-z0-9]{7}\b", "replacement": "<record>"}


class StoredStacks:
    """Keeps what the route hands it: the route alone decides which settings are kept."""

    def __init__(self) -> None:
        self.stacks: dict[str, WatcherStack] = {}

    async def list_all(self, *, enabled_only: bool = False, repos: Sequence[str] | None = None) -> list[WatcherStack]:
        return [s for s in self.stacks.values() if repos is None or s.repo.lower() in repos]

    async def get(self, repo: str) -> WatcherStack | None:
        return self.stacks.get(repo)

    async def upsert(self, stack: WatcherStack) -> WatcherStack:
        self.stacks[stack.repo] = stack
        return stack


@pytest.fixture
def app() -> Any:
    built = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    built.state.watcher_stacks_repo = StoredStacks()
    return built


def _client(app: Any) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    )


async def _put(app: Any, settings: dict[str, Any]) -> Any:
    async with _client(app) as client:
        return await client.put("/api/v1/watcher/stacks/acme/widgets", json={"logql": "{x=1}", "settings": settings})


async def test_masks_are_stored_and_named_among_the_settings(app: Any) -> None:
    resp = await _put(app, {"signature_masks": [MASK, {"pattern": r"tkt-\d{4}-[a-z]{2}"}]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ignored_settings"] == [], "signature_masks was dropped as a key the watcher does not know"
    stored = app.state.watcher_stacks_repo.stacks["acme/widgets"].settings
    assert stored == {"signature_masks": [MASK, {"pattern": r"tkt-\d{4}-[a-z]{2}", "replacement": "<id>"}]}

    async with _client(app) as client:
        listed = (await client.get("/api/v1/watcher/stacks")).json()
    assert "signature_masks" in listed["overridable_settings"], "the vocabulary a caller reads leaves masks out"


@pytest.mark.parametrize(
    ("masks", "named"),
    [
        ([{"pattern": "rec_[", "replacement": "<record>"}], "settings.signature_masks.0.pattern"),
        ([MASK, {"pattern": "(unclosed", "replacement": "<record>"}], "settings.signature_masks.1.pattern"),
        ([MASK] * 51, "settings.signature_masks: List should have at most 50 items"),
        ([{"pattern": "a" * 501}], "settings.signature_masks.0.pattern: String should have at most 500"),
        ([{"pattern": r"\d+", "replacement": "NUMBER"}], "settings.signature_masks.0.replacement"),
    ],
)
async def test_a_bad_mask_is_a_422_naming_the_key_and_the_entry(app: Any, masks: Any, named: str) -> None:
    resp = await _put(app, {"signature_masks": masks, "interval_seconds": 600})
    assert resp.status_code == 422, f"{resp.status_code}: {resp.text}"
    assert named in resp.json()["detail"], resp.json()["detail"]
    assert app.state.watcher_stacks_repo.stacks == {}, "a refused stack was stored"


async def test_a_mask_no_tick_could_match_in_linear_time_is_a_422_naming_the_entry(app: Any) -> None:
    resp = await _put(app, {"signature_masks": [MASK, {"pattern": r"rec_(?=\d)\w+", "replacement": "<record>"}]})
    assert resp.status_code == 422, (
        f"a mask no tick can match in linear time was stored: {resp.status_code} {resp.text}"
    )
    detail = resp.json()["detail"]
    assert "settings.signature_masks.1.pattern" in detail and "look-ahead and look-behind" in detail, detail
    assert app.state.watcher_stacks_repo.stacks == {}, "a refused stack was stored"


async def test_a_stacks_view_lists_the_stored_masks_its_ticks_leave_out(app: Any) -> None:
    """Masks stored before they were matched in linear time can hold one the engine refuses: the stack's view says
    which and why, so the editor that sends them back knows what to fix; every other stack lists none."""
    legacy = [MASK, {"pattern": r"(?<=record )rec_\w+", "replacement": "<record>"}]
    stacks = app.state.watcher_stacks_repo.stacks
    stacks["acme/legacy"] = WatcherStack(repo="acme/legacy", logql="{x=1}", settings={"signature_masks": legacy})
    stacks["acme/clean"] = WatcherStack(repo="acme/clean", logql="{x=1}", settings={"signature_masks": [MASK]})
    async with _client(app) as client:
        one = (await client.get("/api/v1/watcher/stacks/acme/legacy")).json()
        listed = {s["repo"]: s for s in (await client.get("/api/v1/watcher/stacks")).json()["stacks"]}
    refused = one["refused_signature_masks"]
    assert [(r["index"], r["pattern"]) for r in refused] == [(1, legacy[1]["pattern"])], one
    assert refused[0]["reason"].startswith("settings.signature_masks.1.pattern: "), refused[0]
    assert "look-ahead and look-behind" in refused[0]["reason"], refused[0]
    assert one["settings"] == {"signature_masks": legacy}, "the view changed what is stored"
    assert listed["acme/legacy"]["refused_signature_masks"] == refused
    assert listed["acme/clean"]["refused_signature_masks"] == []

    put = await _put(app, {"signature_masks": [MASK]})
    assert put.status_code == 200 and put.json()["refused_signature_masks"] == [], put.text
