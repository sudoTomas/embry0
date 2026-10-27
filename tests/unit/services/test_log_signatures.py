"""Normalizer + clustering for SH-P3.1 (#289).

The fixtures below MIRROR the shapes of a real 24 h error corpus pulled from
this deployment's Loki (743 lines, 592 distinct, 55 clusters) but are
synthesized, not copied: the real lines carry mailbox addresses and MS Graph
notification ids, and committing them would put production PII in the repo —
the exact exposure #294 is about. Every shape here was observed; every value is
invented.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embry0.services.log_signatures import (
    LogCluster,
    LogEntry,
    cluster_entries,
    cluster_stats,
    format_cluster_table,
    normalize_line,
    signature_id,
)

# --- normalization ----------------------------------------------------------


def test_postgres_timestamp_and_pid_are_masked():
    a = '2026-09-20 20:47:09.876 UTC [1913152] ERROR:  duplicate key value violates unique constraint "idx_x"'
    b = '2026-09-19 03:11:00.001 UTC [77] ERROR:  duplicate key value violates unique constraint "idx_x"'
    assert normalize_line(a) == normalize_line(b)
    assert "1913152" not in normalize_line(a)


def test_quoted_constraint_name_is_preserved():
    """The constraint name is the entire diagnostic value — masking it would
    collapse every unique-violation in the database into one signature."""
    a = '2026-09-20 20:47:09.876 UTC [1] ERROR:  duplicate key violates unique constraint "idx_email_external_id"'
    b = '2026-09-20 20:47:09.876 UTC [1] ERROR:  duplicate key violates unique constraint "idx_deal_thread_id"'
    assert normalize_line(a) != normalize_line(b)
    assert "idx_email_external_id" in normalize_line(a)


def test_nginx_worker_connection_and_ip_are_masked():
    a = "2026/09/21 01:44:53 [error] 30#30: *1 backend could not be resolved, client: 10.0.0.5, server: q.example"
    b = "2026/09/21 02:01:02 [error] 31#31: *998 backend could not be resolved, client: 172.16.4.9, server: q.example"
    assert normalize_line(a) == normalize_line(b)


def test_uuid_is_masked():
    a = "thread 62b03f73-4f78-4cb4-ad7b-40abfca95977 failed"
    b = "thread 11111111-2222-3333-4444-555555555555 failed"
    assert normalize_line(a) == normalize_line(b) == "thread <uuid> failed"


def test_opaque_base64_id_is_masked():
    """An MS Graph notification id: mixed case + digits, not hex, so only the
    opaque-token rule can catch it. This was the single biggest clustering miss
    on the real corpus — it split one fault into 7 signatures."""
    a = "ingest DROPPED: notification AAMkADAwMDBhYWFhLTExMTEtEx4mPl3AAA= failed"
    b = "ingest DROPPED: notification AAMkADk5OTliYmJiLTg4ODgtNzc3Qz7Rw2YbAAA= failed"
    assert normalize_line(a) == normalize_line(b)
    assert "<id>" in normalize_line(a)


def test_dotted_class_names_are_not_masked():
    """Long, but meaningful — and the opaque rule must not eat them."""
    line = "Caused by: java.lang.ClassNotFoundException: org.springframework.boot.security.oauth2.server.Resource1"
    out = normalize_line(line)
    assert "org.springframework.boot.security.oauth2.server" in out
    assert "<id>" not in out


def test_screaming_snake_constants_are_not_masked():
    out = normalize_line("rejected: MAX_CONNECTIONS_EXCEEDED_HARD_LIMIT")
    assert "MAX_CONNECTIONS_EXCEEDED_HARD_LIMIT" in out


def test_upper_case_record_ids_are_masked_and_codes_kept():
    """A recurring warning that names a different order number each time must
    stay one signature, not one per order."""
    a = normalize_line("WARN lookup fallback: invalid order id format: ORD00000000001.2")
    b = normalize_line("WARN lookup fallback: invalid order id format: ORD7A000000042.1")
    assert a == b == "WARN lookup fallback: invalid order id format: <id>.<n>"
    for code in ("E11000 duplicate key", "SHA256 mismatch", "PGRST002 schema cache", "HTTP2 stream reset"):
        assert normalize_line(code) == code, code


def test_email_addresses_are_masked():
    """Clustering: one fault per mailbox is one fault. Exposure: the ranked
    table a model reads must carry no addresses (#294)."""
    a = "MS Graph fetch failed for someone@example.com (12ms after webhook): NotFound"
    b = "MS Graph fetch failed for other.person+tag@example.com (4ms after webhook): NotFound"
    assert normalize_line(a) == normalize_line(b)
    assert "@" not in normalize_line(a)


def test_logback_left_truncated_thread_names_collapse():
    """Logback's `%.15t` truncates from the LEFT, so the same logger emits both
    "[MessageBroker-5]" and "[essageBroker-11]" — two signatures for one
    logger until the thread field is masked."""
    a = "DEBUG 1 --- [acme-backend] [MessageBroker-5] c.r.c.s.Reconciler : reconcile done"
    b = "DEBUG 1 --- [acme-backend] [essageBroker-11] c.r.c.s.Reconciler : reconcile done"
    assert normalize_line(a) == normalize_line(b)
    assert "[<thread>]" in normalize_line(a)


def test_app_name_bracket_is_preserved():
    """`[acme-backend]` carries no number, so it is not a thread slot and
    must survive — it is how a multi-service window is told apart."""
    out = normalize_line("DEBUG 1 --- [acme-backend] [MessageBroker-5] c.r.Foo : x")
    assert "[acme-backend]" in out


def test_numbers_durations_and_hex_are_masked():
    a = "listAttachments failed after 1523ms, id deadbeefcafe1234, retries 3"
    b = "listAttachments failed after 7ms, id 0123456789abcdef, retries 11"
    assert normalize_line(a) == normalize_line(b)


def test_normalization_is_idempotent_and_deterministic():
    line = "2026-09-20 20:47:09.876 UTC [42] ERROR: thread 62b03f73-4f78-4cb4-ad7b-40abfca95977 after 5ms"
    once = normalize_line(line)
    assert normalize_line(line) == once
    # Re-normalizing a template must not chew through its own placeholders.
    assert normalize_line(once) == once


def test_blank_and_non_string_lines_are_safe():
    assert normalize_line("") == ""
    assert normalize_line("   \t ") == ""
    assert normalize_line(None) == ""  # type: ignore[arg-type]


# --- signature id -----------------------------------------------------------


def test_signature_id_is_stable_across_processes():
    """Pinned literal: a salted hash (Python's `hash`) would change per run and
    silently orphan every persisted signature after a restart."""
    assert signature_id("a b c") == signature_id("a b c")
    assert signature_id("a b c") != signature_id("a b d")
    assert len(signature_id("x")) == 16
    # Pinned literal. If this ever changes, every persisted signature id is
    # orphaned — so the change must be deliberate and migrated, not incidental.
    assert signature_id("ERROR: boom") == "3e2a17f63ca730bf"


def test_same_line_shapes_share_one_signature_id():
    a = normalize_line("2026-09-20 01:00:00 UTC [1] ERROR: boom 5")
    b = normalize_line("2026-09-21 02:00:00 UTC [999] ERROR: boom 12")
    assert signature_id(a) == signature_id(b)


# --- clustering -------------------------------------------------------------


def _entry(line: str, *, offset: int = 0, service: str | None = None) -> LogEntry:
    return LogEntry(
        line=line, timestamp=datetime(2026, 9, 20, 12, 0, tzinfo=UTC) + timedelta(seconds=offset), service=service
    )


def test_clusters_are_ranked_by_count_descending():
    entries = ["ERROR boom 1"] * 5 + ["WARN other 2"] * 3 + ["INFO rare 3"]
    clusters = cluster_entries(entries)
    assert [c.count for c in clusters] == [5, 3, 1]


def test_ranking_is_total_so_the_order_is_reproducible():
    """Equal counts must break on template, or a shadow comparison reads an
    unstable sort as disagreement."""
    first = cluster_entries(["a err 1", "b err 2"])
    second = cluster_entries(["b err 2", "a err 1"])
    assert [c.template for c in first] == [c.template for c in second]


def test_samples_are_capped_and_keep_the_raw_line():
    clusters = cluster_entries([f"ERROR boom {i}" for i in range(10)], samples_per_cluster=2)
    assert len(clusters) == 1
    assert len(clusters[0].samples) == 2
    # Samples are the untouched originals — the template is the masked form.
    assert clusters[0].samples[0] == "ERROR boom 0"
    assert "<n>" in clusters[0].template


def test_services_are_collected_without_duplicates():
    clusters = cluster_entries(
        [
            _entry("ERROR boom 1", service="api"),
            _entry("ERROR boom 2", service="api"),
            _entry("ERROR boom 3", service="ml"),
        ]
    )
    assert sorted(clusters[0].services) == ["api", "ml"]


def test_first_and_last_seen_span_the_cluster():
    clusters = cluster_entries([_entry("ERROR boom 1", offset=0), _entry("ERROR boom 2", offset=120)])
    assert clusters[0].span_seconds == 120
    assert clusters[0].rate_per_hour == 60.0  # 2 in 120s


def test_rate_is_none_for_an_instantaneous_or_single_line_cluster():
    """Dividing a 1-second burst by the window would understate a spike by
    orders of magnitude; no rate is more honest than a wrong one."""
    assert cluster_entries([_entry("ERROR once", offset=0)])[0].rate_per_hour is None
    same_instant = cluster_entries([_entry("ERROR x 1", offset=5), _entry("ERROR x 2", offset=5)])
    assert same_instant[0].rate_per_hour is None


def test_blank_entries_are_dropped_not_clustered():
    clusters = cluster_entries(["", "   ", "ERROR real 1"])
    assert len(clusters) == 1
    assert cluster_stats(clusters) == {"clusters": 1, "lines": 1, "singletons": 1}


def test_a_volume_spike_cannot_crowd_out_a_rare_signature():
    """The whole point of #289. 5,000 spike lines plus one rare fault: the rare
    one must still appear in a 25-row table, which it never did in a 120-line
    raw tail."""
    entries = [f"DNS resolve failed for host-{i}" for i in range(5000)]
    entries.append("ERROR LazyInitializationException on Company")
    table = format_cluster_table(cluster_entries(entries), max_clusters=25)
    assert "LazyInitializationException" in table


def test_real_corpus_shapes_compress_by_an_order_of_magnitude():
    """Mirrors the measured result: many near-identical lines differing only in
    mailbox, notification id, pid, thread and duration are one fault each."""
    lines: list[str] = []
    for i in range(40):
        lines.append(
            f"2026-09-20 20:{i % 60:02d}:09.876 UTC [{190000 + i}] ERROR:  duplicate key value "
            f'violates unique constraint "idx_email_external_id"'
        )
        lines.append(
            f"WARN {1000 + i} --- [acme-backend] [MessageBroker-{i}] c.e.m.i.MsGraphFetcher : "
            f"listAttachments failed for user{i}@example.com AAMkADAwMDBhYWFhLTEx{i}Ex4mPl3AAA= "
            f"after {i * 7}ms: NotFound"
        )
    clusters = cluster_entries(lines)
    assert len(lines) == 80
    assert len(clusters) == 2, [c.template for c in clusters]
    assert {c.count for c in clusters} == {40}


# --- prompt table -----------------------------------------------------------


def test_table_states_the_omitted_tail_rather_than_dropping_it():
    """ "There were 40 more signatures" is itself a finding."""
    entries = [f"ERROR distinct_fault_{i}_here" for i in range(30)]
    table = format_cluster_table(cluster_entries(entries), max_clusters=5)
    assert "and 25 more signature(s)" in table
    assert "25 line(s)" in table


def test_table_shows_counts_signature_ids_and_samples():
    table = format_cluster_table(cluster_entries(["ERROR boom 1", "ERROR boom 2"]))
    assert "[2×]" in table
    assert "sig=" in table
    assert "template:" in table
    assert "sample:" in table


def test_empty_table_says_so():
    assert format_cluster_table([]) == "(no matching log lines in this window)"


def test_long_samples_are_trimmed():
    long_line = "ERROR " + "x" * 500
    table = format_cluster_table(cluster_entries([long_line]), max_sample_chars=50)
    sample_lines = [ln for ln in table.splitlines() if ln.strip().startswith("sample:")]
    assert sample_lines, table
    assert all(ln.endswith("…") and len(ln) < 120 for ln in sample_lines), sample_lines
    # The template has its own, larger cap and is not governed by max_sample_chars.
    template_line = next(ln for ln in table.splitlines() if "template:" in ln)
    assert len(template_line) <= 360


def test_cluster_dataclass_span_is_zero_without_timestamps():
    c = LogCluster(signature_id="s", template="t", count=1)
    assert c.span_seconds == 0.0
    assert c.rate_per_hour is None
