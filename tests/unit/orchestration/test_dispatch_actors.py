"""#281 — service actors for jobs.dispatched_by, and which of them skip the
per-author hourly limit."""

import ast
from pathlib import Path

import pytest

from embry0.orchestration import dispatch_actors
from embry0.trackers.models import actor_of
from tests.unit.orchestration.test_budget_gate import FakeSettings, FakeSpendRepo, _gate

_EMBRY0 = Path(__file__).resolve().parents[3] / "embry0"


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", sorted(dispatch_actors.RATE_LIMIT_EXEMPT))
async def test_exempt_service_actors_are_never_rate_limited(actor):
    """limit=1 is the starvation case: the count includes the job being
    checked, so a non-exempt watcher would refuse every one of its own ticks."""
    spend = FakeSpendRepo(actor_count=500)
    decision = await _gate(FakeSettings(rate_limit_per_author_per_hour=1), spend).check(actor=actor)
    assert decision.allowed is True
    assert spend.actor_calls == []


@pytest.mark.asyncio
async def test_exempt_actors_still_hit_the_spend_caps():
    decision = await _gate(spend_repo=FakeSpendRepo(monthly=10_000.0)).check(actor=dispatch_actors.WATCHER)
    assert decision.allowed is False
    assert decision.limit == "monthly"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor",
    ["linear-reconciler", dispatch_actors.PR_COMMENT, dispatch_actors.AUTO_ACCEPT, "embry0:something-new", "alex"],
)
async def test_everything_else_is_rate_limited(actor):
    """Exemption is an exact closed set: an autonomous actor (the reconciler)
    keeps the limit as a brake, and a new `embry0:` name is not exempt by
    accident."""
    decision = await _gate(FakeSettings(rate_limit_per_author_per_hour=5), FakeSpendRepo(actor_count=5)).check(
        actor=actor
    )
    assert decision.allowed is False
    assert decision.limit == "rate_limit"


def test_the_context_audit_schedule_skips_the_hourly_limit_and_keeps_the_spend_caps():
    """Its per-tick cap and per-job budget brake it; the hourly limit refused jobs the schedule had just started."""
    assert dispatch_actors.is_rate_limit_exempt(dispatch_actors.CONTEXT_AUDIT)
    from embry0.services.context_audit import ACTOR

    assert ACTOR == dispatch_actors.CONTEXT_AUDIT


def test_the_reconciler_actor_is_the_one_documented_here():
    from embry0.services.linear_reconciler import ACTOR

    assert ACTOR == "linear-reconciler"
    assert not dispatch_actors.is_rate_limit_exempt(ACTOR)


@pytest.mark.parametrize(
    ("payload", "tracker"),
    [
        ({"actor": {"name": "embry0:watcher"}}, "linear"),
        ({"actor": {"email": "embry0:auto-retriage"}}, "linear"),
        ({"sender": {"login": "embry0:watcher"}}, "github"),
        ({"user": {"username": "embry0:answer-resume"}}, "gitlab"),
    ],
)
def test_a_person_cannot_claim_a_service_actor(payload, tracker):
    assert actor_of(payload, tracker) == ""


def test_ordinary_actors_pass_through():
    assert actor_of({"sender": {"login": "alex"}}, "github") == "alex"
    assert actor_of({"actor": {"email": "t@example.com", "name": "T"}}, "linear") == "t@example.com"


# --- structural: no job is created without saying who asked -----------------


def _calls(attr: str, receivers: set[str]) -> list[tuple[str, int, set[str]]]:
    """Every ``<receiver>.<attr>(...)`` call under embry0/, with its keywords."""
    found = []
    for path in sorted(_EMBRY0.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == attr):
                continue
            receiver = ast.unparse(node.func.value)
            if receiver in receivers:
                found.append((str(path.relative_to(_EMBRY0)), node.lineno, {k.arg for k in node.keywords if k.arg}))
    return found


def test_every_jobs_create_call_names_an_actor():
    calls = _calls("create", {"self._jobs", "jobs"})
    assert len(calls) >= 5, calls  # API, executor x3, watcher, PR-comment dispatch
    missing = [(f, line) for f, line, kw in calls if "dispatched_by" not in kw]
    assert not missing, f"jobs.create() without dispatched_by: {missing}"


def test_every_executor_dispatch_names_an_actor():
    calls = _calls("execute", {"self", "executor", "issue_executor", "ctx.issue_executor"})
    assert len(calls) >= 4, calls
    missing = [(f, line) for f, line, kw in calls if "dispatched_by" not in kw]
    assert not missing, f"IssueExecutor.execute() without dispatched_by: {missing}"


def test_issueless_job_entry_points_are_given_the_api_actor():
    for method in ("start_job", "start_onboard_job"):
        calls = _calls(method, {"executor"})
        assert calls, method
        assert all("dispatched_by" in kw for _, _, kw in calls), (method, calls)
