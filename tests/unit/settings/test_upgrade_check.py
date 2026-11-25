"""A deployment that skipped the releases that imported what it kept before the settings document (#168).

The imports are gone, and the state they carried that no variable names (a saved ``budget_config`` row, the
Slop-O-Meter's repositories) is read by nothing now. Startup says so at error level and in the audit log, and only
when the deployment document's ledger has no mark of the import while the older store holds something.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool


def _check() -> Any:
    try:
        return importlib.import_module("embry0.settings.upgrade_check")
    except ImportError:
        pytest.fail("startup must find what a skipped release would have imported, and say so")


async def _mark(db: DatabasePool, *entries: str) -> None:
    async with SettingsStore(db).edit(actor="embry0", source="bootstrap") as draft:
        draft.mark_bootstrapped(entries)


async def _saved_budget(db: DatabasePool) -> None:
    await db.execute("INSERT INTO budget_config (id, daily_cap_usd) VALUES ('default', 5.0)")


async def _meter_rows(db: DatabasePool) -> None:
    await db.execute("INSERT INTO slop_meter_repos (repo) VALUES ('acme/widgets')")


async def _scout_runs(db: DatabasePool) -> None:
    await db.execute("INSERT INTO scout_runs (job_id, repo, template) VALUES ('job-1', 'acme/widgets', 'sample')")


async def _set(db: DatabasePool, scope: str, data: dict[str, Any]) -> None:
    await db.execute(
        "INSERT INTO settings_documents (scope, data) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (scope) DO UPDATE SET data = EXCLUDED.data",
        scope,
        data,
    )


async def _ledger(db: DatabasePool) -> list[str]:
    return list(await db.fetchval("SELECT bootstrapped_keys FROM settings_documents WHERE scope = 'deployment'") or [])


async def _flagged(db: DatabasePool) -> list[Any]:
    return list(await db.fetch("SELECT details FROM audit_log WHERE action = 'settings.upgrade.flagged'"))


async def test_a_new_deployment_says_nothing(db_with_migrations: DatabasePool) -> None:
    with capture_logs() as logs:
        found = await _check().warn_if_imports_skipped(db_with_migrations)
    assert found == [] and not [e for e in logs if e["event"] == "settings_upgrade_skipped"], logs
    assert await _flagged(db_with_migrations) == []


@pytest.mark.parametrize(
    ("kept", "section"),
    [(_saved_budget, "budget"), (_meter_rows, "background.slop_meter"), (_scout_runs, "background.scout")],
    ids=["a saved budget row", "the meter's rows", "the scout's runs"],
)
async def test_state_no_import_carried_is_logged_at_error_level_and_audited(
    db_with_migrations: DatabasePool, kept: Any, section: str
) -> None:
    await kept(db_with_migrations)
    with capture_logs() as logs:
        found = await _check().warn_if_imports_skipped(db_with_migrations)
    events = [e for e in logs if e["event"] == "settings_upgrade_skipped"]
    assert found == [section] and [(e["log_level"], e["section"]) for e in events] == [("error", section)], logs
    assert "start the last release that carried the settings imports" in events[0]["remedy"], events
    flagged = await _flagged(db_with_migrations)
    assert len(flagged) == 1 and section in str(flagged[0]["details"]), flagged


@pytest.mark.parametrize(
    ("kept", "mark"),
    [
        (_saved_budget, "budget.daily_cap_usd"),
        (_meter_rows, "background.slop_meter"),
        (_scout_runs, "background.scout"),
    ],
    ids=["the budget's import ran", "the meter's enrollment import ran", "the scout's enrollment import ran"],
)
async def test_a_deployment_that_ran_the_import_says_nothing(
    db_with_migrations: DatabasePool, kept: Any, mark: str
) -> None:
    await kept(db_with_migrations)
    await _mark(db_with_migrations, mark)
    assert await _check().skipped_imports(db_with_migrations) == []
    assert await _flagged(db_with_migrations) == []


async def test_both_are_named_in_one_audit_row(db_with_migrations: DatabasePool) -> None:
    await _saved_budget(db_with_migrations)
    await _meter_rows(db_with_migrations)
    assert await _check().warn_if_imports_skipped(db_with_migrations) == ["budget", "background.slop_meter"]
    assert len(await _flagged(db_with_migrations)) == 1


async def test_a_mark_of_a_budget_key_the_row_never_held_is_not_the_row_s_import(
    db_with_migrations: DatabasePool,
) -> None:
    """An earlier release imported other budget keys; the row's five were imported by a later one, maybe skipped."""
    await _saved_budget(db_with_migrations)
    await _mark(db_with_migrations, "budget.fix_job_usd")
    assert [s.section for s in await _check().skipped_imports(db_with_migrations)] == ["budget"]


@pytest.mark.parametrize(
    ("kept", "scope", "data"),
    [
        (_saved_budget, "deployment", {"budget": {"daily_cap_usd": 5.0}}),
        (_meter_rows, "organization:acme", {"background": {"slop_meter": True}}),
        (_meter_rows, "deployment", {"background": {"slop_meter": False}}),
        (_scout_runs, "organization:acme", {"background": {"scout": True}}),
    ],
    ids=["the caps set by hand", "an organization enrolled by hand", "the meter turned off for all", "the scout"],
)
async def test_an_operator_who_set_the_section_by_hand_is_not_told_again(
    db_with_migrations: DatabasePool, kept: Any, scope: str, data: dict[str, Any]
) -> None:
    await kept(db_with_migrations)
    await _set(db_with_migrations, scope, data)
    assert await _check().skipped_imports(db_with_migrations) == [], f"{scope} sets {data}: nothing is unread"


@pytest.mark.parametrize(
    ("kept", "data", "section"),
    [
        (_meter_rows, {"background": {"slop_meter": False}}, "background.slop_meter"),
        (_scout_runs, {"background": {"scout": False}}, "background.scout"),
    ],
    ids=["the meter", "the scout"],
)
async def test_a_repository_s_layer_does_not_quiet_it(
    db_with_migrations: DatabasePool, kept: Any, data: dict[str, Any], section: str
) -> None:
    """A key limited to one repository writes that repository's layer; it does not decide for every other one."""
    await kept(db_with_migrations)
    await _set(db_with_migrations, "repository:acme/widgets", data)
    assert [s.section for s in await _check().skipped_imports(db_with_migrations)] == [section], (
        "a repository's layer decided what an unrestricted key decides"
    )


async def test_a_start_with_nothing_to_import_marks_the_ledger_so_later_rows_are_not_a_skipped_upgrade(
    db_with_migrations: DatabasePool,
) -> None:
    """A new deployment: once it has started, keys set and removed again later neither report a skipped upgrade
    nor hold the meter's prune; the text and its history are not touched."""
    assert await _check().warn_if_imports_skipped(db_with_migrations) == []
    ledger = await _ledger(db_with_migrations)
    assert set(ledger) >= {"background.scout", "background.slop_meter", "budget.daily_cap_usd"}, (
        f"a start that finds nothing to import must mark the ledger: {ledger}"
    )
    revisions = await db_with_migrations.fetchval("SELECT count(*) FROM config_revisions WHERE surface = 'settings'")
    await _meter_rows(db_with_migrations)
    await _scout_runs(db_with_migrations)
    await _saved_budget(db_with_migrations)
    assert await _check().warn_if_imports_skipped(db_with_migrations) == []
    assert (
        await db_with_migrations.fetchval("SELECT count(*) FROM config_revisions WHERE surface = 'settings'")
        == revisions
        == 0
    )


async def test_a_start_that_flags_an_import_does_not_mark_it(db_with_migrations: DatabasePool) -> None:
    await _meter_rows(db_with_migrations)
    assert await _check().warn_if_imports_skipped(db_with_migrations) == ["background.slop_meter"]
    ledger = await _ledger(db_with_migrations)
    assert "background.slop_meter" not in ledger and "background.scout" in ledger, ledger
    assert await _check().warn_if_imports_skipped(db_with_migrations) == ["background.slop_meter"], "said again"


async def test_a_check_that_fails_never_stops_the_start() -> None:
    class Broken:
        async def fetchrow(self, *args: Any) -> Any:
            raise OSError("the database went away")

    with capture_logs() as logs:
        assert await _check().warn_if_imports_skipped(Broken()) == []
    assert [e["event"] for e in logs] == ["settings_upgrade_check_failed"], logs
