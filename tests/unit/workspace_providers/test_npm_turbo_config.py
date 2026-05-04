import pytest
from pydantic import ValidationError

from athanor.workspace_providers.npm_workspaces_turbo.config import NpmTurboConfig


def test_config_defaults_match_spec():
    cfg = NpmTurboConfig()
    assert cfg.affected_filter == "[origin/${base_branch}]"
    assert str(cfg.turbo_config_path) == "turbo.json"
    assert cfg.apps_glob == "apps/*"
    assert cfg.packages_glob == "packages/*"


def test_config_overrides_apply():
    cfg = NpmTurboConfig(
        affected_filter="[main...HEAD]",
        turbo_config_path="custom-turbo.json",
        apps_glob="services/*",
        packages_glob="libs/*",
    )
    assert cfg.affected_filter == "[main...HEAD]"
    assert cfg.apps_glob == "services/*"


def test_config_rejects_extra_fields():
    with pytest.raises(ValidationError):
        NpmTurboConfig(unknown_field=42)
