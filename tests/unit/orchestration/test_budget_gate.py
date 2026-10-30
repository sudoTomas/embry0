"""BudgetGate — daily/monthly caps and the per-actor hourly rate limit (#246).

Both collaborators are fakes, so the policy is exercised without Postgres.
The caps are the settings document's ``budget`` section, which the gate asks
for at every check (#171).
"""

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from embry0.orchestration.budget_gate import BudgetGate
from embry0.settings.sections.budget import BudgetSettings

FIXED_NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)

DEFAULT_CONFIG = {
    "max_budget_per_job_usd": 10.0,
    "daily_cap_usd": 100.0,
    "monthly_cap_usd": 500.0,
    "rate_limit_per_author_per_hour": 5,
    "overrun_mode": "soft",
}


class FakeSettings:
    """The ``budget`` section as the gate reads it: asked for at every check."""

    def __init__(self, **over):
        self.section = BudgetSettings(**{**DEFAULT_CONFIG, **over})

    async def __call__(self):
        return self.section


class FakeSpendRepo:
    def __init__(self, daily=0.0, monthly=0.0, actor_count=0):
        self._rollup = {"daily": daily, "monthly": monthly}
        self._actor_count = actor_count
        self.actor_calls: list[tuple[str, datetime]] = []
        self.excluded: list[str | None] = []

    async def spend_rollup(self):
        return dict(self._rollup)

    async def dispatch_count_since(self, actor, since, *, excluding=None):
        self.actor_calls.append((actor, since))
        self.excluded.append(excluding)
        return self._actor_count


def _gate(settings=None, spend_repo=None):
    return BudgetGate(
        settings or FakeSettings(),
        spend_repo or FakeSpendRepo(),
        clock=lambda: FIXED_NOW,
    )


@pytest.mark.asyncio
async def test_allows_dispatch_below_every_cap():
    decision = await _gate(spend_repo=FakeSpendRepo(daily=10.0, monthly=200.0)).check(actor="alex")
    assert decision.allowed is True
    assert decision.limit == ""


@pytest.mark.asyncio
async def test_refuses_at_the_monthly_cap():
    decision = await _gate(spend_repo=FakeSpendRepo(monthly=500.0)).check()
    assert decision.allowed is False
    assert decision.limit == "monthly"
    assert decision.details == {"spent_usd": 500.0, "cap_usd": 500.0}


@pytest.mark.asyncio
async def test_refuses_at_the_daily_cap():
    decision = await _gate(spend_repo=FakeSpendRepo(daily=120.0, monthly=120.0)).check()
    assert decision.allowed is False
    assert decision.limit == "daily"


@pytest.mark.asyncio
async def test_zero_cap_means_unlimited():
    repos = FakeSpendRepo(daily=9999.0, monthly=9999.0)
    config = FakeSettings(daily_cap_usd=0.0, monthly_cap_usd=0.0)
    assert (await _gate(config, repos).check()).allowed is True


@pytest.mark.asyncio
async def test_refuses_when_the_actor_hit_the_hourly_rate_limit():
    spend = FakeSpendRepo(actor_count=5)
    decision = await _gate(FakeSettings(rate_limit_per_author_per_hour=5), spend).check(actor="alex")
    assert decision.allowed is False
    assert decision.limit == "rate_limit"
    assert decision.details == {"actor": "alex", "dispatched": 5, "limit_per_hour": 5}
    assert spend.actor_calls == [("alex", FIXED_NOW - timedelta(hours=1))]


@pytest.mark.asyncio
async def test_the_job_being_checked_is_left_out_of_its_own_count():
    """#116: the runner checks a job whose pending row already exists. The
    gate passes its id down so the count is the actor's other jobs, the same
    count a caller that asks before creating the job gets."""
    spend = FakeSpendRepo(actor_count=4)
    decision = await _gate(FakeSettings(rate_limit_per_author_per_hour=5), spend).check(actor="alex", excluding="job-5")
    assert decision.allowed is True, "four other jobs against a limit of five: the fifth runs"
    assert spend.excluded == ["job-5"]
    assert (await _gate(FakeSettings(rate_limit_per_author_per_hour=5), spend).check(actor="alex")).allowed is True
    assert spend.excluded == ["job-5", None], "an ask before the job exists leaves nothing out"


@pytest.mark.asyncio
async def test_rate_limit_is_skipped_without_an_actor():
    spend = FakeSpendRepo(actor_count=99)
    assert (await _gate(spend_repo=spend).check()).allowed is True
    assert spend.actor_calls == []


@pytest.mark.asyncio
async def test_rate_limit_of_zero_is_disabled():
    spend = FakeSpendRepo(actor_count=99)
    config = FakeSettings(rate_limit_per_author_per_hour=0)
    assert (await _gate(config, spend).check(actor="alex")).allowed is True
    assert spend.actor_calls == []


@pytest.mark.asyncio
async def test_fails_open_when_the_pool_is_down():
    class Broken:
        async def spend_rollup(self):
            raise ConnectionResetError("pool is down")

    assert (await _gate(spend_repo=Broken()).check()).allowed is True


# --- fail-open is for a degraded DB, not for a broken gate (#246) -----------


class _RaisingSpendRepo(FakeSpendRepo):
    def __init__(self, exc):
        super().__init__()
        self._exc = exc

    async def spend_rollup(self):
        raise self._exc

    async def dispatch_count_since(self, actor, since, *, excluding=None):
        raise self._exc


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError("no route to postgres"),
        OSError("socket closed"),
        TimeoutError("query timed out"),
        asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed"),
        asyncpg.exceptions.TooManyConnectionsError("pool exhausted"),
        asyncpg.exceptions.InterfaceError("pool is closed"),
    ],
)
@pytest.mark.asyncio
async def test_connection_trouble_fails_open(exc):
    """The caps live in the same Postgres as the jobs table: refusing every
    dispatch on a transient failure turns a cost control into an outage."""
    assert (await _gate(spend_repo=_RaisingSpendRepo(exc)).check(actor="alex")).allowed is True


@pytest.mark.parametrize(
    "exc",
    [
        asyncpg.exceptions.UndefinedColumnError('column "dispatched_by" does not exist'),
        asyncpg.exceptions.UndefinedTableError('relation "jobs" does not exist'),
        asyncpg.exceptions.PostgresSyntaxError("syntax error at or near"),
        TypeError("spend_rollup() takes 1 positional argument"),
        KeyError("daily"),
    ],
)
@pytest.mark.asyncio
async def test_a_broken_gate_fails_loud_instead_of_disabling_the_caps(exc):
    """A schema drift or a coding error means the gate is broken, not the DB.
    Swallowing it would disable layers 2+3 behind one warning line and allow
    unlimited spend with green CI."""
    with pytest.raises(type(exc)):
        await _gate(spend_repo=_RaisingSpendRepo(exc)).check(actor="alex")


@pytest.mark.asyncio
async def test_a_broken_rate_query_fails_loud_too():
    class BadRate(FakeSpendRepo):
        async def dispatch_count_since(self, actor, since, *, excluding=None):
            raise asyncpg.exceptions.UndefinedColumnError('column "dispatched_by" does not exist')

    with pytest.raises(asyncpg.exceptions.UndefinedColumnError):
        await _gate(spend_repo=BadRate()).check(actor="alex")


@pytest.mark.asyncio
async def test_a_transient_rate_query_failure_still_fails_open():
    class FlakyRate(FakeSpendRepo):
        async def dispatch_count_since(self, actor, since, *, excluding=None):
            raise asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed")

    assert (await _gate(spend_repo=FlakyRate()).check(actor="alex")).allowed is True


# --- what reaches a possibly-public issue (#246) ----------------------------


@pytest.mark.asyncio
async def test_the_public_message_carries_no_figures():
    """`decision.message` lands in jobs.error_message, which the tracker
    comment channel echoes verbatim onto the originating issue. Spend totals
    and caps belong in the audit row and the logs, not on a public ticket."""
    decision = await _gate(FakeSettings(monthly_cap_usd=400.0), FakeSpendRepo(monthly=483.12)).check()
    assert "483.12" in decision.message  # the operator-facing detail is kept
    assert "$" not in decision.public_message
    assert decision.public_message == "dispatch refused: budget cap reached"


@pytest.mark.asyncio
async def test_the_public_rate_limit_message_names_no_actor():
    spend = FakeSpendRepo(actor_count=5)
    decision = await _gate(FakeSettings(rate_limit_per_author_per_hour=5), spend).check(actor="alex")
    assert "alex" in decision.message
    assert "alex" not in decision.public_message
    assert decision.public_message == "dispatch refused: dispatch rate limit reached"


@pytest.mark.asyncio
async def test_an_allowed_decision_has_no_public_message():
    assert (await _gate().check()).public_message == ""


# --- one place builds a gate (#171 review) -----------------------------------


def _gate_constructions() -> list[tuple[str, str]]:
    """Every call that constructs a BudgetGate in the embry0 package: (module path, enclosing function)."""
    import ast
    import pathlib

    import embry0

    root = pathlib.Path(embry0.__file__).parent
    found: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        names = {"BudgetGate"} | {
            alias.asname
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name == "BudgetGate" and alias.asname
        }
        functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            name = (
                callee.id if isinstance(callee, ast.Name) else callee.attr if isinstance(callee, ast.Attribute) else ""
            )
            if name not in names:
                continue
            enclosing = [f.name for f in functions if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)]
            found.append((path.relative_to(root.parent).as_posix(), enclosing[-1] if enclosing else "<module>"))
    return found


def test_only_build_budget_gate_constructs_a_gate():
    """`build_budget_gate(policy)` is the one place a gate is built, so every caller reads the caps through
    the budget policy and a change to the constructor is made once. A second builder (one over the database,
    say) would read the section's defaults while the import is pending."""
    found = _gate_constructions()
    assert found == [("embry0/orchestration/budget_gate.py", "build_budget_gate")], (
        f"a BudgetGate is built somewhere other than build_budget_gate: {found}"
    )


@pytest.mark.asyncio
async def test_the_built_gate_reads_the_caps_through_the_policy_at_every_check():
    from types import SimpleNamespace

    from embry0.orchestration import budget_gate

    if not hasattr(budget_gate, "build_budget_gate"):
        pytest.fail("no build_budget_gate(policy) builds the gate over the budget policy")
    reads = {"n": 0}

    async def settings():
        reads["n"] += 1
        return BudgetSettings(**{**DEFAULT_CONFIG, "daily_cap_usd": 20.0})

    policy = SimpleNamespace(settings=settings, store=SimpleNamespace(db=object()))
    gate = budget_gate.build_budget_gate(policy)
    gate._spend_repo = FakeSpendRepo(daily=25.0)
    decision = await gate.check()
    assert (decision.allowed, decision.limit) == (False, "daily"), "the policy's $20 daily cap, not the default $100"
    await gate.check()
    assert reads["n"] == 2, "the policy is asked at every check"


def test_the_executor_builds_its_gate_with_build_budget_gate_over_its_policy(monkeypatch):
    from types import SimpleNamespace

    from embry0.orchestration import budget_gate
    from embry0.services.issue_executor import IssueExecutor

    if not hasattr(budget_gate, "build_budget_gate"):
        pytest.fail("no build_budget_gate(policy) for the executor to build its gate with")
    built = []
    monkeypatch.setattr(budget_gate, "build_budget_gate", lambda policy: built.append(policy) or "the gate")
    policy = SimpleNamespace(settings=None, store=SimpleNamespace(db=None))
    executor = IssueExecutor.__new__(IssueExecutor)
    executor._budget = policy
    executor._budget_gate = None
    assert executor._get_budget_gate() == "the gate"
    assert built == [policy], "the executor's gate is built over the app's budget policy"
    assert executor._get_budget_gate() == "the gate" and len(built) == 1, "built once"


def test_without_a_database_the_executor_has_no_gate():
    from embry0.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._budget_gate = None
    assert executor._get_budget_gate() is None, "no DB, no settings, no policy: every job is within budget"
