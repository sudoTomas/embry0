"""Log-line redaction (SH-P4.4, #294): what reaches a model provider or a tracker.

Moved from the proxy package with the redactor itself: ``embry0/safety/redaction.py``
is the one redactor now, and these are its log-text profile's tests.
"""

from __future__ import annotations

import time

import pytest

from embry0.safety.redaction import (
    EMAIL_PLACEHOLDER,
    REDACT_OVERLAP_CHARS,
    REDACTED,
    redact_bounded,
    redact_log_text,
    redact_secrets,
    register_redacted_fields,
)
from tests.fixtures.redaction_corpus import CORPUS, EMAIL

_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("GET /x Authorization: Bearer abc123def456ghi789", "abc123def456ghi789"),
        ("authorization=Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
        ("upstream said Bearer zzTOKENzz1234567890 was bad", "zzTOKENzz1234567890"),
        ("X-Api-Key: 9f8e7d6c5b4a", "9f8e7d6c5b4a"),
        ("Cookie: session=abcdef; theme=dark", "abcdef"),
        ('{"password": "hunter2", "user": "bob"}', "hunter2"),
        ("db_password=s3cr3t! retrying", "s3cr3t!"),
        ("client_secret: 'q1w2e3r4'", "q1w2e3r4"),
        ("APOLLO_API_KEY=ak_live_12345 rejected with 401", "ak_live_12345"),
        ("refresh_token=1//0gabcdefg", "1//0gabcdefg"),
        (f"token rejected: {_JWT}", _JWT),
        ("key sk-ant-REDACTME-abcdefghijklmnop was revoked", "sk-ant-REDACTME-abcdefghijklmnop"),
        ("clone with ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
        ("aws AKIAIOSFODNN7EXAMPLE denied", "AKIAIOSFODNN7EXAMPLE"),
        ("postgresql://app:p%40ssw0rd@db:5432/app refused", "p%40ssw0rd"),
        ("mailbox jane.doe+x@example.com returned 403", "jane.doe+x@example.com"),
        ("digest 0123456789abcdef0123456789abcdef01234567 mismatch", "0123456789abcdef0123456789abcdef01234567"),
    ],
)
def test_secret_or_pii_is_removed(raw: str, secret: str) -> None:
    out = redact_log_text(raw, [])
    assert secret not in out
    assert REDACTED in out or EMAIL_PLACEHOLDER in out


@pytest.mark.parametrize(("raw", "secret"), CORPUS, ids=[f"corpus-{i}" for i in range(len(CORPUS))])
def test_every_corpus_kind_is_removed(raw: str, secret: str) -> None:
    out = redact_log_text(raw, [])
    assert secret not in out, f"redact_log_text left {secret!r} in {out!r}"


def test_the_email_becomes_the_placeholder() -> None:
    out = redact_log_text(f"mailbox {EMAIL} returned 403", [])
    assert out == f"mailbox {EMAIL_PLACEHOLDER} returned 403"


def test_diagnostic_content_survives() -> None:
    raw = (
        "2026-09-09T10:00:01Z acme-backend ERROR ApolloClient 401 Unauthorized: invalid key "
        "at com.acme.Apollo.fetch(Apollo.java:88) status=401 attempt=3 path=/api/v1/leads/42"
    )
    assert redact_log_text(raw, []) == raw


def test_key_names_are_kept_so_the_line_still_reads() -> None:
    assert redact_log_text("db_password=s3cr3t host=db1", []) == f"db_password={REDACTED} host=db1"
    assert (
        redact_log_text('{"password": "hunter2", "user": "bob"}', []) == f'{{"password": "{REDACTED}", "user": "bob"}}'
    )


def test_url_userinfo_keeps_host() -> None:
    assert redact_log_text("postgresql://app:pw@db:5432/app", []) == f"postgresql://{REDACTED}@db:5432/app"


def test_idempotent_and_total() -> None:
    once = redact_log_text(f"Authorization: Bearer abc.def.ghi and {_JWT} and a@b.co", [])
    assert redact_log_text(once, []) == once
    assert redact_log_text("", []) == ""


# ---- bounded input: redaction is linear and runs on a bounded window ------


def test_a_long_unbroken_run_does_not_blow_up() -> None:
    """The secret-key rules used to start with an unbounded `[\\w.-]*`, giving
    O(n^2) backtracking on one long token: 32k chars took 44s. Three call sites
    run this synchronously in the event loop on data-controlled text."""
    started = time.monotonic()
    out = redact_log_text("a" * 32_000, [])
    assert time.monotonic() - started < 2.0
    assert len(out) <= 32_000


def test_redact_bounded_cuts_to_the_limit() -> None:
    text = "keep this. " + "b" * 500
    out = redact_bounded(text, 40)
    assert len(out) <= 40
    assert out.startswith("keep this. ")


def test_redact_bounded_only_ever_sees_a_bounded_window() -> None:
    seen: list[int] = []

    def _spy(text: str) -> str:
        seen.append(len(text))
        return text

    redact_bounded("x" * 1_000_000, 100, redact=_spy)
    assert seen and max(seen) <= 100 + REDACT_OVERLAP_CHARS


def test_a_secret_straddling_the_cut_is_not_left_half_visible() -> None:
    """Cutting first and redacting after would hand the redactor a fragment it
    might not match. The window carries an overlap so the whole token is seen."""
    secret = "ghp_" + "a1b2c3d4e5" * 4
    out = redact_bounded("x" * 95 + " " + secret + " trailing", 100)
    assert secret not in out
    assert "ghp_a1b2" not in out
    assert len(out) <= 100


def test_redact_bounded_passes_short_text_through() -> None:
    assert redact_bounded("plain line", 100) == "plain line"
    assert redact_bounded("", 100) == ""


# ---- the process's own secrets ------------------------------------------------


def test_a_known_secret_value_is_removed_in_any_shape() -> None:
    out = redact_log_text("proxy replied 403 for opaque-credential-0001", ["opaque-credential-0001"])
    assert out == f"proxy replied 403 for {REDACTED}"


def test_by_default_the_process_secret_env_values_are_removed(monkeypatch) -> None:
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "plain-shaped-value")
    assert redact_log_text("upstream echoed plain-shaped-value back") == f"upstream echoed {REDACTED} back"


# ---- configurable payload fields ---------------------------------------------


@pytest.fixture
def fields():
    yield register_redacted_fields
    register_redacted_fields([])


def test_configured_payload_fields_are_redacted(fields) -> None:
    assert fields("phone, customer_name") == ["customer_name", "phone"]
    out = redact_log_text('lead phone=+15551234 customer_name="Jane Q" ok=1 {"Phone": "555 0100"}', [])
    assert out == f'lead phone={REDACTED} customer_name="{REDACTED}" ok=1 {{"Phone": "{REDACTED}"}}'


def test_a_configured_field_matches_whole_names_only(fields) -> None:
    fields("phone")
    assert redact_log_text("telephone=+15551234 phone_type=mobile", []) == "telephone=+15551234 phone_type=mobile"


def test_no_configured_fields_leaves_field_values_alone(fields) -> None:
    fields("")
    assert redact_log_text("phone=+15551234", []) == "phone=+15551234"


def test_a_field_name_that_is_not_an_identifier_is_ignored(fields) -> None:
    assert fields(["phone", "a|b", "x)y", ""]) == ["phone"]


# ---- the diagnostics profile keeps its readability ----------------------------


def test_diagnostics_do_not_gain_the_pii_rules() -> None:
    """Decision recorded on #294: agent stderr is stored in embry0's own DB,
    and a digest or path is what makes it useful there. Only the log-text
    profile — what leaves for a model provider or a tracker — drops PII."""
    text = f"lookup {EMAIL} digest 0123456789abcdef0123456789abcdef01234567"
    assert redact_secrets(text) == text


# ---- linear matching on adversarial input --------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "a." * 100_000,  # an e-mail local part that never meets an @
        "a@" * 100_000,
        "a@b." * 50_000,
        "x" * 200_000,
        "1" * 200_000,  # an opaque run
        "token" * 40_000,  # a secret key name with no separator
        "phone" * 40_000,
        "token" + " " * 200_000,  # a key name whose separator never comes
        # #57: the glued token rules, each prefix repeated glued, or one prefix before a long run.
        "aghp_" * 40_000,
        "a" + "ghp_" + "a" * 200_000,
        "x" + "github_pat_" * 20_000,
        "xglpat-" * 30_000,
        "xsk-ant-" * 25_000,
        "kxai-" * 40_000,
        "mlin_api_" * 25_000,
        "nxoxb-" * 30_000,
        "-eyJ" * 50_000,  # a JWT start after a separator, over and over: quadratic before its segments were bounded
        "aeyJ" * 50_000,  # the same, glued
        "令ghp_" * 40_000,  # a glued prefix after a letter of another script
        "xgithub_pat_" + "a" * 200_000,  # a glued github_pat_ with no digit to find
        ".eyJ" * 50_000,  # a JWT start at every run's start
        "eyJ" * 67_000,  # one run of starts
        "x eyJ" + "a" * 8 + "." + "a" * 200_000,  # a JWT start whose second dot never comes
    ],
    ids=[
        "dots",
        "ats",
        "domains",
        "word",
        "digits",
        "keyname",
        "fieldname",
        "spaces",
        "glued-gh",
        "glued-gh-run",
        "glued-github-pat",
        "glued-glpat",
        "glued-sk-ant",
        "glued-xai",
        "glued-lin",
        "glued-xox",
        "jwt-starts",
        "glued-jwt-starts",
        "glued-other-script",
        "glued-github-pat-no-digit",
        "jwt-run-starts",
        "jwt-one-run",
        "jwt-no-second-dot",
    ],
)
def test_matching_stays_linear(hostile: str, fields) -> None:
    fields("phone")
    started = time.perf_counter()
    redact_log_text(hostile, [])
    assert time.perf_counter() - started < 2.0, "a rule backtracks super-linearly on this input"


def test_redaction_module_is_the_only_redactor_left() -> None:
    """#294 converged two redactors into this one; the proxy copy must not return."""
    import importlib.util

    assert importlib.util.find_spec("embry0.execution.proxy.log_redaction") is None
