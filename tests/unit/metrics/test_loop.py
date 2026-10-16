"""LoopMetrics — one method per event, on a registry of the caller's choosing (#285)."""

from datetime import UTC, datetime

from prometheus_client import CollectorRegistry, generate_latest

from embry0.metrics import LoopMetrics
from embry0.metrics.loop import WATCHER_HEALTH_STATES

FORBIDDEN_LABELS = {
    "job_id",
    "case_id",
    "ticket",
    "identifier",
    "tracker_key",
    "pr_url",
    "url",
    "error_message",
    "image",
}


def _metrics() -> tuple[LoopMetrics, CollectorRegistry]:
    registry = CollectorRegistry()
    return LoopMetrics(registry), registry


def test_two_instances_do_not_share_state() -> None:
    a, reg_a = _metrics()
    _, reg_b = _metrics()
    a.watcher_tick("no_issue", analysed=True)
    assert reg_a.get_sample_value("embry0_watcher_ticks_total", {"stack": "", "action": "no_issue"}) == 1
    assert reg_b.get_sample_value("embry0_watcher_ticks_total", {"stack": "", "action": "no_issue"}) is None
    LoopMetrics().watcher_tick("error", analysed=False)  # the private default registry works too


def test_no_metric_is_labelled_by_an_identifier() -> None:
    _, registry = _metrics()
    for family in registry.collect():
        for s in family.samples:
            assert not FORBIDDEN_LABELS & set(s.labels), family.name


def test_every_metric_is_prefixed_and_counters_end_in_total() -> None:
    m, registry = _metrics()
    m.watcher_tick("no_issue", analysed=True)
    for family in registry.collect():
        assert family.name.startswith("embry0_"), family.name
        if family.type == "counter":
            assert all(s.name.endswith(("_total", "_created")) for s in family.samples), family.name


def test_watcher_tick_moves_the_analysed_timestamp_only_on_a_verdict() -> None:
    m, registry = _metrics()
    first = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    later = datetime(2026, 9, 20, 11, 0, tzinfo=UTC)
    m.watcher_tick("no_issue", analysed=True, analysis_cost_usd=0.42, at=first)
    m.watcher_tick("error", analysed=False, analysis_cost_usd=0.10, at=later)
    assert registry.get_sample_value("embry0_watcher_last_tick_timestamp_seconds", {"stack": ""}) == later.timestamp()
    assert (
        registry.get_sample_value("embry0_watcher_last_analysed_tick_timestamp_seconds", {"stack": ""})
        == first.timestamp()
    )
    assert registry.get_sample_value("embry0_watcher_analysis_cost_usd_total") == 0.52
    assert registry.get_sample_value("embry0_watcher_ticks_total", {"stack": "", "action": "error"}) == 1


def test_watcher_window_diagnostics() -> None:
    m, registry = _metrics()
    m.watcher_tick("skipped_quiet", analysed=False, unanalysed_seconds=600)
    assert registry.get_sample_value("embry0_watcher_unanalysed_seconds") == 600
    assert registry.get_sample_value("embry0_watcher_log_seconds_lost_total") == 0  # pending, not lost yet
    m.watcher_tick("no_issue", analysed=True, unanalysed_seconds=900, log_lines_truncated=True)
    assert registry.get_sample_value("embry0_watcher_log_seconds_lost_total") == 900
    assert registry.get_sample_value("embry0_watcher_ticks_log_truncated_total") == 1
    m.watcher_tick("no_issue", analysed=True)
    assert registry.get_sample_value("embry0_watcher_unanalysed_seconds") == 0


def test_an_unknown_watcher_action_cannot_mint_a_series() -> None:
    m, registry = _metrics()
    m.watcher_tick("job-abc123 exploded", analysed=False)
    assert registry.get_sample_value("embry0_watcher_ticks_total", {"stack": "", "action": "other"}) == 1


def test_watcher_health_is_a_state_set() -> None:
    m, registry = _metrics()
    m.watcher_health("invalid_logql")
    values = {
        s: registry.get_sample_value("embry0_watcher_health", {"state": s, "stack": ""}) for s in WATCHER_HEALTH_STATES
    }
    assert values == {"ok": 0, "unverified": 0, "invalid_logql": 1, "missing_logql": 0, "loki_misconfigured": 0}
    m.watcher_health("ok")
    assert registry.get_sample_value("embry0_watcher_health", {"state": "invalid_logql", "stack": ""}) == 0
    assert registry.get_sample_value("embry0_watcher_health", {"state": "ok", "stack": ""}) == 1


def test_seeding_restores_tick_times_and_never_moves_them_backwards() -> None:
    m, registry = _metrics()
    # #378: labelled per stack, so a stack that has never ticked has no series.
    assert registry.get_sample_value("embry0_watcher_last_analysed_tick_timestamp_seconds", {"stack": ""}) is None
    seeded = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)
    m.seed_watcher_ticks(last_tick_at=seeded, last_analysed_at=None, last_verdict_at=None)
    assert registry.get_sample_value("embry0_watcher_last_tick_timestamp_seconds", {"stack": ""}) == seeded.timestamp()
    # Seeding touched the series, so it exists at 0 — which the dead-man rule's
    # `> 0` guard is there for.
    assert registry.get_sample_value("embry0_watcher_last_analysed_tick_timestamp_seconds", {"stack": ""}) == 0
    assert registry.get_sample_value("embry0_watcher_last_verdict_tick_timestamp_seconds", {"stack": ""}) == 0

    # A quiet night: the last verdict is newer than the last analysis, and the
    # seed must say so or the dead-man re-arms on every deploy.
    quiet = datetime(2026, 9, 20, 11, 0, tzinfo=UTC)
    m.seed_watcher_ticks(last_tick_at=quiet, last_analysed_at=seeded, last_verdict_at=quiet)
    assert (
        registry.get_sample_value("embry0_watcher_last_analysed_tick_timestamp_seconds", {"stack": ""})
        == seeded.timestamp()
    )
    assert (
        registry.get_sample_value("embry0_watcher_last_verdict_tick_timestamp_seconds", {"stack": ""})
        == quiet.timestamp()
    )

    live = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    m.watcher_tick("no_issue", analysed=True, at=live)
    m.seed_watcher_ticks(last_tick_at=seeded, last_analysed_at=seeded, last_verdict_at=seeded)
    assert registry.get_sample_value("embry0_watcher_last_tick_timestamp_seconds", {"stack": ""}) == live.timestamp()
    assert (
        registry.get_sample_value("embry0_watcher_last_analysed_tick_timestamp_seconds", {"stack": ""})
        == live.timestamp()
    )
    assert (
        registry.get_sample_value("embry0_watcher_last_verdict_tick_timestamp_seconds", {"stack": ""})
        == live.timestamp()
    )


def test_job_finished_counts_and_times() -> None:
    m, registry = _metrics()
    m.job_finished(pipeline_template="issue-to-pr", status="failed", error_code="ERR_MAX_RETRIES", duration_seconds=90)
    m.job_finished(pipeline_template=None, status="completed")
    labels = {
        "pipeline_template": "issue-to-pr",
        "status": "failed",
        "error_code": "ERR_MAX_RETRIES",
        "error_category": "uncategorised",
    }
    assert registry.get_sample_value("embry0_jobs_finished_total", labels) == 1
    assert (
        registry.get_sample_value(
            "embry0_jobs_finished_total",
            {
                "pipeline_template": "unassigned",
                "status": "completed",
                "error_code": "none",
                # No categoriser in this build; with #340 merged this reads
                # `unknown`, the taxonomy's own value for a missing code.
                "error_category": "uncategorised",
            },
        )
        == 1
    )
    assert (
        registry.get_sample_value(
            "embry0_job_duration_seconds_count", {"pipeline_template": "issue-to-pr", "status": "failed"}
        )
        == 1
    )


def test_pipeline_template_label_is_capped() -> None:
    m, registry = _metrics()
    for i in range(100):
        m.job_finished(pipeline_template=f"user-template-{i}", status="completed")
    pipelines = {
        s.labels["pipeline_template"]
        for family in registry.collect()
        if family.name == "embry0_jobs_finished"
        for s in family.samples
    }
    assert len(pipelines) <= 34 and "other" in pipelines


def test_agent_spend_by_pipeline_provider_and_family() -> None:
    m, registry = _metrics()
    m.agent_spend(pipeline_template="analysis-default", model="claude-opus-5", cost_usd=1.25)
    m.agent_spend(pipeline_template="analysis-default", model="claude-opus-5", cost_usd=0.75)
    m.agent_spend(pipeline_template=None, model="grok-4.5", cost_usd=0.5)
    m.agent_spend(pipeline_template="x", model="claude-opus-5", cost_usd=0.0)  # nothing spent, nothing minted
    assert (
        registry.get_sample_value(
            "embry0_agent_spend_usd_total",
            {"pipeline_template": "analysis-default", "provider": "anthropic", "model_family": "claude-opus"},
        )
        == 2.0
    )
    assert (
        registry.get_sample_value(
            "embry0_agent_spend_usd_total",
            {"pipeline_template": "unassigned", "provider": "xai", "model_family": "grok"},
        )
        == 0.5
    )
    assert b'pipeline_template="x"' not in generate_latest(registry)


def test_case_and_infra_events() -> None:
    m, registry = _metrics()
    m.case_opened("watcher")
    m.case_event("merged", "applied")
    m.case_event("ENG-1988", "applied")
    m.case_resolved("out_of_band")
    m.sandbox_runner_stale_image("argv_fallback")
    m.state_channel_fold("pr_url", "PreferNonEmpty")
    m.volume_reaper_volume("removed", "owner_terminal")
    m.volume_reaper_volume("kept", "sandbox-job-123")
    m.volume_reaper_sweep("ok")
    get = registry.get_sample_value
    assert get("embry0_healing_cases_opened_total", {"origin": "watcher"}) == 1
    assert get("embry0_healing_case_events_total", {"event": "merged", "effect": "applied"}) == 1
    assert get("embry0_healing_case_events_total", {"event": "other", "effect": "applied"}) == 1
    assert get("embry0_healing_cases_resolved_total", {"resolution_path": "out_of_band"}) == 1
    assert get("embry0_sandbox_runner_stale_image_total", {"outcome": "argv_fallback"}) == 1
    folds = "embry0_state_channel_folded_concurrent_writes_total"
    assert get(folds, {"key": "pr_url", "channel": "PreferNonEmpty"}) == 1
    assert get("embry0_volume_reaper_volumes_total", {"outcome": "removed", "reason": "owner_terminal"}) == 1
    assert get("embry0_volume_reaper_volumes_total", {"outcome": "kept", "reason": "other"}) == 1
    assert get("embry0_volume_reaper_sweeps_total", {"result": "ok"}) == 1


def test_reconciler_sweep_publishes_the_last_sweeps_counts() -> None:
    m, registry = _metrics()
    m.reconciler_configured(enabled=True, max_dispatches_per_day=5)
    m.reconciler_decision("would_dispatch", "untracked")
    m.reconciler_decision("skipped", "dedupe:active_job")
    m.reconciler_sweep(mode="shadow", result="ok", tickets={"listed": 2, "would_dispatch": 1, "skipped": 1})
    m.reconciler_sweep(mode="shadow", result="ok", tickets={"listed": 1, "would_dispatch": 0, "skipped": 1})
    get = registry.get_sample_value
    assert get("embry0_linear_reconciler_daily_dispatch_cap") == 5
    assert get("embry0_linear_reconciler_enabled") == 1
    assert get("embry0_linear_reconciler_sweeps_total", {"mode": "shadow", "result": "ok"}) == 2
    assert get("embry0_linear_reconciler_decisions_total", {"decision": "would_dispatch", "reason": "untracked"}) == 1
    assert get("embry0_linear_reconciler_last_sweep_tickets", {"decision": "would_dispatch"}) == 0
    assert get("embry0_linear_reconciler_last_sweep_timestamp_seconds") > 0


def test_the_null_facade_answers_every_event_method_and_records_nothing() -> None:
    """The default for a producer built without metrics. A method added to
    LoopMetrics must work on it too — it is what runs in every unit test of
    every producer, and in production if install_metrics ever fails."""
    from embry0.metrics import NULL_METRICS

    events = [
        name
        for name, value in vars(LoopMetrics).items()
        if callable(value) and not name.startswith("_") and name not in {"registry"}
    ]
    assert len(events) >= 14
    for name in events:
        assert getattr(NULL_METRICS, name)(*[], **{}) is None  # accepts any call shape
    from prometheus_client import REGISTRY

    before = len(list(REGISTRY.collect()))
    NULL_METRICS.watcher_tick("no_issue", analysed=True, analysis_cost_usd=1.0)
    NULL_METRICS.job_finished(pipeline_template="p", status="failed", error_code="ERR_UNKNOWN")
    # It owns no registry, so it cannot leak series into the process-global one.
    assert len(list(REGISTRY.collect())) == before


def test_an_event_method_never_raises() -> None:
    m, _ = _metrics()
    m.job_finished(pipeline_template="p", status="failed", duration_seconds="not-a-number")  # type: ignore[arg-type]
    m.watcher_tick("no_issue", analysed=True, at="yesterday")  # type: ignore[arg-type]


def test_a_quiet_tick_is_a_verdict_but_not_an_analysis() -> None:
    # 2026-09-27: a stack that logs nothing for a night is the loop detecting
    # nothing, not the loop failing to detect. The dead-man gauge must move on
    # skipped_quiet — and must NOT on the ticks that still reach no verdict.
    m, registry = _metrics()
    first = datetime(2026, 9, 27, 1, 0, tzinfo=UTC)
    quiet = datetime(2026, 9, 27, 2, 0, tzinfo=UTC)
    later = datetime(2026, 9, 27, 3, 0, tzinfo=UTC)
    m.watcher_tick("no_issue", analysed=True, at=first)
    m.watcher_tick("skipped_quiet", analysed=False, unanalysed_seconds=600, at=quiet)
    get = registry.get_sample_value
    assert get("embry0_watcher_last_verdict_tick_timestamp_seconds", {"stack": ""}) == quiet.timestamp(), (
        "a quiet tick must advance the verdict timestamp"
    )
    # ... while the analysed gauge and the lost-log counter stay put: nothing was consumed.
    assert get("embry0_watcher_last_analysed_tick_timestamp_seconds", {"stack": ""}) == first.timestamp()
    assert get("embry0_watcher_log_seconds_lost_total") in (None, 0)

    for action in ("skipped_backlog", "skipped_analysis_cap", "skipped_unparseable", "error"):
        m.watcher_tick(action, analysed=False, at=later)
    assert get("embry0_watcher_last_verdict_tick_timestamp_seconds", {"stack": ""}) == quiet.timestamp()
    assert get("embry0_watcher_last_analysed_tick_timestamp_seconds", {"stack": ""}) == first.timestamp()
    assert get("embry0_watcher_last_tick_timestamp_seconds", {"stack": ""}) == later.timestamp()


def test_job_event_persist_failed_counts() -> None:
    m, registry = _metrics()
    m.job_event_persist_failed()
    m.job_event_persist_failed()
    assert registry.get_sample_value("embry0_job_events_persist_failed_total") == 2


def test_job_event_dropped_counts() -> None:
    m, registry = _metrics()
    m.job_event_dropped()
    assert registry.get_sample_value("embry0_job_events_dropped_total") == 1, (
        "an event dropped because the scrub raised must be visible in Prometheus (review issue 8)"
    )
