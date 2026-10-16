"""#281 — agent stderr is stored, so it is scrubbed first."""

import pytest

from embry0.safety.redaction import (
    REDACTED,
    STDERR_TAIL_MAX_BYTES,
    redact_event,
    redact_secrets,
    redacted_stderr_tail,
    secret_env_values,
    tail_bytes,
)

_GH = "ghp_" + "a1B2" * 9
_ANT = "sk-ant-oat01-" + "Zx9_" * 12
_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f"fatal: could not read from https://x-access-token:{_GH}@github.com/o/r.git", _GH),
        (f"remote said: bad credentials for {_GH}", _GH),
        (f"> Authorization: Bearer {_JWT}", _JWT),
        ("> authorization: token abcdefgh12345678", "abcdefgh12345678"),
        ("x-api-key: not-a-known-format-123", "not-a-known-format-123"),
        (f"CLAUDE_CODE_OAUTH_TOKEN={_ANT}", _ANT),
        ('{"api_key": "plain-looking-value", "model": "m"}', "plain-looking-value"),
        ("POSTGRES_PASSWORD='hunter2hunter2'", "hunter2hunter2"),
        ("glpat-abcdefghij0123456789 rejected", "glpat-abcdefghij0123456789"),
        ("xai-" + "q" * 40, "q" * 40),
        ("lin_api_" + "k" * 30, "lin_api_" + "k" * 30),
        ('usage: runner --config {"prompt": "internal text"}', "internal text"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----", "MIIEow"),
        ("Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
    ],
)
def test_secrets_do_not_survive(text, secret):
    out = redact_secrets(text)
    assert secret not in out
    assert REDACTED in out


def test_a_known_value_is_removed_even_in_an_unknown_shape():
    out = redact_secrets("proxy replied 403 for opaque-credential-0001", ["opaque-credential-0001"])
    assert out == f"proxy replied 403 for {REDACTED}"


def test_ordinary_diagnostics_are_left_readable():
    text = "Traceback (most recent call last):\n  File \"/app/runner.py\", line 12\nKeyError: 'model'\nexit code 1"
    assert redact_secrets(text) == text


def test_short_known_values_never_shred_the_text():
    assert redact_secrets("dev mode is true", ["true", "dev"]) == "dev mode is true"


def test_secret_env_values_picks_secret_looking_names_only():
    env = {"GITHUB_TOKEN": "tok-12345678", "API_KEY": "key-12345678", "PATH": "/usr/bin:/bin", "DEBUG_TOKEN": "x"}
    assert set(secret_env_values(env)) == {"tok-12345678", "key-12345678"}


def test_tail_is_capped_and_keeps_the_newest_whole_lines():
    text = "\n".join(f"line {i:05d}" for i in range(5000))
    out = tail_bytes(text, 1024)
    assert len(out.encode()) <= 1024
    assert out.endswith("line 04999")
    assert out.startswith("line ")


def test_tail_never_splits_a_multibyte_character():
    out = tail_bytes("é" * 5000, 1001)
    assert len(out.encode()) <= 1001
    assert set(out) == {"é"}


def test_stored_tail_is_redacted_before_it_is_capped():
    # The token straddles the 8 KiB cut: capping first would store a fragment.
    padding = "x" * (STDERR_TAIL_MAX_BYTES - 20)
    out = redacted_stderr_tail(f"boom {_GH}{padding}", known_values=[])
    assert len(out.encode()) <= STDERR_TAIL_MAX_BYTES
    assert "a1B2a1B2" not in out


def test_blank_stderr_stores_nothing():
    assert redacted_stderr_tail("  \n") == ""
    assert redacted_stderr_tail("") == ""


def test_a_token_cut_by_the_read_window_leaves_no_fragment():
    # One giant line: the 32 KiB window opens inside the token, and the
    # --config redaction then shrinks the text under the cap, so nothing
    # downstream would have dropped the fragment.
    stderr = "A" * 10 + _GH + " --config " + "y" * (STDERR_TAIL_MAX_BYTES * 4 - 20)
    out = redacted_stderr_tail(stderr, known_values=[])
    assert "a1B2" not in out
    assert "B2a1" not in out


def test_redaction_stays_linear_on_a_long_unbroken_word():
    """A traceback carrying a 200 KiB base64 blob is one unbroken word. With
    an unbounded scheme class the URL-credential pattern rescanned to the end
    of the text from every position: 16s of blocked event loop. Every
    repetition in that pattern is bounded now; this runs in milliseconds, and
    the threshold is set well clear of both."""
    import time

    start = time.perf_counter()
    assert redact_secrets("A" * 200_000) == "A" * 200_000
    assert time.perf_counter() - start < 5.0


def test_redact_event_scrubs_strings_recursively_and_leaves_correlation_keys() -> None:
    secret = "sekrit-value-0123456789"
    event = {
        "type": "tool_call",
        "timestamp": "2026-10-16T00:00:00+00:00",
        "run_id": "run-" + "a" * 32,
        "tool_id": "toolu_01" + secret[:8],
        "tool_name": "Bash",
        "summary": f"curl -H 'Authorization: Bearer {secret}'",
        "tool_input": {"command": f"export TOKEN={secret}", "env": [{"name": "X", "value": secret}]},
        "content": [{"type": "text", "text": f"saw {secret}"}],
        "is_error": False,
        "count": 3,
    }
    out = redact_event(event, [secret])
    assert secret not in repr(out), "every string value, however nested, is scrubbed"
    assert out["tool_input"]["env"][0]["value"] == REDACTED
    assert out["content"][0]["text"] == f"saw {REDACTED}"
    assert out["tool_id"] == event["tool_id"] and out["run_id"] == event["run_id"], (
        "correlation keys are never rewritten"
    )
    assert out["is_error"] is False and out["count"] == 3
    assert secret in event["summary"], "the input event is not mutated"
