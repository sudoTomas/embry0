"""The ``budget`` section (#171): every spending cap in one place, its defaults the caps embry0 had.

- Every key's default is the value its reader used before the section, so a
  deployment that never set one keeps it.
- No cap: ``0`` is no limit for the four fleet limits, as their variables and
  the dispatch gate always read it; ``null`` is never a stored cap, so an empty
  YAML value cannot lift one. An unattended job's cap is never unlimited.
- The environment import reads each variable as its reader did: a fleet cap at
  or below 0 (or not finite) is 0, an unattended cap of 0 is not imported.
- An organization may set only the unattended jobs' caps; each setting that
  moved out of ``organizations.settings`` lands on one of them.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from embry0.config import Embry0Config
from embry0.services import organizations as org_service
from embry0.settings.bootstrap import env_values
from embry0.settings.document import DocumentInvalid, check
from embry0.settings.registry import Section, SectionRefused
from embry0.settings.sections import CATALOG

FLEET = ("max_budget_per_job_usd", "daily_cap_usd", "monthly_cap_usd", "rate_limit_per_author_per_hour")
UNATTENDED = ("context_audit_job_usd", "area_map_job_usd", "fix_job_usd")


def _budget() -> Section[Any]:
    section = CATALOG.get("budget")
    assert section is not None, "budget is a section of the settings document"
    return section


def test_every_default_is_the_cap_embry0_had_before_the_section() -> None:
    assert _budget().defaults().model_dump(mode="json") == {
        # budget_config's column defaults, MAX_BUDGET_USD and the rest
        "max_budget_per_job_usd": 10.0,
        "daily_cap_usd": 100.0,
        "monthly_cap_usd": 500.0,
        "rate_limit_per_author_per_hour": 5,
        "overrun_mode": "soft",
        # CONTEXT_AUDIT_JOB_BUDGET_USD served all three unattended jobs
        "context_audit_job_usd": 2.0,
        "context_audit_max_jobs_per_tick": 3,
        "area_map_job_usd": 2.0,
        "fix_job_usd": 2.0,
        # a continue: at most the literal 1000 a person could name, past the ceiling, one more cap
        "continue_extra_max_usd": 1000.0,
        "continue_may_exceed_ceiling": True,
        "continue_default_extra_usd": None,
    }


@pytest.mark.parametrize("key", FLEET)
def test_zero_is_no_limit_for_a_fleet_cap_and_null_is_never_a_cap(key: str) -> None:
    section = _budget()
    assert getattr(section.validate({key: 0}), key) == 0, f"{key}: 0 is the no-limit its readers read"
    with pytest.raises(SectionRefused) as refused:
        section.validate({key: None})
    assert [e["loc"] for e in refused.value.errors] == [(key,)], f"{key}: null is refused, never read as no cap"
    with pytest.raises(SectionRefused):
        section.validate({key: -1})


def test_an_empty_yaml_value_is_refused_not_read_as_no_cap() -> None:
    """``daily_cap_usd:`` with nothing after it parses as null: a document that writes it is refused."""
    _budget()
    with pytest.raises(DocumentInvalid) as invalid:
        check(CATALOG, {"budget": {"daily_cap_usd": None}}, stored={})
    assert [p.loc for p in invalid.value.problems] == [("budget", "daily_cap_usd")]


@pytest.mark.parametrize("key", UNATTENDED)
@pytest.mark.parametrize("value", [0, -2.0])
def test_an_unattended_cap_is_never_unlimited(key: str, value: float) -> None:
    with pytest.raises(SectionRefused) as refused:
        _budget().validate({key: value})
    assert [e["loc"] for e in refused.value.errors] == [(key,)]


def test_a_default_extra_above_what_a_person_may_name_is_refused() -> None:
    section = _budget()
    assert section.validate({"continue_default_extra_usd": 50.0}).continue_default_extra_usd == 50.0
    with pytest.raises(SectionRefused, match="continue_default_extra_usd"):
        section.validate({"continue_default_extra_usd": 50.0, "continue_extra_max_usd": 20.0})


def test_an_organization_may_set_only_the_unattended_caps() -> None:
    section = _budget()
    assert section.org_keys == {*UNATTENDED, "context_audit_max_jobs_per_tick"}
    assert section.repo_keys == frozenset()
    with pytest.raises(DocumentInvalid) as invalid:
        check(CATALOG, {"budget": {"daily_cap_usd": 5.0}}, stored={}, layer="organization", inherited={})
    assert [(p.loc, p.type) for p in invalid.value.problems] == [(("budget", "daily_cap_usd"), "not_overridable")]


def test_each_moved_organization_setting_lands_on_a_key_an_organization_may_set() -> None:
    moved_settings = getattr(org_service, "MOVED_SETTINGS", ())
    assert moved_settings, "the organization settings the budget section took over are declared (MOVED_SETTINGS)"
    for moved in moved_settings:
        section = CATALOG.get(moved.section)
        assert section is not None, f"{moved.legacy} moved to no section of this catalog"
        assert moved.key in section.org_keys, f"{moved.legacy} moved to {moved.path}, which no organization may set"
    assert {m.legacy: m.path for m in moved_settings} == {
        "context_audit_budget_usd": "budget.context_audit_job_usd",
        "context_audit_max_jobs_per_tick": "budget.context_audit_max_jobs_per_tick",
    }


def _imported(**env: Any) -> dict[str, Any]:
    """What the import at startup takes from these variables into the budget section."""
    _budget()
    config = Embry0Config(_env_file=None, **env)
    return env_values(CATALOG, config).values.get("budget", {})


@pytest.mark.parametrize(
    ("value", "imported"),
    [(25.0, 25.0), (0.0, 0.0), (-3.0, 0.0), (math.inf, 0.0), (math.nan, 0.0)],
)
def test_a_fleet_cap_variable_is_imported_as_its_reader_read_it(value: float, imported: float) -> None:
    """At or below 0, or not finite, the dispatch gate and the agent node read no limit: 0 is what is stored."""
    got = _imported(max_budget_usd=value, daily_budget_cap_usd=value, monthly_budget_cap_usd=value)
    assert got == dict.fromkeys(("max_budget_per_job_usd", "daily_cap_usd", "monthly_cap_usd"), imported)


@pytest.mark.parametrize(("value", "imported"), [(3, 3), (0, 0), (-1, 0), (5, None)])
def test_the_rate_limit_variable_is_imported_as_the_gate_read_it(value: int, imported: int | None) -> None:
    got = _imported(rate_limit_per_author_per_hour=value)
    assert got == ({} if imported is None else {"rate_limit_per_author_per_hour": imported})


@pytest.mark.parametrize(("value", "imported"), [("hard", "hard"), ("soft", None), ("HARD", None), ("off", None)])
def test_the_overrun_mode_variable_is_imported_only_when_it_was_a_mode(value: str, imported: str | None) -> None:
    """A word but soft or hard read as soft: it is refused, and the default soft stays."""
    assert _imported(budget_overrun_mode=value) == ({} if imported is None else {"overrun_mode": imported})


@pytest.mark.parametrize(
    ("value", "imported"),
    [(1.5, 1.5), ("3", 3.0), (0, None), (-1, None), (math.inf, None), (math.nan, None), (2.0, None)],
)
def test_the_unattended_cap_variable_is_imported_into_all_three_when_positive_and_finite(
    value: Any, imported: float | None
) -> None:
    """CONTEXT_AUDIT_JOB_BUDGET_USD was the cap of the audit, the area mapper and a finding's fix: 0, a
    negative or a non-finite value read as the default 2.0, never as unlimited."""
    got = {key: v for key, v in _imported(context_audit_job_budget_usd=value).items() if key in UNATTENDED}
    assert got == ({} if imported is None else dict.fromkeys(UNATTENDED, imported))


def test_a_variable_set_to_its_default_is_not_imported() -> None:
    """A code default is never frozen into the document: a later release may change it on purpose."""
    assert (
        _imported(
            max_budget_usd=10.0, daily_budget_cap_usd=100.0, monthly_budget_cap_usd=500.0, budget_overrun_mode="soft"
        )
        == {}
    )


def test_jobs_per_tick_keeps_the_bound_of_fifty_as_a_typo_guard() -> None:
    """The bound an organization's setting had: 9999 jobs a tick is a typo, not a policy."""
    section = _budget()
    assert section.validate({"context_audit_max_jobs_per_tick": 50}).context_audit_max_jobs_per_tick == 50
    with pytest.raises(SectionRefused) as refused:
        section.validate({"context_audit_max_jobs_per_tick": 51})
    assert [e["loc"] for e in refused.value.errors] == [("context_audit_max_jobs_per_tick",)]
    assert "context_audit_max_jobs_per_tick" not in _imported(context_audit_max_jobs_per_tick=51), (
        "a variable past the bound is not imported, so it reads the default"
    )
