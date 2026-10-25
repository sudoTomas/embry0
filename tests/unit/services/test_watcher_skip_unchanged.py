"""Skip the paid analysis when nothing in the window changed (#291, SH-P3.3).

Every tick ran an LLM job even when the logs held nothing new: ~24 jobs/day at
~$0.44-0.55, about $13/day, and 258 of those ticks came back `no_issue`.

The decision has to fail toward *spending*: a window wrongly skipped is a fault
nobody looks at, which costs far more than one analysis. Several tests below
exist only to pin that direction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.storage.repositories.log_signatures import SignatureRow
from embry0.storage.repositories.watcher_runs import ANALYSED_ACTIONS, WATCHER_ACTIONS
from tests.unit.services.test_watcher import _config, _service

# 30 lines that all normalise to one template — one signature, count 30.
_LINES = [f"ERROR DNS resolve failed for host-{i}" for i in range(30)]
WINDOW_START = datetime(2026, 9, 21, 11, 0, tzinfo=UTC)


def _row(sig: str, *, last_window_count: int, last_seen_at: datetime | None = None) -> SignatureRow:
    # Anchored on the real clock, because run_once derives its window_start
    # from `now`. A fixed date here would sit in the future relative to the
    # window, silence would clamp to zero, and "steady" would be an accident of
    # the calendar rather than of the counts.
    seen = last_seen_at or (datetime.now(UTC) - timedelta(minutes=30))
    return SignatureRow(
        signature_id=sig,
        repo="owner/repo",
        template="t",
        first_seen_at=seen - timedelta(days=2),
        last_seen_at=seen,
        occurrence_count=500,
        ticks_seen=20,
        last_window_count=last_window_count,
        services=["svc"],
        sample_lines=["l"],
    )


def _svc(mode: str, *, prior: dict[str, SignatureRow] | None = None, open_ids: set[str] | None = None, **cfg):
    svc = _service(
        config=_config(log_clustering_mode="on", watcher_skip_unchanged_mode=mode, watcher_repo="owner/repo", **cfg),
        lines=_LINES,
        job_result={"job_id": "job-s1", "status": "completed", "result_summary": "NO_ISSUE"},
    )
    svc._signatures = MagicMock(
        rows_for=AsyncMock(return_value=prior if prior is not None else {}),
        record_window=AsyncMock(return_value=0),
    )
    svc._cases = MagicMock(open_signature_ids=AsyncMock(return_value=open_ids or set()))
    svc._recorder = MagicMock()
    return svc


async def _prior_matching(svc, *, count: int) -> dict[str, SignatureRow]:
    """The stored row for whatever signature this window actually produces.

    Computed from the module's own normaliser rather than a hard-coded hash, so
    a change to the clustering rules cannot leave this test pinning a signature
    that no longer occurs — the test would still pass while measuring nothing.
    """
    from embry0.services.log_signatures import cluster_entries

    entries = await svc._fetch_log_entries(WINDOW_START, WINDOW_START + timedelta(hours=1))
    clusters = cluster_entries(entries)
    assert clusters, "the fixture produced no clusters; this test would measure nothing"
    return {clusters[0].signature_id: _row(clusters[0].signature_id, last_window_count=count)}


# ---- the vocabulary --------------------------------------------------------


def test_no_change_is_a_recordable_action() -> None:
    assert "no_change" in WATCHER_ACTIONS


def test_no_change_consumes_its_window() -> None:
    """It must advance the query window and refresh the dead-man gauge.

    If it did not advance the window, every quiet tick would widen it, the
    counts would grow mechanically, and the spike test would then read that as
    a spike — paying for exactly the analysis this issue avoids.
    """
    assert "no_change" in ANALYSED_ACTIONS


# ---- the gate --------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unchanged_window_is_recorded_as_no_change_and_runs_no_job() -> None:
    svc = _svc("on")
    prior = await _prior_matching(svc, count=28)  # 30 now vs 28 before: steady
    svc._signatures.rows_for = AsyncMock(return_value=prior)

    out = await svc.run_once()

    assert out["action"] == "no_change", out
    svc._jobs.create.assert_not_awaited()
    assert out.get("detail") is None or "no new" in str(out.get("detail", ""))


@pytest.mark.asyncio
async def test_a_new_signature_is_always_analysed() -> None:
    svc = _svc("on", prior={})  # nothing on record: the signature is new
    out = await svc.run_once()
    assert out["action"] == "no_issue", out
    svc._jobs.create.assert_awaited()


@pytest.mark.asyncio
async def test_a_spiking_signature_is_analysed() -> None:
    svc = _svc("on")
    prior = await _prior_matching(svc, count=2)  # 30 now vs 2 before
    svc._signatures.rows_for = AsyncMock(return_value=prior)

    out = await svc.run_once()
    assert out["action"] == "no_issue", "a 15x spike was skipped"
    svc._jobs.create.assert_awaited()


@pytest.mark.asyncio
async def test_a_signature_returning_after_a_long_silence_is_analysed() -> None:
    svc = _svc("on")
    from embry0.services.log_signatures import cluster_entries

    entries = await svc._fetch_log_entries(WINDOW_START, WINDOW_START + timedelta(hours=1))
    sig = cluster_entries(entries)[0].signature_id
    # Silent for well over the six-hour regression threshold. window_start is
    # `now` minus an interval inside run_once, so anchor on the real clock.
    quiet_since = datetime.now(UTC) - timedelta(days=3)
    svc._signatures.rows_for = AsyncMock(return_value={sig: _row(sig, last_window_count=30, last_seen_at=quiet_since)})

    out = await svc.run_once()
    assert out["action"] == "no_issue", "a regressed signature was skipped"
    svc._jobs.create.assert_awaited()


# ---- the ladder ------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_computes_the_decision_but_still_analyses() -> None:
    svc = _svc("shadow")
    svc._signatures.rows_for = AsyncMock(return_value=await _prior_matching(svc, count=28))

    out = await svc.run_once()
    assert out["action"] == "no_issue", "shadow mode must not skip anything yet"
    svc._jobs.create.assert_awaited()


@pytest.mark.asyncio
async def test_off_never_skips() -> None:
    svc = _svc("off")
    svc._signatures.rows_for = AsyncMock(return_value=await _prior_matching(svc, count=28))

    out = await svc.run_once()
    assert out["action"] == "no_issue"
    svc._jobs.create.assert_awaited()


@pytest.mark.asyncio
async def test_an_unrecognised_mode_reads_as_off() -> None:
    """A typo in the environment must not switch the watcher to not looking."""
    svc = _svc("ON!!")
    svc._signatures.rows_for = AsyncMock(return_value=await _prior_matching(svc, count=28))

    out = await svc.run_once()
    assert out["action"] == "no_issue"
    svc._jobs.create.assert_awaited()


# ---- notable signatures that are already held ------------------------------


@pytest.mark.asyncio
async def test_a_notable_signature_already_open_as_a_case_is_not_re_analysed() -> None:
    """The tick right after a fault is filed: the signature is new to
    log_signatures but the finding is already in the ledger, so paying a model
    to re-describe it buys nothing — #290's dedupe would discard the proposal
    afterwards, having paid for it."""
    svc = _svc("on", prior={})
    from embry0.services.log_signatures import cluster_entries

    entries = await svc._fetch_log_entries(WINDOW_START, WINDOW_START + timedelta(hours=1))
    sig = cluster_entries(entries)[0].signature_id
    svc._cases.open_signature_ids = AsyncMock(return_value={sig})

    out = await svc.run_once()
    assert out["action"] == "no_change", out
    svc._jobs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_unheld_notable_signature_is_enough_to_pay_for_the_window() -> None:
    """The over-skipping guard: a window holding a known fault AND a new one
    must still be analysed."""
    svc = _svc("on", prior={}, open_ids={"some-other-signature"})

    out = await svc.run_once()
    assert out["action"] == "no_issue", "a new signature was skipped because an unrelated case was open"
    svc._jobs.create.assert_awaited()


@pytest.mark.asyncio
async def test_an_unreadable_ledger_analyses_rather_than_skips() -> None:
    """A ledger that cannot be read is not evidence that nothing changed.
    Fail toward spending: the expensive direction is the safe one."""
    svc = _svc("on", prior={})
    svc._cases.open_signature_ids = AsyncMock(side_effect=RuntimeError("db gone"))

    out = await svc.run_once()
    assert out["action"] == "no_issue"
    svc._jobs.create.assert_awaited()


@pytest.mark.asyncio
async def test_no_signature_memory_at_all_analyses() -> None:
    """A deployment with clustering off, or before anything was recorded, must
    behave exactly as it did before #291."""
    svc = _svc("on")
    svc._signatures = None

    out = await svc.run_once()
    assert out["action"] == "no_issue"
    svc._jobs.create.assert_awaited()


# ---- the saving ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_skipped_tick_does_not_assemble_the_briefing() -> None:
    """The briefing costs a Linear round trip, a GitHub round trip and a second
    Loki query. A tick that is about to skip should pay for none of them."""
    svc = _svc("on")
    svc._signatures.rows_for = AsyncMock(return_value=await _prior_matching(svc, count=28))
    svc._assemble_briefing_sections = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("the briefing was assembled for a tick that skipped")
    )

    out = await svc.run_once()
    assert out["action"] == "no_change"


@pytest.mark.asyncio
async def test_thresholds_come_from_configuration() -> None:
    """`watcher_spike_factor` etc. are read, not hard-coded — #297 moves them
    into the per-repo DB config and this is what proves they are wired."""
    svc = _svc("on", watcher_spike_factor=1.05, watcher_spike_min_count=1)
    # 30 now vs 28 before is steady at the 3x default, a spike at 1.05x.
    svc._signatures.rows_for = AsyncMock(return_value=await _prior_matching(svc, count=28))

    out = await svc.run_once()
    assert out["action"] == "no_issue", "a lowered spike factor did not reach classify_window"


def test_config_exposes_the_thresholds() -> None:
    from embry0.config import Embry0Config

    config = Embry0Config(_env_file=None, database_url="postgresql://unused")
    assert config.watcher_skip_unchanged_mode == "shadow", "#291 must ship in shadow, like every other new decider"
    assert config.watcher_spike_factor == 3.0
    assert config.watcher_spike_min_count == 10
    assert config.watcher_regression_silence_seconds == 21600


def test_service_reads_the_mode_from_a_plain_namespace() -> None:
    from embry0.services.watcher import WatcherService

    svc = WatcherService.__new__(WatcherService)
    svc._config = SimpleNamespace(watcher_skip_unchanged_mode="on")
    assert svc._skip_unchanged_mode() == "on"
    svc._config = SimpleNamespace()
    assert svc._skip_unchanged_mode() == "shadow"


# ---- signatures an analysis found clean a moment ago (spec 2026-10-25) -----


def _regressed(svc_sig: str) -> dict[str, SignatureRow]:
    """A known signature back after more than the regression silence: notable on its own."""
    return {svc_sig: _row(svc_sig, last_window_count=30, last_seen_at=datetime.now(UTC) - timedelta(hours=8))}


async def _window_signature(svc) -> str:
    from embry0.services.log_signatures import cluster_entries

    entries = await svc._fetch_log_entries(WINDOW_START, WINDOW_START + timedelta(hours=1))
    return cluster_entries(entries)[0].signature_id


def test_skipped_known_is_recordable_and_consumes_its_window() -> None:
    assert "skipped_known" in WATCHER_ACTIONS and "skipped_known" in ANALYSED_ACTIONS


@pytest.mark.asyncio
async def test_a_returning_signature_found_clean_lately_is_not_paid_for_again() -> None:
    """A known warning that comes and goes reads as regressed on each return; an
    analysis that found nothing in it within the hours holds it."""
    svc = _svc("on")
    sig = await _window_signature(svc)
    svc._signatures.rows_for = AsyncMock(return_value=_regressed(sig))
    svc._signatures.clean_since = AsyncMock(return_value={sig: 30})

    out = await svc.run_once()
    assert out["action"] == "skipped_known", out
    svc._jobs.create.assert_not_awaited()
    since = svc._signatures.clean_since.await_args.kwargs["since"]
    assert timedelta(hours=23) < datetime.now(UTC) - since < timedelta(hours=25), "24 h is the default"


@pytest.mark.asyncio
async def test_a_clean_signature_that_grew_past_the_spike_factor_is_analysed() -> None:
    """The analysis read it at 5 lines; 30 now is more than 3x that: look again."""
    svc = _svc("on")
    sig = await _window_signature(svc)
    svc._signatures.rows_for = AsyncMock(return_value=_regressed(sig))
    svc._signatures.clean_since = AsyncMock(return_value={sig: 5})
    svc._signatures.mark_analysed = AsyncMock()

    out = await svc.run_once()
    assert out["action"] == "no_issue"
    svc._jobs.create.assert_awaited()


@pytest.mark.asyncio
async def test_an_unreadable_analysis_memory_analyses_and_zero_hours_turns_it_off() -> None:
    for svc_cfg, clean in (
        ({}, AsyncMock(side_effect=RuntimeError("db gone"))),
        ({"watcher_skip_known_hours": 0}, None),
    ):
        svc = _svc("on", **svc_cfg)
        sig = await _window_signature(svc)
        svc._signatures.rows_for = AsyncMock(return_value=_regressed(sig))
        svc._signatures.clean_since = clean or AsyncMock(return_value={sig: 30})
        svc._signatures.mark_analysed = AsyncMock()

        out = await svc.run_once()
        assert out["action"] == "no_issue", svc_cfg
        if clean is None:
            svc._signatures.clean_since.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_analysis_remembers_what_it_found_in_each_signature() -> None:
    svc = _svc("on")
    sig = await _window_signature(svc)
    svc._signatures.rows_for = AsyncMock(return_value=_regressed(sig))
    svc._signatures.clean_since = AsyncMock(return_value={})
    svc._signatures.mark_analysed = AsyncMock()

    out = await svc.run_once()
    assert out["action"] == "no_issue"
    call = svc._signatures.mark_analysed.await_args
    assert call.args[0] == {sig: len(_LINES)}
    assert call.kwargs["verdict"] == "no_issue" and call.kwargs["repo"] == "owner/repo"


@pytest.mark.asyncio
async def test_in_shadow_a_known_window_is_logged_and_still_analysed() -> None:
    svc = _svc("shadow")
    sig = await _window_signature(svc)
    svc._signatures.rows_for = AsyncMock(return_value=_regressed(sig))
    svc._signatures.clean_since = AsyncMock(return_value={sig: 30})
    svc._signatures.mark_analysed = AsyncMock()

    out = await svc.run_once()
    assert out["action"] == "no_issue"
    svc._jobs.create.assert_awaited()
