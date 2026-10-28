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


# -- signature masks (#172) -------------------------------------------------------

MASK = {"pattern": r"\brec_[a-z0-9]{7}\b", "replacement": "<record>"}


def test_signature_masks_are_the_stacks_own_with_no_global_to_inherit() -> None:
    """There is no WATCHER_* default for them (the built-in masks live in code), so a stack
    without the key reads none, and one with it reads its own."""
    without = getattr(StackConfig(GLOBALS, _stack()), "watcher_signature_masks", None)
    assert without is not None and list(without) == [], f"a stack with no masks reads {without!r}"
    view = StackConfig(GLOBALS, _stack(settings={"signature_masks": [MASK]}))
    assert getattr(view, "watcher_signature_masks", None) == [MASK], "the stack's own masks did not reach its view"


def test_every_key_a_stack_may_hold_is_typed() -> None:
    from embry0.services import watcher_stacks

    keys = set(watcher_stacks.OVERRIDABLE) | set(getattr(watcher_stacks, "STACK_ONLY", {}))
    assert "signature_masks" in keys, "signature_masks is not a setting a stack may hold"
    assert keys == set(watcher_stacks._SETTING_TYPES), "a key a stack may hold has no type, or a type has no key"


def test_a_mask_without_a_replacement_leaves_the_built_in_id_placeholder() -> None:
    from embry0.services.watcher_stacks import check_settings

    assert check_settings({"signature_masks": [{"pattern": "rec_[a-z0-9]{7}"}]}) == {
        "signature_masks": [{"pattern": "rec_[a-z0-9]{7}", "replacement": "<id>"}]
    }


@pytest.mark.parametrize(
    ("masks", "why"),
    [
        ([{"pattern": "rec_[", "replacement": "<record>"}], "not a Python regular expression"),
        ([{"pattern": "rec_\\d+", "replacement": "<record>"}] * 51, "at most 50"),
        ([{"pattern": "x" * 501, "replacement": "<record>"}], "at most 500"),
        ([{"pattern": "", "replacement": "<record>"}], "at least 1"),
        ([{"pattern": "(rec_[a-z0-9]+)?", "replacement": "<record>"}], "matches an empty string"),
        ([{"pattern": "rec_\\d+", "replacement": "record"}], "placeholder"),
        ([{"pattern": "rec_\\d+", "replacement": "<deadbeef>"}], "rewritten by the built-in masks"),
        ([{"pattern": "rec_\\d+", "replacement": "<" + "r" * 40 + ">"}], "placeholder"),
        ([{"pattern": "rec_\\d+", "replace": "<record>"}], "Extra inputs"),
        ([{"pattern": 5}], "valid string"),
        ({"pattern": "rec_\\d+"}, "valid list"),
        ("rec_\\d+", "valid list"),
    ],
)
def test_a_bad_mask_is_refused_naming_the_key_and_the_entry(masks: Any, why: str) -> None:
    from embry0.services.watcher_stacks import check_settings

    with pytest.raises(ValueError, match="settings.signature_masks") as refused:
        check_settings({"signature_masks": masks})
    assert why in str(refused.value), str(refused.value)


def test_the_refusal_says_which_mask() -> None:
    from embry0.services.watcher_stacks import check_settings

    with pytest.raises(ValueError) as refused:
        check_settings({"signature_masks": [MASK, {"pattern": "rec_(", "replacement": "<record>"}]})
    assert "settings.signature_masks.1.pattern" in str(refused.value), str(refused.value)
    assert "'rec_('" in str(refused.value), "the refusal does not quote the pattern it refused"


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
