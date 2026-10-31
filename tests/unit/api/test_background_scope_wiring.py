"""Production reads every background feature's repositories through the one scope rule (#165).

A loop built with a list of its own (the world map's regions, an
environment variable) constructs happily and reads exactly what it read
before, which looks like an operator who enrolled nothing; so this reads the
AST of ``embry0/api/app.py``, as test_scout_wiring.py does.
``tests/unit/api/test_background_scope_live.py`` runs the real lifespan.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import embry0.api.app as app_module

#: What the scope rule replaced: the world map's regions and the variables the lists were read from.
RETIRED = ("scout_repos", "scout_exclude", "slop_meter_repos", "slop_meter_exclude")


def _tree() -> ast.Module:
    return ast.parse(pathlib.Path(app_module.__file__).read_text())


def _construction(name: str) -> dict[str, str]:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name:
            return {kw.arg: ast.unparse(kw.value) for kw in node.keywords if kw.arg}
    pytest.fail(f"embry0/api/app.py never constructs {name}")


def _function(name: str) -> str:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.unparse(node)
    pytest.fail(f"embry0/api/app.py defines no {name}")


def test_the_meter_lists_registered_repositories_and_reads_its_enrollment_every_tick() -> None:
    passed = _construction("SlopMeterLoop")
    assert passed.get("repos") == "registry_reader(app.state.repos_repo)", (
        "the meter's candidates are the registered repositories, active or not (it reads the active ones and "
        f"keeps what it read of the inactive), never the world map's regions: got repos={passed.get('repos')!r}"
    )
    assert passed.get("read") == "meter_reader(app.state.settings)", (
        "the meter reads background.slop_meter and its judge's model from one snapshot a tick: "
        f"got read={passed.get('read')!r}"
    )
    assert "models" not in passed, "no second reader of the settings documents: a tick could mix two versions"


def test_the_meter_records_departures_and_audits_each_removal() -> None:
    passed = _construction("SlopMeterLoop")
    assert passed.get("exits") == "ScopeExitsRepository(db)", (
        "when a repository left the meter's scope is stored, so its removal waits background.prune_after_days "
        f"across restarts: got exits={passed.get('exits')!r}"
    )
    assert passed.get("audit") == (
        "_slop_meter_audit_writer(_slop_meter_emit_audit, db, actor=_slop_meter_actors.SLOP_METER, "
        "audit_log_path=config.audit_log_path)"
    ), f"each removal and each refusal leaves an audit row: got audit={passed.get('audit')!r}"


def test_the_scout_lists_registered_repositories_and_reads_its_enrollment_with_its_section() -> None:
    passed = _construction("ScoutLoop")
    assert passed.get("repos") == "registered_reader(app.state.repos_repo)", (
        f"the scout's candidates are the registered, active repositories: got repos={passed.get('repos')!r}"
    )
    assert passed.get("settings") == "tick_settings(app.state.settings)", (
        "tick_settings reads background.scout from the snapshot the scout's section and model come from"
    )


def test_the_leads_targets_are_the_enrolled_repositories_someone_answers_for() -> None:
    body = _function("_lead_targets")
    assert "_leads_enrollment(section.snapshot)" in body and "app.state.settings.snapshot()" not in body, (
        "a pass reads background.leads from the snapshot its leads section came from, never a second one"
    )
    passed = _construction("LeadsLoop")
    assert passed.get("settings") == "_leads_reader(app.state.settings, _LEADS)" and passed.get("targets") == (
        "_lead_targets"
    ), f"the loop hands its targets the section it read this pass: {passed}"
    assert ".scope('leads', await _leads_candidates(), among=" in body, (
        "of the registered repositories, those enrolled in leads, among those with owners or a watched stack"
    )


def test_no_feature_reads_the_world_map_or_the_retired_lists_for_its_repositories() -> None:
    read = [
        ast.unparse(node)
        for node in ast.walk(_tree())
        if isinstance(node, ast.Attribute)
        and (
            (node.attr in RETIRED and ast.unparse(node.value) == "config")
            or (node.attr == "regions" and ast.unparse(node.value) == "app.state.world_repo")
        )
    ]
    assert read == [], f"which repositories a background feature reads is the scope rule's alone: {read}"


def test_the_enrollment_is_imported_once_at_startup_before_any_loop_reads_it() -> None:
    lifespan = next(n for n in ast.walk(_tree()) if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
    awaited = [
        ast.unparse(node.value)
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
    ]
    started = [call for call in awaited if call.startswith("import_enrollment_once(")]
    assert started == [
        "import_enrollment_once(app.state.settings, config, reported=enrollment_reported, "
        "metrics=getattr(app.state, 'loop_metrics', None))"
    ], f"the first start of the release enrolls what the scout and the meter read before (#165): {started}"
    order = [call.split("(")[0] for call in awaited]
    assert (
        order.index("import_provider_settings_once")
        < order.index("import_enrollment_once")
        < order.index("app.state.settings.snapshot")
    ), "after the other imports, and before the first snapshot every loop starts from"
    source = ast.unparse(lifespan)
    assert "log_ignored_lists(first_snapshot, config)" in source, (
        "every start says when a list variable is set and no longer read"
    )
    retried = [
        node
        for node in ast.walk(lifespan)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "enrollment_import is None or enrollment_import.failed"
    ]
    assert retried and (
        "settle_enrollment(app.state.settings, config, enrollment_import, reported=enrollment_reported, "
        "metrics=getattr(app.state, 'loop_metrics', None))"
    ) in ast.unparse(retried[0]), (
        "an enrollment import that failed is tried again in the same run, with the store's start, sharing the "
        "names already reported (each logged at error level and audited once) and the blocked gauge"
    )
