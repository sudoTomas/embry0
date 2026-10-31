"""Production builds every background feature on the map of background models (#164).

Each loop takes its model as a reader of the settings document's
``background_models`` section, called every tick, and makes its calls outside
a job through the one ``BackgroundCalls`` that resolves the repository's key,
asks the budget caps and records the spend. A loop built with a value read
once, or with a resolver of its own, constructs happily and silently ignores
every change to the section, or spends outside the caps; so this reads the
AST of ``embry0/api/app.py``, as test_scout_wiring.py does.
``tests/unit/api/test_background_models_settings.py`` runs the real lifespan.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import embry0.api.app as app_module

#: Each loop, the keyword it takes its model by, and the reader production must pass.
READERS = {
    "JobBriefWriter": ("models", "background_model(app.state.settings, 'job_brief')"),
}
#: The scout reads its model with its own section, from one snapshot a tick: ``tick_settings`` reads
#: ``background_models.scout`` (``tests/unit/api/test_scout_per_organization.py`` runs it).
SCOUT_READER = ("settings", "tick_settings(app.state.settings)")
#: The proposal briefs read their model with the ``leads`` section, from one snapshot a tick: ``brief_settings``
#: reads ``background_models.proposal_brief`` (``tests/unit/api/test_leads_settings.py`` counts the snapshots).
BRIEF_READER = ("settings", "brief_settings(app.state.settings)")
#: The variables and the provider setting the features read before #164; app.py reads none of them now.
RETIRED = ("proposal_brief_model", "job_brief_model", "scout_model", "context_audit_local_model", "watcher_local_model")


def _tree() -> ast.Module:
    return ast.parse(pathlib.Path(app_module.__file__).read_text())


def _construction(name: str) -> ast.Call:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name:
            return node
    pytest.fail(f"embry0/api/app.py never constructs {name}")


def _keywords(call: ast.Call) -> dict[str, str]:
    return {kw.arg: ast.unparse(kw.value) for kw in call.keywords if kw.arg}


@pytest.mark.parametrize("loop", sorted(READERS))
def test_each_loop_reads_its_own_key_of_the_section_every_tick(loop: str) -> None:
    keyword, reader = READERS[loop]
    passed = _keywords(_construction(loop))
    assert passed.get(keyword) == reader, (
        f"{loop} must read its model from background_models every tick, through its own key (#164); "
        f"got {keyword}={passed.get(keyword)!r}"
    )


async def test_the_meter_reads_its_judge_s_model_from_the_snapshot_it_reads_its_repositories_from() -> None:
    """The meter reads background_models.slop_judge and its enrollment (#165) from one snapshot a tick:
    ``meter_reader``, which ``tests/unit/api/test_background_scope_wiring.py`` pins production passes."""
    from embry0.services.slop_meter import loop

    meter_reader = getattr(loop, "meter_reader", None)
    assert meter_reader is not None, "the meter's one reader of the settings documents a tick is meter_reader"
    from embry0.settings.sections import CATALOG
    from embry0.settings.store import build_snapshot

    data = {"background_models": {"default": "custom/a", "slop_judge": "custom/judge", "job_brief": "custom/b"}}
    snapshot = build_snapshot(CATALOG, [{"scope": "deployment", "data": data, "generation": 1, "updated_at": None}])

    class Store:
        async def snapshot(self) -> object:
            return snapshot

    read = await meter_reader(Store())()
    assert read.models("acme/widgets") == "custom/judge", "the meter judges outliers on its own key (#164)"


def test_the_scout_reads_its_model_and_its_section_from_one_snapshot() -> None:
    keyword, reader = SCOUT_READER
    passed = _keywords(_construction("ScoutLoop"))
    assert passed.get(keyword) == reader and "models" not in passed, (
        "the scout reads background_models.scout and its scout section from one snapshot a tick, through one "
        f"reader, so a write during a tick cannot mix two versions; got {passed}"
    )


def test_the_proposal_briefs_read_their_model_and_the_leads_section_from_one_snapshot() -> None:
    keyword, reader = BRIEF_READER
    passed = _keywords(_construction("ProposalBriefWriter"))
    assert passed.get(keyword) == reader and not {"models", "leads"} & set(passed), (
        "the proposal briefs read background_models.proposal_brief and the leads section from one snapshot a "
        f"tick, through one reader, so a write during a tick cannot mix two versions; got {passed}"
    )


@pytest.mark.parametrize("loop", ["ProposalBriefWriter", "JobBriefWriter", "SlopMeterLoop"])
def test_each_call_outside_a_job_goes_through_the_one_metered_path(loop: str) -> None:
    passed = _keywords(_construction(loop))
    assert passed.get("calls") == "app.state.background_calls", (
        f"{loop}'s calls must resolve the repository's key, ask the budget caps and record their spend"
    )
    assert "resolver" not in passed, f"{loop} must not resolve a key of its own, outside the caps"


def test_the_metered_path_records_spend_and_asks_the_budget_gate() -> None:
    passed = _keywords(_construction("BackgroundCalls"))
    assert passed.get("spend") == "BackgroundSpendRepository(db)", (
        "every background call's spend goes where the budget caps and /stats count it"
    )
    assert passed.get("gate") == "build_budget_gate(app.state.budget_policy)", (
        "a hosted model's call asks the gate jobs are dispatched through, built by the one factory both use over "
        "the application's budget policy: the operator's caps, never the section's code defaults"
    )
    assert passed.get("resolver") == "getattr(app.state, 'credential_resolver', None)"
    assigned = [
        ast.unparse(node.targets[0])
        for node in ast.walk(_tree())
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and ast.unparse(node.value.func) == "BackgroundCalls"
    ]
    assert assigned == ["app.state.background_calls"]


def test_the_watcher_prefers_its_stack_s_model_from_the_section() -> None:
    passed = _keywords(_construction("WatcherService"))
    assert passed.get("local_first_model") == "_watcher_local_first_model(app, stack.repo)", (
        "each stack reads background_models.watcher as its own repository reads it"
    )
    body = next(
        ast.unparse(node)
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef) and node.name == "_watcher_local_first_model"
    )
    assert "background_model(app.state.settings, 'watcher')" in body
    assert "ProviderConfigRepository" not in body, "the watcher's model is no longer the provider settings'"


def test_the_retired_chain_and_variables_are_read_nowhere_in_the_app() -> None:
    read: list[str] = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Attribute) and node.attr in RETIRED and ast.unparse(node.value) == "config":
            read.append(ast.unparse(node))
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "getattr" and len(node.args) >= 2:
            name = node.args[1]
            if isinstance(name, ast.Constant) and name.value in RETIRED:
                read.append(ast.unparse(node))
    assert read == [], f"each background feature reads its model from the settings document only (#164): {read}"


def test_the_provider_settings_are_imported_once_after_the_variables() -> None:
    lifespan = next(n for n in ast.walk(_tree()) if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
    awaited = [
        ast.unparse(node.value.func)
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
    ]
    assert "import_provider_settings_once" in awaited, "the old watcher model must reach the section at startup"
    assert awaited.index("import_env_once") < awaited.index("import_provider_settings_once"), (
        "after the variables, so the chain it fills in never overwrites a feature's own variable"
    )
