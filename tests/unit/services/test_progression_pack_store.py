"""The stored content pack (#176): draft, activate, put, restore, reset, If-Match, fallback and the startup upgrade.

Against a real Postgres: the versions are rows, and the one-active and
one-draft rules are the database's.
"""

from __future__ import annotations

from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.services.progression_pack import SCHEMA_VERSION, PackInvalid, default_text, pack_hash, parse_text
from embry0.services.progression_pack_store import (
    DraftStale,
    NoDraft,
    PackStore,
    Shipped,
    VersionMismatch,
    VersionNotFound,
    read_body,
    read_text,
    shipped_default,
    upgrade_stored_packs,
)
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.progression_packs import KEEP_SUPERSEDED, ProgressionPacksRepository

pytestmark = [pytest.mark.requires_postgres]

DEFAULT = default_text()
#: The default with its first tier renamed: a customised pack.
COPPER = DEFAULT.replace("{id: bronze, name: Bronze,", "{id: bronze, name: Copper,")
#: The default with its second tier renamed too.
COPPER_PEWTER = COPPER.replace("{id: silver, name: Silver,", "{id: silver, name: Pewter,")


def _tier(pack: Any) -> str:
    return str(pack.tiers[0].name)


@pytest.fixture
def repo(db_with_migrations: DatabasePool) -> ProgressionPacksRepository:
    return ProgressionPacksRepository(db_with_migrations)


@pytest.fixture
def store(repo: ProgressionPacksRepository) -> PackStore:
    return PackStore(repo)


async def _versions(repo: ProgressionPacksRepository) -> list[tuple[int, str, str, bool]]:
    return [(v.version, v.status, v.source, v.follows_default) for v in await repo.versions()]


# ---- the lifecycle ----


async def test_nothing_stored_follows_the_shipped_default(store: PackStore) -> None:
    effective = await store.effective()
    assert (effective.version, effective.follows_default, effective.fallback) == (0, True, None)
    assert pack_hash(await store.active()) == shipped_default().hash, "with no row the shipped default is in effect"


async def test_a_draft_changes_nothing_until_it_is_activated(
    store: PackStore, repo: ProgressionPacksRepository
) -> None:
    draft, replaced = await store.save_draft(read_text(COPPER), actor="ann")
    assert replaced is None
    assert _tier(await store.active()) == "Bronze", "saving a draft must not change the pack in effect"
    assert await _versions(repo) == [], "a draft is not a version of the active pack"

    result = await store.activate_draft(actor="bob", if_match={draft.version})
    assert (result.changed, result.previous, result.source) == (True, None, "person")
    assert _tier(await store.active()) == "Copper", "the activated draft is the pack in effect"
    assert await store.draft() is None, "activating uses the draft up"
    assert await _versions(repo) == [(result.version, "active", "person", False)]
    active = await repo.active()
    assert active is not None and (active.text, active.created_by) == (COPPER, "bob"), "who activated it is recorded"
    with pytest.raises(NoDraft):
        await store.activate_draft(actor="ann")


async def test_an_activated_draft_goes_live_as_the_newest_version(
    store: PackStore, repo: ProgressionPacksRepository
) -> None:
    """The active version is always the newest, so its ETag only rises (review of #211, finding 3)."""
    await store.put(read_text(COPPER), actor="ann")
    draft, _ = await store.save_draft(read_text(COPPER_PEWTER), actor="ann")
    later = await store.put(read_text(COPPER.replace("Copper", "Tin")), actor="bob")
    draft, _ = await store.save_draft(read_text(COPPER_PEWTER), actor="ann", if_match={draft.version})
    result = await store.activate_draft(actor="ann", if_match={draft.version})
    numbers = [v.version for v in await repo.versions()]
    assert result.version not in (draft.version, later.version) and result.version == max(numbers), (
        f"an activated draft must take the next number, not its draft number: {result.version} of {numbers}"
    )
    assert draft.version not in numbers, "the draft row is used up, not kept as a version"
    assert (await store.effective()).version == result.version


async def test_a_draft_saved_over_an_earlier_pack_is_not_activated_over_a_newer_one(
    store: PackStore, repo: ProgressionPacksRepository
) -> None:
    """Ann saves a draft over version 1; Bob applies a file (version 2); Ann's activation must not undo it unseen."""
    first = await store.put(read_text(COPPER), actor="ann")
    draft, _ = await store.save_draft(read_text(COPPER_PEWTER), actor="ann")
    assert draft.parent_version == first.version, "a draft records the version in effect when it was saved"
    bob = await store.put(read_text(COPPER.replace("Copper", "Tin")), actor="bob", source="file")
    with pytest.raises(DraftStale) as stale:
        await store.activate_draft(actor="ann", if_match={draft.version})
    assert (stale.value.parent, stale.value.current) == (first.version, bob.version)
    assert _tier(await store.active()) == "Tin", "Bob's activation must still be in effect"
    resaved, _ = await store.save_draft(read_text(COPPER_PEWTER), actor="ann", if_match={draft.version})
    assert resaved.parent_version == bob.version
    assert (await store.activate_draft(actor="ann", if_match={resaved.version})).changed, (
        "saved again over the pack in effect, it activates"
    )


async def test_saving_another_draft_replaces_it_under_a_new_version(store: PackStore) -> None:
    first, _ = await store.save_draft(read_text(COPPER), actor="ann")
    second, replaced = await store.save_draft(read_text(COPPER_PEWTER), actor="bob", if_match={first.version})
    assert replaced == first.version and second.version > first.version
    with pytest.raises(VersionMismatch) as stale:
        await store.save_draft(read_text(COPPER), actor="ann", if_match={first.version})
    assert stale.value.current == second.version, "an If-Match naming the replaced draft must not match the new one"
    with pytest.raises(VersionMismatch):
        await store.activate_draft(actor="ann", if_match={first.version})
    assert await store.delete_draft(if_match={second.version}) == second.version
    assert await store.draft() is None


async def test_put_activates_at_once_and_an_unchanged_text_records_nothing(
    store: PackStore, repo: ProgressionPacksRepository
) -> None:
    unchanged = await store.put(read_text(DEFAULT), actor="ann", source="file")
    assert (unchanged.changed, unchanged.version) == (False, 0), (
        "the default's own text, while following it, changes nothing"
    )
    first = await store.put(read_text(COPPER), actor="ann", source="file", reason="rename")
    assert (first.changed, first.source, first.previous) == (True, "file", None)
    again = await store.put(read_text(COPPER), actor="ann", source="file")
    assert (again.changed, again.version) == (False, first.version), "an unchanged file makes no version"
    second = await store.put(read_text(COPPER_PEWTER), actor="bob", if_match={first.version})
    assert second.previous == first.version
    assert await _versions(repo) == [
        (second.version, "active", "person", False),
        (first.version, "superseded", "file", False),
    ]
    stored = await repo.get(first.version)
    assert stored is not None and (stored.text, stored.reason, stored.created_by) == (COPPER, "rename", "ann")


async def test_a_stale_if_match_is_refused_and_changes_nothing(store: PackStore) -> None:
    first = await store.put(read_text(COPPER), actor="ann")
    await store.put(read_text(COPPER_PEWTER), actor="bob")
    for write in (
        store.put(read_text(DEFAULT), actor="ann", if_match={first.version}),
        store.reset(actor="ann", if_match={first.version}),
        store.restore(first.version, actor="ann", if_match={first.version}),
    ):
        with pytest.raises(VersionMismatch):
            await write
    assert _tier(await store.active()) == "Copper" and (await store.active()).tiers[1].name == "Pewter"


async def test_restore_is_undo_as_a_new_version(store: PackStore, repo: ProgressionPacksRepository) -> None:
    first = await store.put(read_text(COPPER), actor="ann")
    await store.put(read_text(COPPER_PEWTER), actor="bob")
    restored = await store.restore(first.version, actor="ann")
    assert (restored.changed, restored.source) == (True, "restore")
    assert restored.version > first.version, "a restore is recorded as a new version"
    assert (await store.active()).tiers[1].name == "Silver" and _tier(await store.active()) == "Copper"
    row = await repo.get(restored.version)
    assert row is not None and row.text == COPPER and row.reason == f"restore of version {first.version}"
    with pytest.raises(VersionNotFound):
        await store.restore(9999, actor="ann")


async def test_reset_follows_the_default_and_can_itself_be_undone(
    store: PackStore, repo: ProgressionPacksRepository
) -> None:
    custom = await store.put(read_text(COPPER), actor="ann")
    reset = await store.reset(actor="ann", reason="back to the default")
    assert (reset.changed, reset.source, reset.follows_default) == (True, "reset", True)
    assert _tier(await store.active()) == "Bronze"
    assert (await store.reset(actor="ann")).changed is False, "a reset while following the default records nothing"
    undone = await store.restore(custom.version, actor="ann")
    assert _tier(await store.active()) == "Copper" and undone.changed
    again = await store.restore(reset.version, actor="ann")
    assert again.follows_default and _tier(await store.active()) == "Bronze", "restoring a reset follows the default"
    assert [v[2] for v in await _versions(repo)] == ["restore", "restore", "reset", "person"]


async def test_versions_beyond_the_newest_hundred_superseded_are_trimmed(
    store: PackStore, repo: ProgressionPacksRepository
) -> None:
    texts = [COPPER.replace("name: Copper", f"name: Copper {i}") for i in range(KEEP_SUPERSEDED + 3)]
    results = [await store.put(read_text(text), actor="ann") for text in texts]
    versions = await repo.versions(limit=1000)
    assert len([v for v in versions if v.status == "superseded"]) == KEEP_SUPERSEDED
    assert versions[0].version == results[-1].version and versions[-1].version == results[2].version


# ---- following the default, and a stored pack this release refuses ----


async def test_a_changed_shipped_default_is_what_a_deployment_following_it_reads(
    repo: ProgressionPacksRepository,
) -> None:
    release = {"text": DEFAULT}

    def shipped() -> Shipped:
        pack = parse_text(release["text"])
        return Shipped(release["text"], pack, pack_hash(pack))

    store = PackStore(repo, shipped=shipped)
    assert _tier(await store.active()) == "Bronze"
    release["text"] = COPPER  # the next release ships another default
    assert _tier(await store.active()) == "Copper", "with no row, the new release's default is in effect at once"
    await store.put(read_text(COPPER_PEWTER), actor="ann")
    await store.reset(actor="ann")
    release["text"] = DEFAULT
    assert _tier(await store.active()) == "Bronze", "after a reset the deployment follows whatever default ships"


async def test_a_stored_pack_this_release_refuses_falls_back_to_the_default(
    store: PackStore, db_with_migrations: DatabasePool
) -> None:
    await db_with_migrations.execute(
        "INSERT INTO progression_packs (status, schema_version, text, document, source) "
        "VALUES ('active', 1, 'curve: {}\n', $1::jsonb, 'person')",
        {"curve": {}, "retired_section": True},
    )
    with capture_logs() as logs:
        effective = await store.effective()
        await store.active()
    assert pack_hash(effective.pack) == shipped_default().hash, "a refused stored pack must never be served"
    assert effective.fallback, "the refusal must say why"
    assert any(p.loc == ("retired_section",) for p in effective.fallback), effective.fallback
    warned = [entry for entry in logs if entry["event"] == "progression_pack_stored_invalid"]
    assert len(warned) == 1, "the fallback is logged once per version, not on every read"


async def test_a_pack_written_by_a_newer_release_falls_back_too(
    store: PackStore, db_with_migrations: DatabasePool
) -> None:
    await db_with_migrations.execute(
        "INSERT INTO progression_packs (status, schema_version, text, document, source) "
        "VALUES ('active', $1, 'x\n', $2::jsonb, 'person')",
        SCHEMA_VERSION + 1,
        read_text(COPPER).document,
    )
    effective = await store.effective()
    assert effective.fallback and effective.fallback[0].loc == ("version",)
    assert _tier(effective.pack) == "Bronze"


async def test_a_database_error_keeps_the_last_pack_read(store: PackStore, repo: ProgressionPacksRepository) -> None:
    await store.put(read_text(COPPER), actor="ann")
    assert _tier(await store.active()) == "Copper"

    async def broken() -> int | None:
        raise ConnectionError("the database went away")

    repo.active_version = broken  # type: ignore[method-assign]
    assert _tier(await store.active()) == "Copper", "a failed read keeps the last pack instead of failing the request"
    cold = PackStore(repo)
    with pytest.raises(ConnectionError):
        # The shipped default would mis-price a deployment that customised its pack: fail like any other read.
        await cold.active()


# ---- the shipped default moving on ----


async def test_the_dashboard_learns_when_the_default_moved_on_since_a_pack_was_taken_from_it(
    repo: ProgressionPacksRepository,
) -> None:
    release = {"text": DEFAULT}

    def shipped() -> Shipped:
        pack = parse_text(release["text"])
        return Shipped(release["text"], pack, pack_hash(pack))

    store = PackStore(repo, shipped=shipped)
    await store.put(read_text(COPPER_PEWTER), actor="ann")
    assert (await store.default_moved())["moved"] is False
    release["text"] = DEFAULT.replace("{id: gold, name: Gold,", "{id: gold, name: Golden,")
    moved = await store.default_moved()
    assert moved["moved"] is True and moved["based_on"] == pack_hash(parse_text(DEFAULT))
    assert "-  - {id: gold, name: Gold," in moved["text_diff"] and "+  - {id: gold, name: Golden," in moved["text_diff"]
    edited = await store.put(read_text(COPPER_PEWTER.replace("Pewter", "Tin")), actor="ann")
    assert (await store.default_moved())["moved"] is True, "an edit alone keeps the default it was taken from"
    await store.put(read_text(COPPER_PEWTER.replace("Pewter", "Tin")), actor="ann", rebase=True)
    assert (await store.default_moved())["moved"] is False, "a rebase records that the pack takes in today's default"
    assert edited.changed


# ---- the startup upgrade ----


async def test_a_stored_pack_of_an_older_schema_is_upgraded_at_startup(
    repo: ProgressionPacksRepository, db_with_migrations: DatabasePool
) -> None:
    old = dict(read_text(COPPER).document)
    old.pop("version", None)
    old["tiers_v0"] = old.pop("tiers")  # how a schema version 0 might have spelled it
    await db_with_migrations.execute(
        "INSERT INTO progression_packs (status, schema_version, text, document, source, based_on) "
        "VALUES ('active', 0, '# my comments\n', $1::jsonb, 'person', 'abc')",
        old,
    )

    def zero_to_one(document: dict[str, Any]) -> dict[str, Any]:
        out = dict(document)
        out["tiers"] = out.pop("tiers_v0")
        return out

    upgraded = await upgrade_stored_packs(repo, {0: zero_to_one})
    assert len(upgraded) == 1
    row = await repo.active()
    assert row is not None and (row.version, row.source, row.schema_version) == (upgraded[0], "upgrade", 1)
    assert row.based_on == "abc" and "comments" in row.reason and row.text is not None
    assert "# my comments" not in row.text and row.text.startswith("# Written again from the stored values")
    assert _tier(parse_text(row.text)) == "Copper", "the regenerated text is the upgraded pack"
    assert await upgrade_stored_packs(repo, {0: zero_to_one}) == [], "a second boot upgrades nothing"
    assert [(v.status, v.schema_version) for v in await repo.versions()] == [("active", 1), ("superseded", 0)]


async def test_a_pack_with_no_upgrade_step_is_left_alone(
    repo: ProgressionPacksRepository, db_with_migrations: DatabasePool
) -> None:
    await db_with_migrations.execute(
        "INSERT INTO progression_packs (status, schema_version, text, document, source) "
        "VALUES ('active', 0, 'x\n', '{\"x\": 1}'::jsonb, 'person')"
    )
    with capture_logs() as logs:
        assert await upgrade_stored_packs(repo, {}) == []
    assert any(entry["event"] == "progression_pack_upgrade_refused" for entry in logs)
    assert [v.schema_version for v in await repo.versions()] == [0]


# ---- bodies ----


def test_a_json_body_is_stored_as_the_yaml_it_dumps_to() -> None:
    import json

    document = read_text(COPPER).document
    submitted = read_body(json.dumps(document).encode(), "application/json; charset=utf-8")
    assert submitted.hash == pack_hash(parse_text(COPPER))
    assert submitted.text.startswith("version: 1\n") and _tier(parse_text(submitted.text)) == "Copper"


@pytest.mark.parametrize(
    ("body", "content_type", "kind"),
    [
        (b"\xff\xfe", "application/yaml", "encoding"),
        (b"[1, 2]", "application/json", "dict_type"),
        (b"{", "application/json", "json_syntax"),
        (b"[" * 200_000, "application/json", "too_deep"),
        (b'{"timezone": "UTC", "timezone": "Europe/Berlin"}', "application/json", "duplicate_key"),
        (DEFAULT.replace("name: Bronze", 'name: "Bro\\0nze"').encode(), "application/yaml", "nul_character"),
    ],
)
def test_a_body_that_is_not_a_pack_is_refused_by_kind(body: bytes, content_type: str, kind: str) -> None:
    with pytest.raises(PackInvalid) as refused:
        read_body(body, content_type)
    assert [p.type for p in refused.value.problems] == [kind], refused.value.problems


# ---- what Python accepts and the database does not ----


async def test_a_timezone_only_python_knows_is_refused_on_every_write(store: PackStore) -> None:
    """``localtime`` is a zone ``zoneinfo`` reads and Postgres does not: the facts query would fail on every read."""
    local = read_text(COPPER.replace("timezone: UTC", "timezone: localtime"))
    berlin = read_text(COPPER.replace("timezone: UTC", "timezone: Europe/Berlin"))
    for write in (
        store.put(local, actor="ann"),
        store.save_draft(local, actor="ann"),
        store.preview_put(local),
    ):
        with pytest.raises(PackInvalid) as refused:
            await write
        assert [(p.loc, p.type) for p in refused.value.problems] == [(("timezone",), "timezone")]
        assert "localtime" not in refused.value.problems[0].msg, "a refusal never echoes the value"
    assert (await store.put(berlin, actor="ann")).changed, "a zone both know is stored"
    assert (await store.active()).timezone == "Europe/Berlin"


async def test_a_draft_whose_timezone_the_database_no_longer_knows_is_not_activated(
    store: PackStore, db_with_migrations: DatabasePool
) -> None:
    draft, _ = await store.save_draft(read_text(COPPER), actor="ann")
    document = dict(draft.document or {})
    document["timezone"] = "localtime"
    await db_with_migrations.execute(
        "UPDATE progression_packs SET document = $1::jsonb WHERE version = $2", document, draft.version
    )
    with pytest.raises(PackInvalid) as refused:
        await store.activate_draft(actor="ann")
    assert refused.value.problems[0].loc == ("timezone",)
    assert (await store.effective()).version == 0, "nothing was activated"


@pytest.mark.parametrize(
    ("text", "loc", "kind"),
    [
        (DEFAULT + "\ntimezone: UTC\n", ("timezone",), "duplicate_key"),
        (
            DEFAULT.replace("  max_level: 99\n", "  max_level: 99\n  max_level: 50\n"),
            ("curve", "max_level"),
            "duplicate_key",
        ),
        (
            DEFAULT.replace("cloaks:\n", "cloaks:\n  <<: {quest: {base: '#000000', trim: '#000000'}}\n"),
            ("cloaks",),
            "yaml_merge",
        ),
    ],
)
def test_a_key_written_twice_or_a_merge_key_is_refused(text: str, loc: tuple[str, ...], kind: str) -> None:
    with pytest.raises(PackInvalid) as refused:
        read_text(text)
    assert [(p.loc, p.type) for p in refused.value.problems] == [(loc, kind)], refused.value.problems


def test_a_text_with_no_final_newline_diffs_as_git_writes_it() -> None:
    """The pack's diff is the settings document's: git's note after a last line with no newline, never a joined line."""
    from embry0.services.progression_pack_store import text_diff

    diff = text_diff("timezone: UTC\n", "timezone: Europe/Berlin")
    assert diff.splitlines() == [
        "--- active",
        "+++ proposed",
        "@@ -1 +1 @@",
        "-timezone: UTC",
        "+timezone: Europe/Berlin",
        "\\ No newline at end of file",
    ], diff
