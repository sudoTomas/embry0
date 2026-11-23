"""Secrets must not ride a test command's output into the database (#341 review).

The sandbox environment holds XAI_API_KEY, the Claude OAuth token and the
proxy bearer. A target command that echoes its environment — `env`, `set -x`,
a framework that dumps config on failure — would otherwise put them in
job_verifications.report and in a deliverable the API serves.
"""

from __future__ import annotations

import pytest

from embry0.workflows.verification.scrub import REDACTED, scrub_output
from tests.fixtures.cpu_cost import cost, same


@pytest.mark.parametrize(
    "line",
    [
        "XAI_API_KEY=xai-abcdef0123456789abcdef0123456789",
        "ANTHROPIC_API_KEY=sk-ant-api03-AAAABBBBCCCCDDDD",
        "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-ZZZZYYYYXXXX",
        "GITHUB_TOKEN=ghp_0123456789abcdefghijklmnopqrstuvwxyz",
        "PROXY_ADMIN_TOKEN=Hs8_v-QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ",
        '  declare -x GITLAB_TOKEN="glpat-AAAAAAAAAAAAAAAAAAAA"',
        "POSTGRES_PASSWORD: hunter2hunter2hunter2",
        "Authorization: Bearer sk-ant-oat01-ZZZZYYYYXXXX",
        "embry0_secret_key=abcdef0123456789",
    ],
)
def test_a_secret_never_survives(line: str) -> None:
    scrubbed = scrub_output(line)
    assert REDACTED in scrubbed
    for secret in ("xai-abcdef", "sk-ant", "ghp_0123", "glpat-", "hunter2", "Hs8_v-", "abcdef0123456789"):
        assert secret not in scrubbed


def test_a_bare_token_shaped_string_is_caught_even_without_a_key_name() -> None:
    assert "xai-abcdef0123456789abcdef0123456789" not in scrub_output(
        "connecting with xai-abcdef0123456789abcdef0123456789"
    )


@pytest.mark.parametrize(
    "line",
    [
        "FAILED tests/test_a.py::test_key - AssertionError: expected 3",
        "[ERROR]   LeadIdleTimerServiceImplTest.returnsToPool:42 expected: <1> but was: <2>",
        "==== 1 failed, 40 passed in 3.21s ====",
        "PATH=/usr/local/bin:/usr/bin",
        "NODE_ENV=test",
        "api_key_missing: the fixture did not set one",
        "Tests run: 212, Failures: 1, Errors: 1, Skipped: 0",
    ],
)
def test_ordinary_output_is_untouched(line: str) -> None:
    assert scrub_output(line) == line


def test_the_shape_of_the_output_is_preserved() -> None:
    text = "line one\nSECRET_TOKEN=abcdefghijklmnop\nline three"
    scrubbed = scrub_output(text)
    assert scrubbed.splitlines()[0] == "line one"
    assert scrubbed.splitlines()[2] == "line three"
    assert "abcdefghijklmnop" not in scrubbed


def test_empty_input_is_handled() -> None:
    assert scrub_output("") == ""


def test_known_values_are_redacted_verbatim_longest_first() -> None:
    """Spec 2026-09-29 §5: a registry secret has no known shape, so it is removed by value."""
    text = "echo s3cr3t-nonstandard-value-long and s3cr3t-nonstandard-value"
    scrubbed = scrub_output(text, known_values=["s3cr3t-nonstandard-value", "s3cr3t-nonstandard-value-long"])
    assert "s3cr3t" not in scrubbed, scrubbed
    assert scrubbed == f"echo {REDACTED} and {REDACTED}"


# ---- #57: one set of token rules ----------------------------------------------------------------------------

_GH = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"  # fake


@pytest.mark.parametrize(
    "line",
    [
        f"AssertionError: expected token{_GH}",  # glued to the word before it
        f"assert 'x' == '令牌{_GH}'",  # glued to a word of another script
        f"lin_api_{'k' * 40} rejected",  # a token shape this scrubber did not know
        f"{'1234567890'}:{'A' * 35} sent",  # a Telegram bot token
    ],
)
def test_a_token_shape_the_redactor_knows_is_caught(line: str) -> None:
    scrubbed = scrub_output(line)
    assert (
        REDACTED in scrubbed
        and "A1b2C3d4" not in scrubbed
        and "kkkkkkkk" not in scrubbed
        and "AAAAAAAA" not in scrubbed
    )


def test_a_private_key_block_is_redacted_line_by_line() -> None:
    begin, end = "-----BEGIN RSA " + "PRIVATE KEY-----", "-----END RSA " + "PRIVATE KEY-----"
    text = "before\n" + begin + "\nMIIEowIBAAKCAQEA\nqKmtNdVq7xYz\n" + end + "\nafter"
    scrubbed = scrub_output(text)
    assert scrubbed.splitlines() == ["before", REDACTED, REDACTED, REDACTED, REDACTED, "after"], scrubbed


def test_an_opaque_bearer_token_is_redacted() -> None:
    """The scrubber's own bearer rule: a token of no known shape after "Bearer"."""
    scrubbed = scrub_output("sent Bearer Zq8opaqueValueWithNoShape0123 to the API")
    assert "Zq8opaque" not in scrubbed and REDACTED in scrubbed


def test_a_private_key_block_with_lone_cr_line_ends_keeps_its_lines() -> None:
    begin, end = "-----BEGIN RSA " + "PRIVATE KEY-----", "-----END RSA " + "PRIVATE KEY-----"
    text = "before\r" + begin + "\rMIIEowIBAAKCAQEA\r" + end + "\rafter"
    scrubbed = scrub_output(text)
    assert scrubbed.splitlines() == ["before", REDACTED, REDACTED, REDACTED, "after"], scrubbed


@pytest.mark.parametrize(
    "text",
    ["a" * 200_000, "a1" * 100_000, "9" * 100_000 + "a" * 100_000, ("x" * 50 + " ") * 4_000],
    ids=["one-word", "letters-and-digits", "digits-then-letters", "many-words"],
)
def test_a_long_word_is_scrubbed_in_linear_time(text: str) -> None:
    """A name was tried from every character of a word, and ran to the word's end from each: a 40,000-character
    word took about 10 s. It is tried where a word starts now."""
    assert scrub_output(text) == text
    measured = cost(same(lambda: scrub_output(text)), [text])
    assert measured < SCRUB_COST, f"scrubbing cost {measured:.0f} passes of re over the text, at most {SCRUB_COST:.0f}"


#: How many passes of re over a text scrubbing it may cost, on this thread's CPU clock (tests/fixtures/cpu_cost.py),
#: not the wall clock, which times the host: the texts above measure at most about 6; a name tried from every
#: character of a word, running to the word's end from each, about 1,400 on 16,000 characters, growing with the word
#: (:func:`test_the_scrub_bound_tells_a_name_tried_from_every_character`).
SCRUB_COST = 100.0


def test_the_scrub_bound_tells_a_name_tried_from_every_character() -> None:
    import re

    name = re.compile(r"[A-Za-z0-9_]*(?:SECRET|TOKEN)\b")
    text = "a" * 16_000
    measured = cost(same(lambda: name.sub("[REDACTED]", text)), [text], runs=1)
    assert measured > SCRUB_COST, (
        f"a name tried from every character cost {measured:.0f} passes of re, within the bound"
    )


@pytest.mark.parametrize(
    ("line", "scrubbed"),
    [
        ("9API_KEY=hunter2 next", f"9API_KEY={REDACTED} next"),  # a word that starts with digits: kept
        ("export2 GITHUB_TOKEN='abc def'", f"export2 GITHUB_TOKEN='{REDACTED}'"),
        ("xAPI_KEY=hunter2", f"xAPI_KEY={REDACTED}"),
        ("path=/usr/bin api_key: s3cr3t", f"path=/usr/bin api_key: {REDACTED}"),
    ],
)
def test_an_assignment_is_found_where_its_word_starts(line: str, scrubbed: str) -> None:
    assert scrub_output(line) == scrubbed
