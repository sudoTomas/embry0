"""Production wires the leads loop and the brief writer to the settings document's ``leads`` section.

A loop built with its section read once would run, and ignore every change
to it; one started only when leads are on at startup would never notice
them turned on. A reader left reading a moved variable would follow a value
the document no longer governs. Read from the AST of ``lifespan``, as
test_scout_wiring.py does; ``tests/unit/api/test_leads_settings.py`` runs
the real lifespan.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

import embry0
import embry0.api.app as app_module

#: The ``Embry0Config`` attributes the ``leads`` section imports from: read only by the import.
MOVED = (
    "leads_enabled",
    "leads_finders",
    "leads_interval_hours",
    "leads_backlog_interval_hours",
    "leads_max_new_per_repo_per_day",
    "leads_ticket_min_score",
    "leads_review_stale_days",
    "leads_flaky_commits",
    "proposals_ticket_min_readiness",
)


def _tree() -> ast.Module:
    return ast.parse(pathlib.Path(app_module.__file__).read_text())


def _construction(name: str) -> ast.Call:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name:
            return node
    pytest.fail(f"embry0/api/app.py never constructs {name}")


def _started(tree: ast.Module, task: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "asyncio.create_task"
        and node.args
        and ast.unparse(node.args[0]) == task
    ]


def _conditions_around(tree: ast.Module, calls: list[ast.Call]) -> list[str]:
    """The test of each ``if`` (or ``else``) one of ``calls`` sits in, read from the same parse."""
    return [
        ast.unparse(node.test)
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and any(inner is call for inner in ast.walk(node) for call in calls)
    ]


def test_the_loop_reads_its_section_per_repository_and_always_starts() -> None:
    passed = {kw.arg: ast.unparse(kw.value) for kw in _construction("LeadsLoop").keywords if kw.arg}
    assert passed.get("settings") == "_leads_reader(app.state.settings, _LEADS)", (
        "the leads loop reads the settings document's leads section at every look, per repository, "
        f"through a scoped reader of the store: {passed.get('settings')}"
    )
    tree = _tree()
    starts = _started(tree, "app.state.leads_loop.start()")
    assert len(starts) == 1, "app.py starts the leads loop once"
    assert _conditions_around(tree, starts) == [], (
        "the leads loop is started only under a condition: it must always run and read leads.enabled itself, "
        "so turning leads on needs no restart"
    )


def test_the_check_for_a_loop_started_under_a_condition_can_fail() -> None:
    tree = ast.parse(
        "async def lifespan(app, config):\n"
        "    if config.flag:\n"
        "        task = asyncio.create_task(app.state.leads_loop.start())\n"
    )
    starts = _started(tree, "app.state.leads_loop.start()")
    assert _conditions_around(tree, starts) == ["config.flag"], "a start inside an if is found, with its test"


def test_the_brief_writer_orders_held_tickets_by_the_section() -> None:
    passed = {kw.arg: ast.unparse(kw.value) for kw in _construction("ProposalBriefWriter").keywords if kw.arg}
    assert passed.get("settings") == "brief_settings(app.state.settings)", (
        "the brief writer reads its model and leads.ask_min every tick, from one snapshot of the settings "
        f"documents: {passed}"
    )
    assert not {"min_readiness", "models", "leads"} & set(passed), (
        f"no readiness floor read once from the environment, and no second reader of the documents: {passed}"
    )


def test_no_code_reads_a_variable_the_leads_section_governs() -> None:
    """The config keeps these one release only as the import's source; the section's ``env`` names them."""
    root = pathlib.Path(embry0.__file__).parent
    pattern = re.compile(r"\b(?:" + "|".join(MOVED) + r")\b")
    readers = {
        str(path.relative_to(root)): sorted(set(pattern.findall(path.read_text(encoding="utf-8"))))
        for path in sorted(root.rglob("*.py"))
        if path.name != "config.py" and path.relative_to(root).as_posix() != "settings/sections/leads.py"
    }
    found = {path: names for path, names in readers.items() if names}
    assert found == {}, f"these read a variable the settings document governs now: {found}"


def test_the_check_for_a_reader_of_a_governed_variable_can_fail() -> None:
    pattern = re.compile(r"\b(?:" + "|".join(MOVED) + r")\b")
    assert pattern.findall("min_readiness=config.proposals_ticket_min_readiness") == ["proposals_ticket_min_readiness"]
    assert pattern.findall("if config.leads_enabled:") == ["leads_enabled"]
    assert pattern.findall("leads_enabled_at = 1; my_leads_finders2") == [], "a longer name is not a read"


def test_the_settings_retry_reads_the_same_sources_and_start_as_the_import() -> None:
    """The lifespan's retry imports what the start's import would: the budget's saved row before its variables
    (``earlier``), and only records older than the process start (the store's ``started``)."""
    retries = [
        node
        for node in ast.walk(_tree())
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "settle_unchecked"
    ]
    assert len(retries) == 1, "the lifespan starts one retry of the settings import"
    passed = {kw.arg: ast.unparse(kw.value) for kw in retries[0].keywords if kw.arg}
    assert passed.get("earlier") == "EARLIER_SOURCES", f"the retry reads the same earlier sources: {passed}"
    stores = [
        {kw.arg: ast.unparse(kw.value) for kw in node.keywords if kw.arg}
        for node in ast.walk(_tree())
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "SettingsStore"
    ]
    assert {"started": "settings_started"} in stores, (
        f"the lifespan's store carries when the process began, for every import in it: {stores}"
    )
