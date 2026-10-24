"""BudgetGate — daily/monthly caps and the per-actor hourly rate limit (#246).

Both collaborators are fakes, so the policy is exercised without Postgres.
"""

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from embry0.orchestration.budget_gate import BudgetGate

FIXED_NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)

DEFAULT_CONFIG = {
    "max_budget_per_job_usd": 10.0,
    "daily_cap_usd": 100.0,
    "monthly_cap_usd": 500.0,
    "rate_limit_per_author_per_hour": 5,
    "overrun_mode": "soft",
}


class FakeConfigRepo:
    def __init__(self, **over):
        self._row = {**DEFAULT_CONFIG, **over}

    async def get(self):
        return dict(self._row)


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


def _gate(config_repo=None, spend_repo=None):
    return BudgetGate(
        config_repo or FakeConfigRepo(),
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
    config = FakeConfigRepo(daily_cap_usd=0.0, monthly_cap_usd=0.0)
    assert (await _gate(config, repos).check()).allowed is True


@pytest.mark.asyncio
async def test_refuses_when_the_actor_hit_the_hourly_rate_limit():
    spend = FakeSpendRepo(actor_count=5)
    decision = await _gate(FakeConfigRepo(rate_limit_per_author_per_hour=5), spend).check(actor="alex")
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
    decision = await _gate(FakeConfigRepo(rate_limit_per_author_per_hour=5), spend).check(
        actor="alex", excluding="job-5"
    )
    assert decision.allowed is True, "four other jobs against a limit of five: the fifth runs"
    assert spend.excluded == ["job-5"]
    assert (await _gate(FakeConfigRepo(rate_limit_per_author_per_hour=5), spend).check(actor="alex")).allowed is True
    assert spend.excluded == ["job-5", None], "an ask before the job exists leaves nothing out"


@pytest.mark.asyncio
async def test_rate_limit_is_skipped_without_an_actor():
    spend = FakeSpendRepo(actor_count=99)
    assert (await _gate(spend_repo=spend).check()).allowed is True
    assert spend.actor_calls == []


@pytest.mark.asyncio
async def test_rate_limit_of_zero_is_disabled():
    spend = FakeSpendRepo(actor_count=99)
    config = FakeConfigRepo(rate_limit_per_author_per_hour=0)
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
    decision = await _gate(FakeConfigRepo(monthly_cap_usd=400.0), FakeSpendRepo(monthly=483.12)).check()
    assert "483.12" in decision.message  # the operator-facing detail is kept
    assert "$" not in decision.public_message
    assert decision.public_message == "dispatch refused: budget cap reached"


@pytest.mark.asyncio
async def test_the_public_rate_limit_message_names_no_actor():
    spend = FakeSpendRepo(actor_count=5)
    decision = await _gate(FakeConfigRepo(rate_limit_per_author_per_hour=5), spend).check(actor="alex")
    assert "alex" in decision.message
    assert "alex" not in decision.public_message
    assert decision.public_message == "dispatch refused: dispatch rate limit reached"


@pytest.mark.asyncio
async def test_an_allowed_decision_has_no_public_message():
    assert (await _gate().check()).public_message == ""
