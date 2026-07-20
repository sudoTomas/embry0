"""StaticAppsProvider + RootScopedProvider (EMB-32)."""

from pathlib import Path

import pytest

from embry0.workspace_providers.provider import AffectedSet, WorkspaceProvider
from embry0.workspace_providers.root_scoped import RootScopedProvider
from embry0.workspace_providers.static_apps import StaticAppsProvider

CFG = {
    "apps": {
        "hub": {"path": "apps/hub", "prefixes": ["apps/hub/", "shared/"]},
        "leads": {"prefixes": ["apps/leads/"]},
        "quoting": {},  # no prefixes -> always affected
    }
}


def _p(config) -> StaticAppsProvider:
    return StaticAppsProvider(Path("/tmp/repo"), config)


def test_satisfies_protocol():
    assert isinstance(_p(CFG), WorkspaceProvider)


def test_discover_returns_declared_apps():
    apps, packages = _p(CFG).discover()
    assert sorted(a.name for a in apps) == ["hub", "leads", "quoting"]
    assert all(p.is_app for p in packages)
    hub = next(a for a in apps if a.name == "hub")
    assert hub.path == Path("apps/hub")
    assert hub.package_name == "hub"


def test_affected_by_prefix_match():
    result = _p(CFG).affected([Path("apps/hub/src/page.tsx")], frozenset())
    # hub matched by prefix; quoting always included; leads not matched.
    assert result.apps_to_qa == frozenset({"hub", "quoting"})
    assert result.directly_changed == result.cascade_closure == result.apps_to_qa


def test_shared_prefix_hits_multiple_declaring_apps():
    result = _p(CFG).affected([Path("shared/lib.ts")], frozenset())
    assert "hub" in result.apps_to_qa
    assert "leads" not in result.apps_to_qa


def test_empty_diff_keeps_only_always_affected_apps():
    result = _p(CFG).affected([], frozenset())
    assert result.apps_to_qa == frozenset({"quoting"})


def test_list_form_apps_always_affected():
    provider = _p({"apps": ["hub", "leads"]})
    result = provider.affected([], frozenset())
    assert result.apps_to_qa == frozenset({"hub", "leads"})


def test_validate_flags_undeclared_qa_yaml_apps():
    warnings = _p(CFG).validate(["hub", "ghost"])
    assert len(warnings) == 1
    assert "ghost" in warnings[0]


def test_validate_empty_config_errors():
    warnings = _p({}).validate(["hub"])
    assert warnings and "no apps declared" in warnings[0]


# ---------------------------------------------------------------------------
# RootScopedProvider
# ---------------------------------------------------------------------------


class _RecordingProvider:
    name = "recording"

    def __init__(self):
        self.seen_changed: list[Path] | None = None

    def discover(self):
        return [], []

    def affected(self, changed_files, no_cascade_packages):
        self.seen_changed = list(changed_files)
        return AffectedSet(frozenset(), frozenset(), frozenset())

    def validate(self, apps):
        return []


def test_root_scoped_rebases_changed_files_and_drops_outside():
    inner = _RecordingProvider()
    wrapper = RootScopedProvider(inner, root="frontend")
    wrapper.affected(
        [Path("frontend/apps/hub/page.tsx"), Path("backend/src/Main.java")],
        frozenset(),
    )
    assert inner.seen_changed == [Path("apps/hub/page.tsx")]


def test_root_scoped_delegates_name_discover_validate():
    inner = _RecordingProvider()
    wrapper = RootScopedProvider(inner, root="frontend/")
    assert wrapper.name == "recording"
    assert wrapper.discover() == ([], [])
    assert wrapper.validate(["x"]) == []


def test_static_apps_rejects_unknown_config_keys():
    with pytest.raises(Exception, match="extra|not permitted|Extra"):
        _p({"apps": {}, "bogus": 1})
