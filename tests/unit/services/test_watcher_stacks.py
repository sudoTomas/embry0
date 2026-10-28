"""Stack rows and the config view over them (#378).

The view is the whole compatibility story: every `self._config.X` in the
watcher must answer from the stack's row when it overrides X and from the
process config otherwise, so a deployment that never writes a row behaves
exactly as it did before stacks existed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from embry0.services.watcher_stacks import (
    OVERRIDABLE,
    StackConfig,
    WatcherStack,
    seed_stacks,
    stack_from_env,
)

GLOBALS = SimpleNamespace(
    watcher_repo="acme/backend",
    watcher_logql='{compose_project="acme"}',
    watcher_interval_seconds=3600,
    watcher_min_log_lines=5,
    watcher_max_open_proposals=3,
    watcher_job_budget_usd=2.0,
    watcher_linear_team_id="team-1",
    watcher_linear_project_id="proj-1",
    watcher_deploy_logql='{job="acme-deploy"}',
    context_proxy_container_allowlist="acme-backend",
    api_key="unrelated",
)


def _stack(**overrides: Any) -> WatcherStack:
    base = {"repo": "example-org/embry0", "logql": '{compose_project="embry0"}'}
    return WatcherStack(**{**base, **overrides})


# -- the config view ------------------------------------------------------------


def test_a_stack_without_overrides_reads_every_global() -> None:
    view = StackConfig(GLOBALS, _stack())
    assert view.watcher_interval_seconds == 3600
    assert view.watcher_max_open_proposals == 3
    assert view.watcher_job_budget_usd == 2.0
    assert view.api_key == "unrelated", "settings the watcher does not own still resolve"


def test_the_stack_owns_its_repo_and_selector() -> None:
    """Never inherited: these two ARE the stack."""
    view = StackConfig(GLOBALS, _stack())
    assert view.watcher_repo == "example-org/embry0"
    assert view.watcher_logql == '{compose_project="embry0"}'


def test_an_override_wins_and_its_neighbours_do_not_move() -> None:
    view = StackConfig(GLOBALS, _stack(settings={"interval_seconds": 900, "max_open_proposals": 1}))
    assert view.watcher_interval_seconds == 900
    assert view.watcher_max_open_proposals == 1
    assert view.watcher_min_log_lines == 5, "an absent key still inherits"


def test_an_unknown_setting_key_is_ignored() -> None:
    """A typo must not become a setting nothing reads."""
    view = StackConfig(GLOBALS, _stack(settings={"intervul_seconds": 5}))
    assert view.watcher_interval_seconds == 3600
    assert not hasattr(view, "intervul_seconds")


def test_every_overridable_key_maps_to_a_real_global() -> None:
    """A mapping to an attribute the config does not have would read as an
    override that silently never applies."""
    missing = [g for g in OVERRIDABLE.values() if not hasattr(GLOBALS, g) and not g.startswith(("log_", "watcher_"))]
    assert missing == []
    from embry0.config import Embry0Config

    config = Embry0Config(_env_file=None)
    unknown = [g for g in OVERRIDABLE.values() if not hasattr(config, g)]
    assert unknown == [], f"OVERRIDABLE names settings Embry0Config does not have: {unknown}"


def test_every_overridable_key_has_a_type_matching_its_global() -> None:
    """#172: each key is checked, and its global's own default passes the check (else the schema and
    the config disagree about what the setting is)."""
    from embry0.config import Embry0Config
    from embry0.services.watcher_stacks import check_setting

    config = Embry0Config(_env_file=None)
    for key, name in OVERRIDABLE.items():
        default = getattr(config, name)
        assert check_setting(key, default) == default, f"{key}: its default {default!r} does not pass its own check"


@pytest.mark.parametrize(
    ("settings", "named"),
    [
        ({"auto_accept_max_per_day": "x"}, "auto_accept_max_per_day"),
        ({"auto_accept_max_per_day": -1}, "auto_accept_max_per_day"),
        ({"auto_accept_min_severity": "hgh"}, "auto_accept_min_severity"),
        ({"auto_accept": "true"}, "auto_accept"),
        ({"interval_seconds": 0}, "interval_seconds"),
        ({"skip_unchanged_mode": "maybe"}, "skip_unchanged_mode"),
    ],
)
def test_a_bad_value_is_refused_naming_its_key(settings: dict[str, Any], named: str) -> None:
    from embry0.services.watcher_stacks import check_settings

    with pytest.raises(ValueError, match=f"settings.{named}"):
        check_settings(settings)


def test_good_values_are_kept_and_unknown_keys_dropped() -> None:
    from embry0.services.watcher_stacks import check_settings

    assert check_settings(
        {"auto_accept_min_severity": " High ", "job_budget_usd": 2, "auto_accept": True, "intervul_seconds": 1}
    ) == {"auto_accept_min_severity": "high", "job_budget_usd": 2.0, "auto_accept": True}


def test_a_stored_bad_value_reads_as_the_global_and_never_raises() -> None:
    """#172: a row written before values were checked; the tick reads the deployment's value for that key."""
    view = StackConfig(GLOBALS, _stack(settings={"interval_seconds": "soon", "max_open_proposals": 1}))
    assert view.watcher_interval_seconds == GLOBALS.watcher_interval_seconds, "the bad override is left out"
    assert view.watcher_max_open_proposals == 1, "a good neighbour still applies"


# -- seeding --------------------------------------------------------------------


class FakeStacksRepo:
    def __init__(self, rows: int = 0) -> None:
        self.rows = rows
        self.upserted: list[WatcherStack] = []

    async def count(self) -> int:
        return self.rows

    async def upsert(self, stack: WatcherStack) -> WatcherStack:
        self.upserted.append(stack)
        self.rows += 1
        return stack


def test_the_env_stack_is_read_off_the_config() -> None:
    stack = stack_from_env(GLOBALS)
    assert stack is not None
    assert (stack.repo, stack.tracker) == ("acme/backend", "linear")
    assert stack.tracker_target == {"team_id": "team-1", "project_id": "proj-1"}
    assert stack.settings == {}, "seeding must not freeze today's globals into the row"


def test_an_unconfigured_deployment_seeds_nothing() -> None:
    assert stack_from_env(SimpleNamespace(watcher_repo="", watcher_logql="")) is None


@pytest.mark.asyncio
async def test_seeding_happens_once_and_never_resurrects_a_deleted_stack() -> None:
    repo = FakeStacksRepo()
    assert (await seed_stacks(repo, GLOBALS)) is not None
    assert [s.repo for s in repo.upserted] == ["acme/backend"]

    assert (await seed_stacks(repo, GLOBALS)) is None, "a non-empty table is never re-seeded"

    # An operator deleted the seeded stack and left the table with another.
    repo.upserted.clear()
    assert (await seed_stacks(repo, GLOBALS)) is None
    assert repo.upserted == []
