"""A value deployments relied on is kept, once, when a release turns its key's default.

The import never imports a code default. A key whose section declares a
``ReliedOn`` is the exception: while its variable is not set, the section's
check reads the deployment's records (``DeploymentHistory``, on the import's
connection), and when the deployment existed before, the declared value is
imported under a comment saying why. A check that fails is logged at error
level, holds its key, and is asked again in the same run
(``settle_unchecked``). Each test builds its own section, whose switch is off
by default.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import Field
from structlog.testing import capture_logs

from embry0.config import Embry0Config
from embry0.settings import bootstrap, registry
from embry0.settings.bootstrap import BOOTSTRAP_ACTOR, import_env_once
from embry0.settings.registry import Catalog, EnvImport, Section, SectionError, SectionModel
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.repos import ReposRepository

pytestmark = pytest.mark.requires_postgres

DAY = date(2026, 10, 30)
EVIDENCE = "the deployment existed before the switch became opt-in"


class SwitchSettings(SectionModel):
    running: bool = Field(default=False, description="Run the switched loop; off by default.")
    limit: int = Field(default=5, ge=0, le=100, description="A second key, imported as usual.")


class Check:
    """A check that reads the history it is given, counts its calls, and answers what the deployment's
    records say, or what the test says."""

    def __init__(self, answer: bool | None = None) -> None:
        self.answer = answer
        self.calls = 0
        self.given: list[Any] = []

    async def __call__(self, history: Any) -> bool:
        self.calls += 1
        self.given.append(history)
        found = bool(await history.existed_before(lead_kinds=("ci",)))
        return found if self.answer is None else self.answer


def _relied_on(value: Any, check: Callable[[Any], Awaitable[bool]]) -> Any:
    relied_on = getattr(registry, "ReliedOn", None)
    assert relied_on is not None, "a section can declare a value deployments relied on"
    try:
        return relied_on(value=value, check=check, evidence=EVIDENCE)
    except TypeError:
        pytest.fail("ReliedOn carries the value to keep (value=), the check and the evidence")


def _catalog(check: Callable[[Any], Awaitable[bool]], *, value: Any = True, attr: str = "leads_enabled") -> Catalog:
    section = Section(
        name="switch",
        model=SwitchSettings,
        summary="A section whose switch is off by default.",
        env={
            "running": EnvImport(attr, relied_on=_relied_on(value, check)),
            "limit": "leads_max_new_per_repo_per_day",
        },
    )
    return Catalog((section,))


def _config(**env: str) -> Embry0Config:
    with patch.dict(os.environ, env, clear=True):
        return Embry0Config(_env_file=None)  # type: ignore[call-arg]


def _marked(row: dict[str, Any]) -> tuple[str, ...]:
    """The keys the ledger marks imported."""
    return tuple(entry for entry in row["bootstrapped_keys"] if "@" not in entry)


def _asked(row: dict[str, Any]) -> tuple[str, ...]:
    """The keys the ledger records a first-asked moment for (``section.key@<time>``)."""
    return tuple(entry.partition("@")[0] for entry in row["bootstrapped_keys"] if "@" in entry)


async def _import_at(store: SettingsStore, config: Embry0Config, started: Any, **kwargs: Any) -> Any:
    """``import_env_once`` for a process that started at ``started`` (the database's clock)."""
    assert "started" in inspect.signature(import_env_once).parameters, (
        "the import is told when this process started, so it counts only what came before the release"
    )
    return await import_env_once(store, config, started=started, **kwargs)


async def _register(db: DatabasePool) -> None:
    """A registered repository: what a deployment in use holds and a new one does not."""
    await ReposRepository(db).register("acme/widgets")


async def test_the_declared_value_is_kept_where_the_deployment_existed_before(db_with_migrations: DatabasePool) -> None:
    await _register(db_with_migrations)
    check = Check()
    store = SettingsStore(db_with_migrations, _catalog(check))

    with capture_logs() as logs:
        result = await import_env_once(store, _config(), today=DAY)

    assert result.imported == {"switch": {"running": True}}, (
        f"LEADS_ENABLED is not set and the deployment holds a repository: the declared value is kept: {result.imported}"
    )
    assert getattr(result, "kept", None) == ("switch.running",), "the result names the key whose value was kept"
    assert check.calls == 1 and type(check.given[0]).__name__ == "DeploymentHistory", (
        "the check was asked once, with the deployment's records read on the import's connection"
    )
    row = await store.document()
    assert row["yaml_text"] == (
        f"# Kept on 2026-10-30: LEADS_ENABLED is not set, and {EVIDENCE}.\nswitch:\n  running: true\n"
    ), "a comment above the value says why it is there"
    assert _marked(row) == ("switch.running", "switch.limit"), "both keys are marked"
    assert _asked(row) == ("switch.running",), "and when the relied-on key was first asked is recorded"
    (revision,) = await store.revisions()
    assert (revision["actor"], revision["source"]) == (BOOTSTRAP_ACTOR, "bootstrap")
    audit = await db_with_migrations.fetch("SELECT details FROM audit_log WHERE action = 'settings.bootstrapped'")
    assert [r["details"].get("kept") for r in audit] == [["switch.running"]], "the audit row names the kept key"
    kept = [(e["variable"], e["key"], e["evidence"]) for e in logs if e["event"] == "settings_default_kept"]
    assert kept == [("LEADS_ENABLED", "switch.running", EVIDENCE)], f"one log line says what was kept and why: {kept}"


async def test_the_kept_value_is_the_declared_one_not_the_variable_s_default(db_with_migrations: DatabasePool) -> None:
    """ASKS_ENABLED defaults to false: what is kept is ReliedOn.value, never read from the config."""
    await _register(db_with_migrations)
    store = SettingsStore(db_with_migrations, _catalog(Check(), value=True, attr="asks_enabled"))

    result = await import_env_once(store, _config(), today=DAY)

    assert result.imported == {"switch": {"running": True}}, (
        f"the declared value true is kept although ASKS_ENABLED's own default is false: {result.imported}"
    )


def test_a_relied_on_value_the_section_refuses_or_that_equals_the_default_cannot_be_registered() -> None:
    _relied_on(True, Check())
    with pytest.raises(SectionError, match="relied_on keeps is refused"):
        _catalog(Check(), value="yes")
    with pytest.raises(SectionError, match="relied_on keeps the key's default"):
        _catalog(Check(), value=False)


async def test_a_new_deployment_keeps_nothing(db_with_migrations: DatabasePool) -> None:
    """A database just migrated holds no repository, job or lead: the check finds nothing."""
    check = Check()
    store = SettingsStore(db_with_migrations, _catalog(check))

    result = await import_env_once(store, _config(), today=DAY)

    assert check.calls == 1, "the check was asked"
    assert (result.imported, getattr(result, "kept", None)) == ({}, ()), "a new deployment takes the new default"
    row = await store.document()
    assert (row["yaml_text"], row["data"]) == ("", {})
    assert _marked(row) == ("switch.running", "switch.limit"), "the key is marked all the same"


@pytest.mark.parametrize(("value", "imported"), [("false", {}), ("true", {"switch": {"running": True}})])
async def test_a_variable_that_is_set_is_imported_as_usual_and_the_check_is_never_asked(
    db_with_migrations: DatabasePool, value: str, imported: dict[str, Any]
) -> None:
    """``LEADS_ENABLED=false`` equals the new default, so nothing is written: the operator turned it off."""
    await _register(db_with_migrations)
    check = Check()
    store = SettingsStore(db_with_migrations, _catalog(check))

    result = await import_env_once(store, _config(LEADS_ENABLED=value), today=DAY)

    assert check.calls == 0, f"LEADS_ENABLED={value} is set: the normal import applies and the check is not asked"
    assert (result.imported, getattr(result, "kept", None)) == (imported, ()), result
    if imported:
        text = (await store.document())["yaml_text"]
        assert text.startswith("# Imported from the environment on 2026-10-30: LEADS_ENABLED.\n"), text


async def test_the_check_is_asked_once_ever_and_a_person_s_value_stays(db_with_migrations: DatabasePool) -> None:
    await _register(db_with_migrations)
    check = Check()
    store = SettingsStore(db_with_migrations, _catalog(check))
    await import_env_once(store, _config(), today=DAY)
    await store.save("switch:\n  running: false\n", {"switch": {"running": False}}, actor="alex@example.com")

    again = await import_env_once(SettingsStore(db_with_migrations, _catalog(check)), _config(), today=DAY)

    assert check.calls == 1, "the ledger marks the key: the second boot does not ask again"
    assert (again.imported, again.revision_id) == ({}, None), "and imports nothing"
    assert (await store.document())["data"] == {"switch": {"running": False}}, "the person's false stays"


async def _hide_jobs(db: DatabasePool) -> None:
    """Make the history's own query fail inside the import's transaction, as a real database error does."""
    await db.execute("ALTER TABLE jobs RENAME TO jobs_hidden_for_this_test")


async def _show_jobs(db: DatabasePool) -> None:
    await db.execute("ALTER TABLE jobs_hidden_for_this_test RENAME TO jobs")


async def test_a_failed_check_is_an_error_and_holds_its_key_while_no_retry_has_run(
    db_with_migrations: DatabasePool,
) -> None:
    """The query fails in a savepoint, so the rest of the import still commits."""
    await _register(db_with_migrations)
    await _hide_jobs(db_with_migrations)
    store = SettingsStore(db_with_migrations, _catalog(Check()))

    with capture_logs() as logs:
        first = await import_env_once(store, _config(LEADS_MAX_NEW_PER_REPO_PER_DAY="9"), today=DAY)

    assert first.imported == {"switch": {"limit": 9}}, f"the other key is imported all the same: {first.imported}"
    assert [(item.key, item.variable) for item in getattr(first, "unchecked", ())] == [("running", "LEADS_ENABLED")]
    row = await store.document()
    assert _marked(row) == ("switch.limit",), "the key whose check failed is not marked"
    assert _asked(row) == ("switch.running",), "but when it was first asked is recorded for its next try"
    assert row["data"] == {"switch": {"limit": 9}}, "and reads its default until a try answers"
    failed = [(e["key"], e["log_level"]) for e in logs if e["event"] == "settings_bootstrap_check_failed"]
    assert failed == [("switch.running", "error")], f"an error names the key while it is unresolved: {failed}"
    await _show_jobs(db_with_migrations)


async def test_a_failed_check_is_asked_again_in_the_same_run_until_it_answers(
    db_with_migrations: DatabasePool,
) -> None:
    await _register(db_with_migrations)
    await _hide_jobs(db_with_migrations)
    store = SettingsStore(db_with_migrations, _catalog(Check()))
    first = await import_env_once(store, _config(), today=DAY)
    assert getattr(first, "unchecked", ()), "the start's import left the key unchecked"
    settle = getattr(bootstrap, "settle_unchecked", None)
    assert settle is not None, "a key whose check failed is asked again in the same run, not only at the next start"

    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) == 2:
            await _show_jobs(db_with_migrations)  # the table is back before the second try

    with capture_logs() as logs:
        settled = await settle(store, _config(), first, sleep=sleep)

    assert slept == [60.0, 120.0], f"it waits a minute, then two, between tries: {slept}"
    assert (settled.unchecked, settled.kept) == ((), ("switch.running",)), "the second try answers and keeps it"
    assert (await store.document())["data"] == {"switch": {"running": True}}, "within the same run"
    failed = [e["log_level"] for e in logs if e["event"] == "settings_bootstrap_check_failed"]
    assert failed == ["error"], f"the first try, still failing, is an error: {failed}"
    assert [e["tries"] for e in logs if e["event"] == "settings_bootstrap_check_settled"] == [2]


async def test_a_given_value_and_a_kept_one_each_carry_their_own_comment(db_with_migrations: DatabasePool) -> None:
    await _register(db_with_migrations)
    store = SettingsStore(db_with_migrations, _catalog(Check()))

    await import_env_once(store, _config(LEADS_MAX_NEW_PER_REPO_PER_DAY="9"), today=DAY)

    row = await store.document()
    assert row["data"] == {"switch": {"limit": 9, "running": True}}
    assert row["yaml_text"] == (
        "# Imported from the environment on 2026-10-30: LEADS_MAX_NEW_PER_REPO_PER_DAY.\n"
        "switch:\n"
        "  limit: 9\n"
        f"  # Kept on 2026-10-30: LEADS_ENABLED is not set, and {EVIDENCE}.\n"
        "  running: true\n"
    ), row["yaml_text"]


async def test_only_records_older_than_the_first_ask_count_across_a_restart(db_with_migrations: DatabasePool) -> None:
    """A new deployment whose first check fails, then registers a repository, then restarts: the repository
    came after the release first started, so it is not evidence that the deployment existed before."""
    first_boot = await db_with_migrations.fetchval("SELECT now()")
    await _hide_jobs(db_with_migrations)
    store = SettingsStore(db_with_migrations, _catalog(Check()))
    first = await _import_at(store, _config(), first_boot, today=DAY)
    assert getattr(first, "unchecked", ()), "the first boot's check failed"
    await _show_jobs(db_with_migrations)
    await _register(db_with_migrations)

    second_boot = await db_with_migrations.fetchval("SELECT now()")
    check = Check()
    again = await _import_at(SettingsStore(db_with_migrations, _catalog(check)), _config(), second_boot)

    assert check.calls == 1 and check.given[0].before == first_boot, (
        f"the restart asks about what was there when the release first started, not at this start: {check.given}"
    )
    assert again.imported == {}, f"a repository registered after the first boot is not evidence: {again.imported}"
    assert _marked(await store.document()) == ("switch.limit", "switch.running"), (
        "the key is settled now: off, and asked no more"
    )


async def test_the_retry_reads_the_same_earlier_sources_as_the_start(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key kept in a database record before the document is imported from the record on the retry too, never
    from its variable: the record held 9, the variable says 4."""
    earlier_source = getattr(bootstrap, "EarlierSource", None)
    assert earlier_source is not None, "the settings import reads keys kept in the database before the document"

    async def read(conn: Any, config: Any) -> dict[str, Any]:
        return {"limit": 9}

    source = earlier_source(section="switch", label="the switch table", read=read)
    real = bootstrap._relied_on  # noqa: SLF001
    calls: list[int] = []

    async def fail_once(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("the start's import fails")
        return await real(*args, **kwargs)

    monkeypatch.setattr(bootstrap, "_relied_on", fail_once)
    store = SettingsStore(db_with_migrations, _catalog(Check()))
    config = _config(LEADS_MAX_NEW_PER_REPO_PER_DAY="4")
    with pytest.raises(RuntimeError):
        await import_env_once(store, config, earlier=[source])

    async def no_wait(seconds: float) -> None:
        return None

    settle = getattr(bootstrap, "settle_unchecked", None)
    assert settle is not None and "earlier" in inspect.signature(settle).parameters, (
        "the retry takes the same earlier sources as the start's import"
    )
    settled = await settle(store, config, None, earlier=[source], sleep=no_wait)

    assert settled.earlier == {"switch": {"limit": 9}}, f"the retry imports the record's 9, not the 4: {settled}"
    assert (await store.document())["data"] == {"switch": {"limit": 9}}
