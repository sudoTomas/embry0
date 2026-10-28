"""Which log signature a watcher finding is about (#657) — the pure half."""

from __future__ import annotations

from embry0.services.healing.lifecycle import SignatureSource
from embry0.services.log_signatures import LogCluster, LogEntry, cluster_entries, normalize_line, signature_id
from embry0.services.signature_attribution import (
    CUT_LINE_COVERAGE,
    CUT_LINE_PRECISION,
    MIN_SHARED_WORDS,
    SIMILARITY_THRESHOLD,
    AnchorMatch,
    _Vocabulary,
    anchor_signature,
    evidence_text,
    fallback_anchor,
    map_evidence,
    quoted_lines,
    words,
)
from embry0.services.signature_delta import Change, SignatureChange, WindowDelta

# A window of a typical stack: one loud edge line, several quieter errors.
LOUD = 'ERR Request failed error="dial tcp: lookup web on resolver: no such host" connIndex=7 dest=https://app.test/'
COLUMN = "2026-09-20 12:00:05.123 UTC [4242] ERROR:  column d.thread_ref does not exist at character 66"
ENUM = (
    "2026-09-20T12:01:02.003Z ERROR 1 --- [api] [nio-8080-exec-3] o.h.engine.jdbc.spi.SqlExceptionHelper"
    '   : ERROR: type "ordersource" does not exist'
)
UNHANDLED = (
    "2026-09-20T12:01:02.004Z ERROR 1 --- [api] [nio-8080-exec-3] c.e.a.GlobalExceptionHandler   : Unhandled exception"
)
DENIED = (
    "2026-09-20T12:02:00.000Z  WARN 1 --- [api] [nio-8080-exec-9] c.e.a.GlobalExceptionHandler"
    "   : Access denied: Not allowed to view this account"
)
TRACE = "Traceback (most recent call last):"
PLAN = "psycopg.errors.FeatureNotSupported: cached plan must not change result type"
WITH_ID = "lead refinement failed for 471186e6-0096-464a-bb61-da2a0ec8ec9f: breaker 'scorer' is OPEN"


def _window(*parts: tuple[str, int], notable: frozenset[str] = frozenset()) -> tuple[list[LogCluster], WindowDelta]:
    """Cluster the lines exactly as the watcher does; ``notable`` lines are NEW, the rest STEADY."""
    entries = [LogEntry(line=line) for line, count in parts for _ in range(count)]
    clusters = cluster_entries(entries)
    notable_ids = {_sig(line) for line in notable}
    delta = WindowDelta(
        changes=tuple(
            SignatureChange(
                signature_id=c.signature_id,
                template=c.template,
                change=Change.NEW if c.signature_id in notable_ids else Change.STEADY,
                count=c.count,
                previous_count=None,
                silent_seconds=None,
            )
            for c in clusters
        )
    )
    return clusters, delta


def _sig(line: str) -> str:
    return signature_id(normalize_line(line))


def _body(evidence: str) -> str:
    return f"## Evidence\n{evidence}\n\n## Impact\nIt fails.\n\n## Suspected cause\nSomething in `{LOUD}`."


# ---- the headline -----------------------------------------------------------


def test_the_anchor_is_the_quoted_error_not_the_loudest_line() -> None:
    clusters, delta = _window((LOUD, 40), (COLUMN, 3), notable=frozenset({LOUD, COLUMN}))
    assert delta.primary is not None and delta.primary.signature_id == _sig(LOUD), "fixture: LOUD must be primary"

    anchor = anchor_signature("Fix the column alias", _body(f"- `[svc] {COLUMN}` (3x)"), clusters, delta)

    assert anchor is not None
    assert anchor.signature_id == _sig(COLUMN), f"anchored to {anchor.template!r}, not the quoted error"
    assert anchor.source is SignatureSource.EVIDENCE
    assert anchor.match is AnchorMatch.LINE
    assert anchor.count == 3


def test_nothing_quoted_falls_back_to_primary_and_says_so() -> None:
    clusters, delta = _window((LOUD, 40), (COLUMN, 3))
    anchor = anchor_signature(
        "Breaker keeps opening", _body("- scorer breaker OPEN for 900s (drill-down)"), clusters, delta
    )
    assert anchor is not None
    assert (anchor.signature_id, anchor.source, anchor.match) == (
        _sig(LOUD),
        SignatureSource.FALLBACK,
        AnchorMatch.PRIMARY,
    )
    assert anchor.mapped == ()
    assert anchor == fallback_anchor(delta)


def test_no_delta_means_no_anchor() -> None:
    clusters, _ = _window((LOUD, 4))
    assert anchor_signature("t", _body(LOUD), clusters, None) is None
    assert fallback_anchor(None) is None


# ---- the three ways a quote maps -------------------------------------------


def test_a_quoted_signature_id_maps() -> None:
    clusters, delta = _window((LOUD, 40), (COLUMN, 3))
    anchor = anchor_signature("t", _body(f"The second row of the table, sig={_sig(COLUMN)}."), clusters, delta)
    assert anchor is not None
    assert (anchor.signature_id, anchor.match) == (_sig(COLUMN), AnchorMatch.SIGNATURE_ID)


def test_a_verbatim_line_maps_through_the_clusterings_own_normalization() -> None:
    clusters, _ = _window((LOUD, 40), (COLUMN, 3))
    # Different pid, time and offset: normalize_line masks them all.
    other_occurrence = "2026-10-01 08:30:00.000 UTC [7] ERROR:  column d.thread_ref does not exist at character 12"
    assert map_evidence(_body(other_occurrence), clusters) == {_sig(COLUMN): AnchorMatch.LINE}


def test_a_redacted_quote_maps_through_the_redacted_samples() -> None:
    """The proposal is redacted before the watcher reads it (#294): the UUID in
    the quote is ``***REDACTED***`` while the template says ``<uuid>``."""
    clusters, _ = _window((LOUD, 40), (WITH_ID, 2))
    redacted_quote = "lead refinement failed for ***REDACTED***: breaker 'scorer' is OPEN"
    assert normalize_line(redacted_quote) != normalize_line(WITH_ID), "fixture: the quote must not match verbatim"
    assert map_evidence(_body(f"`{redacted_quote}`"), clusters) == {_sig(WITH_ID): AnchorMatch.LINE}
    assert map_evidence(_body(f"`{redacted_quote}`"), clusters, redact=None) != {_sig(WITH_ID): AnchorMatch.LINE}


def test_a_reformatted_quote_maps_by_similarity() -> None:
    """Models drop the logger preamble and shorten class names."""
    clusters, _ = _window((LOUD, 40), (ENUM, 2), (DENIED, 30))
    quote = '- api 15:41:05 ERROR SqlExceptionHelper: ERROR: type "ordersource" does not exist'
    assert map_evidence(_body(quote), clusters) == {_sig(ENUM): AnchorMatch.SIMILARITY}


REJECTED = (
    'enrichment /organizations/enrich rejected the request: HTTP 401 {"error":"Invalid API key. See the provider '
    'documentation for authentication details","code":"AUTH_KEY_INVALID","hint":"rotate the key in the provider console"}'
)
CUT_QUOTE = 'enrichment /organizations/enrich rejected the request: HTTP 401 {"error":"Invalid API key. See the provider documentation…'


def _cut_window() -> list[LogCluster]:
    clusters, _ = _window((LOUD, 40), (REJECTED, 4), (DENIED, 30), (ENUM, 2), (COLUMN, 3))
    return clusters


def test_a_quote_cut_short_maps_when_every_word_it_has_is_the_templates() -> None:
    clusters = _cut_window()
    vocabulary = _Vocabulary(clusters, ())
    line = words(CUT_QUOTE)
    coverage, precision = vocabulary.coverage(_sig(REJECTED), line), vocabulary.precision(_sig(REJECTED), line)
    assert CUT_LINE_COVERAGE <= coverage < SIMILARITY_THRESHOLD, (
        f"fixture: coverage {coverage:.3f} must need the cut rule"
    )
    assert precision >= CUT_LINE_PRECISION, f"fixture: precision {precision:.3f}"
    assert map_evidence(_body(f"- `[svc] {CUT_QUOTE}` — 4x"), clusters) == {_sig(REJECTED): AnchorMatch.SIMILARITY}


def test_prose_carrying_as_much_of_the_template_is_not_a_cut_quote() -> None:
    clusters = _cut_window()
    prose = (
        "every enrichment call to /organizations/enrich was rejected: the request failed with HTTP 401 and an "
        "invalid API key error, see the provider documentation; nothing was retried and every company stayed unenriched"
    )
    vocabulary = _Vocabulary(clusters, ())
    line = words(prose)
    assert vocabulary.coverage(_sig(REJECTED), line) >= CUT_LINE_COVERAGE, "fixture: coverage must not refuse it"
    assert map_evidence(_body(f"- {prose}"), clusters) == {}


def test_a_fragment_of_a_template_is_not_a_quote_of_it() -> None:
    """Every word of it is the template's, but it carries too little of it."""
    clusters = _cut_window()
    assert map_evidence(_body("- `rejected the request: HTTP 401`"), clusters) == {}


def test_a_passing_mention_of_a_few_words_does_not_map() -> None:
    """Three of the template's words on one line are not a quote of it."""
    clusters, _ = _window((LOUD, 40), (DENIED, 30))
    prose = "- No access problem on the account view was seen in the window."
    assert len(words(prose) & words(DENIED)) >= MIN_SHARED_WORDS, "fixture: the floor must not be what refuses it"
    assert map_evidence(_body(prose), clusters) == {}


def test_a_short_template_is_not_matched_by_any_line_using_its_words() -> None:
    clusters, _ = _window((LOUD, 40), ("Broken pipe", 5))
    prose = "- a client went away early, so the pipe was broken; harmless."
    assert map_evidence(_body(prose), clusters) == {}, "two shared words matched a two-word template"


def test_only_the_evidence_section_is_read() -> None:
    """The fixture body names LOUD under Suspected cause; that is discussion, not evidence."""
    clusters, _ = _window((LOUD, 40), (COLUMN, 3))
    assert map_evidence(_body("- nothing that is in this window"), clusters) == {}


# ---- choosing among several mapped clusters --------------------------------


def test_the_title_picks_the_error_over_a_generic_line_quoted_with_it() -> None:
    clusters, delta = _window((TRACE, 50), (PLAN, 2), notable=frozenset({TRACE, PLAN}))
    body = _body(f"```\n{TRACE}\n  ...\n{PLAN}\n```")
    assert set(map_evidence(body, clusters)) == {_sig(TRACE), _sig(PLAN)}, "fixture: both lines must map"

    anchor = anchor_signature("Fix the psycopg cached plan mismatch after migrations", body, clusters, delta)
    assert anchor is not None
    assert anchor.signature_id == _sig(PLAN), f"the generic {anchor.template!r} outranked the titled error"
    assert set(anchor.mapped) == {_sig(TRACE), _sig(PLAN)}


def test_a_line_ruled_out_in_the_evidence_does_not_win() -> None:
    clusters, delta = _window((DENIED, 30), (ENUM, 2), (UNHANDLED, 2), notable=frozenset({DENIED}))
    body = _body(f"- `{UNHANDLED}`\n- `{ENUM}`\n\nNot this ticket: `{DENIED}` is the known permission warning.")
    anchor = anchor_signature('Fix the account page 500 from the "ordersource" type cast', body, clusters, delta)
    assert anchor is not None
    assert anchor.signature_id == _sig(ENUM), f"anchored to {anchor.template!r}"


def test_without_a_telling_title_290s_rule_decides_among_the_mapped() -> None:
    """Loudest notable first, then loudest — restricted to what the evidence quotes."""
    clusters, delta = _window((LOUD, 40), (COLUMN, 5), (ENUM, 3), notable=frozenset({ENUM}))
    body = _body(f"- `{COLUMN}`\n- `{ENUM}`")

    anchor = anchor_signature("Something broke", body, clusters, delta)
    assert anchor is not None
    assert anchor.signature_id == _sig(ENUM), "the notable cluster outranks a louder steady one"

    clusters, delta = _window((LOUD, 40), (COLUMN, 5), (ENUM, 3))
    anchor = anchor_signature("Something broke", body, clusters, delta)
    assert anchor is not None
    assert anchor.signature_id == _sig(COLUMN), "with nothing notable, the louder mapped cluster wins"


def test_the_choice_is_deterministic() -> None:
    clusters, delta = _window((LOUD, 40), (COLUMN, 3), (ENUM, 3))
    body = _body(f"- `{COLUMN}`\n- `{ENUM}`")
    first = anchor_signature("Something broke", body, clusters, delta)
    assert all(anchor_signature("Something broke", body, clusters, delta) == first for _ in range(5))


# ---- reading the proposal ---------------------------------------------------


def test_evidence_text_is_the_section_up_to_the_next_heading_of_its_level() -> None:
    body = "Intro\n## Evidence\nA\n### Sub\nB\n## Impact\nC"
    assert evidence_text(body) == "A\n### Sub\nB"


def test_a_body_without_an_evidence_heading_is_read_whole() -> None:
    assert evidence_text("just text\nmore") == "just text\nmore"


def test_quoted_lines_strip_markdown_and_the_container_prefix() -> None:
    text = "> - `[svc] ERROR boom` — 3x\n1. plain line\n```\nfenced line\n```"
    lines = quoted_lines(text)
    for expected in ("[svc] ERROR boom", "ERROR boom", "plain line", "fenced line"):
        assert expected in lines, (expected, lines)
    assert "```" not in lines
