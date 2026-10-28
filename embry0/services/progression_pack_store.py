"""The stored progression content pack (#176): an operator's edits, with a draft, versions, undo and reset.

One pack per deployment. With nothing stored, or after a reset, the
deployment follows the shipped default (``progression_pack_default.yaml``),
so a release that changes it reaches every deployment nobody customised. A
customised pack is a whole document, stored as written, comments kept,
beside its parsed values.

The lifecycle follows the settings document's conventions (#160), with a
stored draft added because a pack re-prices the whole history:

- **save a draft**: validate and store the one draft; nothing anyone sees changes;
- **activate**: the draft becomes the active version, the old one superseded;
- **put**: validate and activate in one call (the CLI and MCP); an unchanged text records nothing;
- **restore**: a superseded version's document becomes a new active version (undo);
- **reset**: a new active version that follows the shipped default.

Every activation is a version (``progression_packs``); each records where
it came from (``person``, ``file``, ``restore``, ``reset``, ``upgrade``),
who and why. ``If-Match`` names the version a write expects to replace.

**Reading.** :class:`PackStore` is the progression service's
:class:`~embry0.services.progression_pack.PackSource`. It asks the database
for the active version's number on every read and parses the stored
document only when that number moved. A stored pack the running code
refuses (written by an older or newer release) is never served: the shipped
default is, with a warning logged once and the problems reported by
``GET /progression/pack``. A database error keeps the last pack read;
with none read yet (just after a boot), it fails the read like any other
read of the database, rather than serve the shipped default to a
deployment that customised its pack.

**Drafts and the pack in effect.** Every row records the active version
it was written over. A draft saved over version 3 is not activated over
version 4 (:class:`DraftStale`, a 409): whoever activated version 4 would
lose their change unseen. The draft is previewed and saved again first.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Final

import structlog
import yaml

from embry0.services.progression_pack import (
    MAX_TEXT_BYTES,
    SCHEMA_VERSION,
    Pack,
    PackInvalid,
    Problem,
    default_pack,
    default_text,
    load_text,
    pack_hash,
    parse,
    warnings,
)
from embry0.storage.repositories.progression_packs import (
    PackSource as RevisionSource,
)
from embry0.storage.repositories.progression_packs import (
    ProgressionPacksRepository,
    StoredPack,
)
from embry0.text_documents import DuplicateKey, refuse_duplicate_keys
from embry0.text_documents import text_diff as shared_text_diff

logger = structlog.get_logger(__name__)

#: A schema upgrade step: the document written in version ``n`` as version ``n + 1``.
Upgrade = Callable[[dict[str, Any]], dict[str, Any]]

#: The upgrade chain, by the version a step reads. Empty while the schema is at version 1.
UPGRADES: Final[dict[int, Upgrade]] = {}

#: The first line of a text the startup upgrade wrote again from the stored values.
UPGRADED_HEADER: Final = (
    "# Written again from the stored values when this pack was upgraded to schema version {version};\n"
    "# the comments of the earlier text were lost.\n"
)


# ---- errors ----


class PackTooLarge(ValueError):
    """The body is longer than the pack's limit (256 KiB)."""


class VersionMismatch(Exception):
    """``If-Match`` names no version that is the current one."""

    def __init__(self, what: str, current: int) -> None:
        super().__init__(f"the {what} is at version {current}")
        self.what = what
        self.current = current


class DraftStale(Exception):
    """The pack in effect changed since the draft was saved: activating it would undo that change unseen."""

    def __init__(self, parent: int, current: int) -> None:
        super().__init__(f"the draft was saved over version {parent}; version {current} is in effect now")
        self.parent = parent
        self.current = current


class NoDraft(LookupError):
    """There is no draft to read, preview or activate."""


class VersionNotFound(LookupError):
    """No active or superseded version has that number."""


# ---- the shipped default ----


@dataclass(frozen=True)
class Shipped:
    """The shipped default as this release has it: its text, its pack and its hash."""

    text: str
    pack: Pack
    hash: str


@cache
def shipped_default() -> Shipped:
    pack = default_pack()
    return Shipped(default_text(), pack, pack_hash(pack))


# ---- a body ----


@dataclass(frozen=True)
class Submitted:
    """A pack a write sent: the text that is stored, its values, the pack they are, and its hash."""

    text: str
    document: dict[str, Any]
    pack: Pack
    hash: str


def dump_yaml(data: Mapping[str, Any]) -> str:
    """Values as block YAML, in their own order."""
    return yaml.safe_dump(dict(data), sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)


def _storable(data: dict[str, Any]) -> dict[str, Any]:
    """``data`` as JSON can hold it, which is what Postgres stores; refused when it cannot."""
    try:
        encoded = json.dumps(data, allow_nan=False)
    except (TypeError, ValueError):
        raise PackInvalid([Problem((), "not_json", "the document holds a value JSON cannot hold")]) from None
    if "\\u0000" in encoded:
        raise PackInvalid([Problem((), "nul_character", "the document cannot hold the NUL character")])
    loaded: dict[str, Any] = json.loads(encoded)
    return loaded


def read_text(text: str) -> Submitted:
    """A pack written as YAML (or JSON) text, checked in full; raises :class:`PackInvalid` naming every problem."""
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise PackTooLarge(f"the pack is larger than {MAX_TEXT_BYTES // 1024} KiB")
    if "\x00" in text:
        raise PackInvalid([Problem((), "nul_character", "the document cannot hold the NUL character")])
    data = load_text(text)
    pack = parse(data)
    return Submitted(text, _storable(data), pack, pack_hash(pack))


def read_body(body: bytes, content_type: str) -> Submitted:
    """A request body as a pack. YAML (any type but JSON) is stored as written; a JSON object as the YAML it dumps to."""
    if len(body) > MAX_TEXT_BYTES:
        raise PackTooLarge(f"the pack is larger than {MAX_TEXT_BYTES // 1024} KiB")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise PackInvalid([Problem((), "encoding", "the document must be UTF-8 text")]) from None
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        return read_text(text)
    try:
        data = json.loads(text, object_pairs_hook=refuse_duplicate_keys)
    except DuplicateKey as exc:
        raise PackInvalid([Problem((), "duplicate_key", str(exc))]) from None
    except json.JSONDecodeError as exc:
        raise PackInvalid(
            [Problem((), "json_syntax", f"the JSON cannot be read: line {exc.lineno}, column {exc.colno}")]
        ) from None
    except RecursionError:
        raise PackInvalid([Problem((), "too_deep", "the document is nested too deeply")]) from None
    if not isinstance(data, dict):
        raise PackInvalid([Problem((), "dict_type", "a pack is a mapping of sections")])
    pack = parse(data)
    stored = _storable(data)
    return Submitted(dump_yaml(stored), stored, pack, pack_hash(pack))


# ---- comparing ----


def text_diff(before: str, after: str, *, before_name: str = "active", after_name: str = "proposed") -> str:
    """A unified diff of two texts as git writes it (the settings document's format); empty when they are the same."""
    return shared_text_diff(before, after, before_name=before_name, after_name=after_name)


def changed_sections(before: Pack, after: Pack) -> list[str]:
    """The top-level sections whose values differ, in the schema's order."""
    old, new = before.model_dump(mode="json"), after.model_dump(mode="json")
    return [name for name in Pack.model_fields if old.get(name) != new.get(name)]


def pack_warnings(pack: Pack, agent_types: Collection[str] | None = None) -> list[str]:
    """What a valid pack leaves out without being wrong.

    The categories no skill is trained by, and, when ``agent_types`` is
    given, each agent a rate or a quest reward names that is not defined
    now: its runs earn nothing until it is.
    """
    out = warnings(pack)
    if agent_types is not None:
        known = set(agent_types)
        named = [run.agent for run in pack.rates.runs] + [r.agent for q in pack.quests for r in q.xp]
        out += [f"no agent {agent!r} is defined, so its rate earns nothing yet" for agent in sorted(set(named) - known)]
    return out


# ---- the store ----


@dataclass(frozen=True)
class Effective:
    """The pack in effect, and the stored version it comes from (None when nothing is stored)."""

    row: StoredPack | None
    pack: Pack
    #: Why the stored version is not in effect: its problems under this release's schema; None when it is.
    fallback: list[Problem] | None = None

    @property
    def version(self) -> int:
        return self.row.version if self.row is not None else 0

    @property
    def follows_default(self) -> bool:
        return self.row is None or self.row.follows_default


@dataclass(frozen=True)
class Activation:
    """What an activating write did: the version now active, the one it replaced, and whether anything changed."""

    changed: bool
    version: int
    previous: int | None
    source: RevisionSource | None
    hash: str
    follows_default: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "version": self.version,
            "previous_version": self.previous,
            "source": self.source,
            "hash": self.hash,
            "follows_default": self.follows_default,
            "warnings": list(self.warnings),
        }


def _read_stored(row: StoredPack) -> tuple[Pack | None, list[Problem] | None]:
    """A stored customised version as this release reads it: its pack, or why it cannot be read."""
    if row.schema_version > SCHEMA_VERSION:
        return None, [
            Problem(
                ("version",),
                "version",
                f"written in schema version {row.schema_version} by a newer release; this release reads "
                f"version {SCHEMA_VERSION}",
            )
        ]
    try:
        return parse(row.document), None
    except PackInvalid as exc:
        return None, exc.problems
    except Exception:  # noqa: BLE001 - a stored pack must never fail a read
        return None, [Problem((), "unreadable", "the stored pack cannot be read by this release")]


def _matches(expected: Collection[int] | None, current: int) -> bool:
    return expected is None or current in expected


class PackStore:
    """The deployment's content pack: the source every progression read asks, and every write's path."""

    def __init__(self, repository: ProgressionPacksRepository, *, shipped: Callable[[], Shipped] = shipped_default):
        self._repository = repository
        self._shipped = shipped
        self._cached: Effective | None = None
        self._cached_default: str | None = None
        self._warned: set[tuple[int, str]] = set()

    @property
    def repository(self) -> ProgressionPacksRepository:
        return self._repository

    def shipped(self) -> Shipped:
        return self._shipped()

    # ---- reading ----

    def resolve(self, row: StoredPack | None) -> Effective:
        """The pack a stored row puts in effect; the shipped default when it follows it or cannot be read."""
        shipped = self._shipped()
        if row is None or row.follows_default:
            return Effective(row, shipped.pack)
        pack, problems = _read_stored(row)
        if pack is not None:
            return Effective(row, pack)
        problems = problems or []
        key = (row.version, shipped.hash)
        if key not in self._warned:
            self._warned.add(key)
            logger.warning(
                "progression_pack_stored_invalid",
                version=row.version,
                schema_version=row.schema_version,
                problems=len(problems),
                keys=[p.key for p in problems[:5]],
                serving="shipped default",
            )
        return Effective(row, shipped.pack, problems)

    async def active(self) -> Pack:
        """The pack in effect now. One query when nothing changed."""
        return (await self.effective()).pack

    async def effective(self) -> Effective:
        """The pack in effect now and the version it comes from.

        When the database fails, the last pack read; with none read yet, the
        error is raised, as any other read of the database raises it.
        """
        shipped = self._shipped()
        try:
            version = await self._repository.active_version()
            cached = self._cached
            if cached is not None and cached.version == (version or 0) and self._cached_default == shipped.hash:
                return cached
            row = await self._repository.active() if version is not None else None
            return self._keep(self.resolve(row))
        except Exception:
            # With a pack read before, keep it; with none (just after a boot), fail like any other read of
            # the database: the shipped default would mis-price a deployment that customised its pack.
            if self._cached is None:
                raise
            logger.warning("progression_pack_read_failed", version=self._cached.version, exc_info=True)
            return self._cached

    def _keep(self, effective: Effective) -> Effective:
        self._cached = effective
        self._cached_default = self._shipped().hash
        return effective

    async def draft(self) -> StoredPack | None:
        return await self._repository.draft()

    async def draft_pack(self) -> tuple[StoredPack, Pack]:
        """The draft and the pack it is; raises :class:`NoDraft`, or :class:`PackInvalid` when this release refuses it."""
        row = await self._repository.draft()
        if row is None:
            raise NoDraft()
        if row.follows_default:
            return row, self._shipped().pack
        pack, problems = _read_stored(row)
        if pack is None:
            raise PackInvalid(problems or [])
        return row, pack

    def text_of(self, row: StoredPack | None) -> str:
        """A version's text: its own, or the shipped default's when it follows it."""
        if row is None or row.follows_default or row.text is None:
            return self._shipped().text
        return row.text

    # ---- writing ----

    async def check_timezone(self, pack: Pack, conn: Any = None) -> None:
        """Refuse (422 on ``timezone``) a pack whose timezone Postgres does not know: its facts query could not run."""
        if not await self._repository.timezone_known(pack.timezone, conn):
            raise PackInvalid(
                [
                    Problem(
                        ("timezone",),
                        "timezone",
                        "the database does not know this timezone; name one both know, such as UTC or Europe/Berlin",
                    )
                ]
            )

    def _based_on(self, submitted: Submitted, active: StoredPack | None, rebase: bool) -> str:
        """The shipped default a customised pack is taken from.

        The default itself, or one an operator says the text now takes in
        (``rebase``), is the running release's; otherwise it stays what the
        active customised pack was based on, and a pack written over the
        default is based on the default.
        """
        shipped = self._shipped()
        if rebase or submitted.hash == shipped.hash:
            return shipped.hash
        if active is not None and not active.follows_default and active.based_on:
            return active.based_on
        return shipped.hash

    async def save_draft(
        self,
        submitted: Submitted,
        *,
        actor: str,
        if_match: Collection[int] | None = None,
        rebase: bool = False,
        reason: str = "",
    ) -> tuple[StoredPack, int | None]:
        """Store ``submitted`` as the one draft; answers the draft and the version of the draft it replaced."""
        shipped = self._shipped()
        async with self._repository.writing() as conn:
            draft = await self._repository.draft_on(conn)
            if not _matches(if_match, draft.version if draft else 0):
                raise VersionMismatch("draft", draft.version if draft else 0)
            await self.check_timezone(submitted.pack, conn)
            active = await self._repository.active_on(conn)
            based_on = self._based_on(submitted, active, rebase)
            if based_on == shipped.hash:
                await self._repository.record_default(conn, shipped.hash, shipped.text)
            replaced = await self._repository.delete_draft(conn)
            row = await self._repository.insert(
                conn,
                parent_version=active.version if active else 0,
                status="draft",
                schema_version=SCHEMA_VERSION,
                follows_default=False,
                text=submitted.text,
                document=submitted.document,
                based_on=based_on,
                source="person",
                reason=reason,
                created_by=actor,
            )
        return row, replaced

    async def delete_draft(self, *, if_match: Collection[int] | None = None) -> int:
        """Discard the draft; answers its version. Raises :class:`NoDraft` or :class:`VersionMismatch`."""
        async with self._repository.writing() as conn:
            draft = await self._repository.draft_on(conn)
            if draft is None:
                raise NoDraft()
            if not _matches(if_match, draft.version):
                raise VersionMismatch("draft", draft.version)
            await self._repository.delete_draft(conn)
        return draft.version

    async def activate_draft(
        self, *, actor: str, if_match: Collection[int] | None = None, reason: str = ""
    ) -> Activation:
        """The draft goes live as a new active version, and the draft is used up.

        ``If-Match`` names the draft (the one a preview showed). The draft is
        refused (:class:`DraftStale`) when the pack in effect changed since
        it was saved, since activating it would undo that change unseen; it
        is checked again first, too, since this release may refuse what it
        stored. The new version takes the next number, so the active
        version is always the newest.
        """
        async with self._repository.writing() as conn:
            draft = await self._repository.draft_on(conn)
            if draft is None:
                raise NoDraft()
            if not _matches(if_match, draft.version):
                raise VersionMismatch("draft", draft.version)
            active = await self._repository.active_on(conn)
            current = active.version if active else 0
            if draft.parent_version != current:
                raise DraftStale(draft.parent_version, current)
            pack = self.resolve(draft)
            if pack.fallback is not None:
                raise PackInvalid(pack.fallback)
            await self.check_timezone(pack.pack, conn)
            previous = await self._repository.supersede_active(conn)
            row = await self._repository.insert(
                conn,
                status="active",
                schema_version=draft.schema_version,
                follows_default=draft.follows_default,
                text=draft.text,
                document=draft.document,
                based_on=draft.based_on,
                source=draft.source,
                reason=reason or draft.reason,
                created_by=actor,
                parent_version=current,
            )
            await self._repository.delete_draft(conn)
            await self._repository.trim(conn)
        self._keep(Effective(row, pack.pack))
        logger.info(
            "progression_pack_activated", version=row.version, previous=previous, source=row.source, actor=actor
        )
        return Activation(True, row.version, previous, row.source, pack_hash(pack.pack), False)

    async def put(
        self,
        submitted: Submitted,
        *,
        actor: str,
        source: RevisionSource = "person",
        reason: str = "",
        if_match: Collection[int] | None = None,
        rebase: bool = False,
    ) -> Activation:
        """Validate and activate in one call. A text equal to the one in effect records nothing."""
        shipped = self._shipped()
        async with self._repository.writing() as conn:
            active = await self._repository.active_on(conn)
            current = active.version if active else 0
            if not _matches(if_match, current):
                raise VersionMismatch("active pack", current)
            await self.check_timezone(submitted.pack, conn)
            based_on = self._based_on(submitted, active, rebase)
            following = active is None or active.follows_default
            same_base = following or (active is not None and active.based_on == based_on)
            if submitted.text == self.text_of(active) and same_base:
                return Activation(False, current, None, None, submitted.hash, following)
            if based_on == shipped.hash:
                await self._repository.record_default(conn, shipped.hash, shipped.text)
            previous = await self._repository.supersede_active(conn)
            row = await self._repository.insert(
                conn,
                parent_version=current,
                status="active",
                schema_version=SCHEMA_VERSION,
                follows_default=False,
                text=submitted.text,
                document=submitted.document,
                based_on=based_on,
                source=source,
                reason=reason,
                created_by=actor,
            )
            await self._repository.trim(conn)
        self._keep(Effective(row, submitted.pack))
        logger.info("progression_pack_activated", version=row.version, previous=previous, source=source, actor=actor)
        return Activation(True, row.version, previous, source, submitted.hash, False)

    async def preview_put(self, submitted: Submitted, *, if_match: Collection[int] | None = None) -> dict[str, Any]:
        """What :meth:`put` would do, storing nothing: whether it changes, the sections, and the text diff."""
        active = await self._repository.active()
        current = active.version if active else 0
        if not _matches(if_match, current):
            raise VersionMismatch("active pack", current)
        await self.check_timezone(submitted.pack)
        effective = self.resolve(active)
        before = self.text_of(active)
        return {
            "would_change": submitted.text != before,
            "version": current,
            "hash": submitted.hash,
            "sections": changed_sections(effective.pack, submitted.pack),
            "text_diff": text_diff(before, submitted.text),
        }

    async def restore(
        self, version: int, *, actor: str, if_match: Collection[int] | None = None, reason: str = ""
    ) -> Activation:
        """A superseded version becomes the active one again, as a new version (undo).

        Raises :class:`VersionNotFound`, :class:`VersionMismatch`, or
        :class:`PackInvalid` when this release refuses that version's pack.
        """
        async with self._repository.writing() as conn:
            target = await self._repository.get_on(conn, version)
            if target is None or target.status == "draft":
                raise VersionNotFound(version)
            active = await self._repository.active_on(conn)
            current = active.version if active else 0
            if not _matches(if_match, current):
                raise VersionMismatch("active pack", current)
            effective = self.resolve(target)
            if effective.fallback is not None:
                raise PackInvalid(
                    [Problem((), "version_invalid", "That earlier version is no longer valid."), *effective.fallback]
                )
            await self.check_timezone(effective.pack, conn)
            digest = pack_hash(effective.pack)
            if target.status == "active":
                return Activation(False, current, None, None, digest, target.follows_default)
            previous = await self._repository.supersede_active(conn)
            row = await self._repository.insert(
                conn,
                parent_version=current,
                status="active",
                schema_version=target.schema_version,
                follows_default=target.follows_default,
                text=target.text,
                document=target.document,
                based_on=target.based_on,
                source="restore",
                reason=reason or f"restore of version {version}",
                created_by=actor,
            )
            await self._repository.trim(conn)
        self._keep(self.resolve(row))
        logger.info("progression_pack_activated", version=row.version, previous=previous, source="restore", actor=actor)
        return Activation(True, row.version, previous, "restore", digest, row.follows_default)

    async def reset(self, *, actor: str, if_match: Collection[int] | None = None, reason: str = "") -> Activation:
        """Follow the shipped default from now on, as a new version; nothing changes when it already does."""
        shipped = self._shipped()
        async with self._repository.writing() as conn:
            active = await self._repository.active_on(conn)
            current = active.version if active else 0
            if not _matches(if_match, current):
                raise VersionMismatch("active pack", current)
            if active is None or active.follows_default:
                return Activation(False, current, None, None, shipped.hash, True)
            previous = await self._repository.supersede_active(conn)
            row = await self._repository.insert(
                conn,
                parent_version=current,
                status="active",
                schema_version=SCHEMA_VERSION,
                follows_default=True,
                text=None,
                document=None,
                based_on=None,
                source="reset",
                reason=reason,
                created_by=actor,
            )
            await self._repository.trim(conn)
        self._keep(Effective(row, shipped.pack))
        logger.info("progression_pack_reset", version=row.version, previous=previous, actor=actor)
        return Activation(True, row.version, previous, "reset", shipped.hash, True)

    # ---- the shipped default moving on ----

    async def default_moved(self, row: StoredPack | None = None) -> dict[str, Any]:
        """Whether the shipped default changed since the active customised pack was taken from it, and how.

        ``text_diff`` is the diff from the default it was based on to this
        release's; null when that default's text was not recorded.
        """
        shipped = self._shipped()
        if row is None:
            row = await self._repository.active()
        based_on = row.based_on if row is not None and not row.follows_default else None
        moved = based_on is not None and based_on != shipped.hash
        diff: str | None = None
        if moved and based_on is not None:
            old = await self._repository.default_text(based_on)
            diff = text_diff(old, shipped.text, before_name="based on", after_name="shipped now") if old else None
        return {"based_on": based_on, "default_hash": shipped.hash, "moved": moved, "text_diff": diff}


async def upgrade_stored_packs(
    repository: ProgressionPacksRepository, steps: Mapping[int, Upgrade] = UPGRADES
) -> list[int]:
    """At startup: the active pack and the draft, written in an older schema, upgraded to this release's.

    Each upgraded one is stored as a new version with source ``upgrade``;
    its text is written again from its values, so its comments are lost,
    and the version's reason says so. A pack that cannot be upgraded (no
    step, or the result is refused) is left as it is: reads fall back to the
    shipped default and say why. Answers the new versions.
    """
    upgraded: list[int] = []
    renumbered: dict[int, int] = {}
    async with repository.writing() as conn:
        for row in (await repository.active_on(conn), await repository.draft_on(conn)):
            if row is None or row.follows_default or row.document is None or row.schema_version >= SCHEMA_VERSION:
                continue
            data, version = dict(row.document), row.schema_version
            try:
                while version < SCHEMA_VERSION:
                    step = steps.get(version)
                    if step is None:
                        raise LookupError(f"no upgrade from schema version {version}")
                    data, version = step(data), version + 1
                data["version"] = SCHEMA_VERSION
                parse(data)
                data = _storable(data)
            except (LookupError, PackInvalid) as exc:
                logger.warning(
                    "progression_pack_upgrade_refused",
                    version=row.version,
                    status=row.status,
                    schema_version=row.schema_version,
                    reason=str(exc),
                )
                continue
            text = UPGRADED_HEADER.format(version=SCHEMA_VERSION) + dump_yaml(data)
            reason = (
                f"upgraded from schema version {row.schema_version} to {SCHEMA_VERSION}; the text was written "
                "again from the stored values, so its comments were lost"
            )
            active = await repository.active_on(conn)
            if row.status == "draft":
                # Saved over the active pack this pass just upgraded: over its upgraded version now.
                parent = renumbered.get(row.parent_version, row.parent_version)
            else:
                parent = active.version if active else 0
            if row.status == "active":
                await repository.supersede_active(conn)
            else:
                await repository.delete_draft(conn)
            new = await repository.insert(
                conn,
                parent_version=parent,
                status="active" if row.status == "active" else "draft",
                schema_version=SCHEMA_VERSION,
                follows_default=False,
                text=text,
                document=data,
                based_on=row.based_on,
                source="upgrade",
                reason=reason,
                created_by="embry0:upgrade",
            )
            upgraded.append(new.version)
            renumbered[row.version] = new.version
            logger.info("progression_pack_upgraded", previous=row.version, version=new.version, status=row.status)
        if upgraded:
            await repository.trim(conn)
    return upgraded


def validate_text(text: str) -> tuple[Pack | None, list[Problem]]:
    """The offline check (``embry0 progression-pack check``): the pack, or every problem, as a write meets them."""
    try:
        return read_text(text).pack, []
    except PackTooLarge as exc:
        return None, [Problem((), "too_large", str(exc))]
    except PackInvalid as exc:
        return None, exc.problems
