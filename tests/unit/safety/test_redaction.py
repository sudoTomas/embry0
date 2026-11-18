"""#281 — agent stderr is stored, so it is scrubbed first."""

import pytest

from embry0.safety.redaction import (
    REDACTED,
    STDERR_TAIL_MAX_BYTES,
    redact_bounded,
    redact_event,
    redact_log_text,
    redact_secrets,
    redacted_stderr_tail,
    secret_env_values,
    tail_bytes,
)
from embry0.workflows.verification.scrub import scrub_output

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


# ---- #57: a provider token glued to the word before it ------------------------------------------------------

#: Fake token values only: (the text, the token in it). Each is glued to the character before it.
_GH36 = "ghp_" + "AbCdEfGhIjKlMnOpQrStUvWxYzAbCdEfGhIj"  # 36 letters, no digit: the generic rule misses it
_GH36_DIGITS = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
_GLPAT = "glpat-" + "abcdefghijklmnopqrst"  # 20 characters: too short for the generic rule
_JWT_FAKE = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
GLUED = [
    ("n" + _GH36, _GH36),
    ("n" + _GH36_DIGITS, _GH36_DIGITS),
    ("_" + "ghs_" + "Zz9" * 12, "ghs_" + "Zz9" * 12),
    ("n" + _GH36_DIGITS + "x", _GH36_DIGITS),  # glued on both sides
    ("x" + "github_pat_" + "11ABCDEFG0_" + "abcdefghij" * 6, "github_pat_" + "11ABCDEFG0_" + "abcdefghij" * 6),
    ("n" + _GLPAT, _GLPAT),
    ("z" + "sk-ant-oat01-" + "Zx9_" * 12, "sk-ant-oat01-" + "Zx9_" * 12),
    ("z" + "sk-proj-" + "Ab1c" * 12, "sk-proj-" + "Ab1c" * 12),
    ("z" + "sk-svcacct-" + "Ab1c" * 12, "sk-svcacct-" + "Ab1c" * 12),
    ("z" + "sk-admin-" + "Ab1c" * 12, "sk-admin-" + "Ab1c" * 12),
    ("z" + "sk-or-v1-" + "0a1b2c3d" * 8, "sk-or-v1-" + "0a1b2c3d" * 8),  # OpenRouter
    ("k" + "xai-" + "q" * 40, "xai-" + "q" * 40),
    ("m" + "lin_api_" + "k" * 40, "lin_api_" + "k" * 40),
    ("m" + "lin_oauth_" + "k" * 40, "lin_oauth_" + "k" * 40),
    *[("w" + f"xox{kind}-" + "123456789012-AbCdEfGh", f"xox{kind}-" + "123456789012-AbCdEfGh") for kind in "abprs"],
    ("token" + _JWT_FAKE, _JWT_FAKE),
    # A word of another script before it: \b takes it for a word character too.
    ("令牌" + _GH36_DIGITS, _GH36_DIGITS),
    ("トークン" + _GLPAT, _GLPAT),
    ("ключ" + "sk-ant-oat01-" + "Zx9_" * 12, "sk-ant-oat01-" + "Zx9_" * 12),
    ("clé" + _GH36, _GH36),
]


@pytest.mark.parametrize(("text", "token"), GLUED, ids=[f"{i}-{t[:9]}" for i, (_, t) in enumerate(GLUED)])
@pytest.mark.parametrize(
    "redact",
    [lambda t: redact_secrets(t), lambda t: redact_log_text(t), lambda t: redact_bounded(t, 2000)],
    ids=["redact_secrets", "redact_log_text", "redact_bounded"],
)
def test_a_token_glued_to_the_word_before_it_is_redacted(text: str, token: str, redact) -> None:
    out = redact(f"token {text} rejected")
    assert token not in out and token[-20:] not in out, f"left readable: {out}"
    assert REDACTED in out


@pytest.mark.parametrize(
    "text",
    [
        "berlin_api_clientconfigurationhandler",  # lin_api_ inside an identifier
        "task-0123456789abcdef0123456789abcdef01234567",  # sk- inside a word, with a hash
        "disk-usage-report-generator-for-tenants",
        "network-interface-configuration-loader",  # rk- inside a word
        "highs_andlowsofthemarketthisquarterxx",  # ghs_ inside a word
        "thighs_" + "a" * 35,  # one short of a token's 36
        "EASTASIAPACIFICREGIONSX",  # an AWS key id's prefix inside an uppercase word
        "maxai-gateway-configuration",
        "helpdesk-admin-panel-settings",  # sk-admin- inside a word, too short and no digit
        "test_github_pat_with_no_owner_is_refused",
        "test_github_pat_with_no_owner_is_refused_when_the_registry_has_none",  # 40+, but no digit
        "callbacks-proj-milestone-review-checklist-for-the-quarter",  # sk-proj-? no: "ks-proj" is not "sk-proj"
        "desk-proj-alpha-beta-gamma-delta-epsilon-zeta-eta",  # sk-proj- inside a word, no digit
        "thighs_" + "a" * 37,  # one past a token's 36, then more letters
        "test_github_pat_rotation_v2_is_refused",  # 20-39 characters after the prefix, a digit among them
        "helpdesk-admin-panel-settings-for-the-support-team-members",  # 40+ after sk-admin-, no digit
    ],
)
def test_an_identifier_that_only_holds_a_prefix_is_left_alone(text: str) -> None:
    assert redact_secrets(text) == text


def _jwt(header: int, payload: int) -> str:
    """A fake JWS whose header and payload segments run to the given numbers of base64url characters."""
    return "eyJ" + "hb" * ((header - 3) // 2) + "." + "eyJ" + "zd" * ((payload - 3) // 2) + "." + "dozjgNry" * 6


@pytest.mark.parametrize(
    ("header", "payload"),
    [(4_843, 300), (1_099, 300), (300, 21_355), (4_843, 21_355)],
    ids=["x5c-header", "long-kid", "16-KiB-payload", "both"],
)
@pytest.mark.parametrize("redact", [lambda t: redact_secrets(t), lambda t: redact_log_text(t)], ids=["secrets", "log"])
def test_a_jwt_past_the_bounds_of_a_glued_one_is_redacted_whole(header: int, payload: int, redact) -> None:
    """A JWT that starts a run is matched without a bound: a header carrying a certificate chain or a long key id,
    and a payload of many claims, are redacted whole."""
    token = _jwt(header, payload)
    out = redact(f'{{"id_token": "x", "raw": " {token} "}}')
    for part in token.split("."):
        assert part[-40:] not in out, f"left readable: {len(part)} characters of a {header}/{payload} JWT"


def test_a_glued_jwt_with_a_payload_of_many_claims_is_redacted_whole() -> None:
    """The glued rule bounds only its header: a payload past 16 KiB is redacted with the rest."""
    token = _jwt(400, 21_355)
    out = redact_secrets(f"token{token} rejected")
    for part in token.split("."):
        assert part[-40:] not in out, f"left readable: {len(part)} characters"


def test_a_glued_jwt_with_a_longer_header_is_redacted() -> None:
    token = _jwt(400, 300)
    out = redact_secrets(f"token{token} rejected")
    assert token.split(".")[0][-40:] not in out and token.split(".")[2] not in out, out[:80]


# ---- a JWE, an OpenPGP private key, a token glued to what follows it --------------------------------------------

#: A JWE in compact form, header.key.iv.ciphertext.tag: with "alg": "dir" the key part is empty (an Auth.js session
#: cookie is one), with "RSA-OAEP" it is the wrapped content key. Fake segments only.
_JWE_IV, _JWE_TAG = "AbCdEfGhIjKlMnOp", "QrStUvWxYzAbCdEfGhIjKl"
_JWE_CIPHERTEXT = "CiPhErTeXt0_-" * 30
_JWE_WRAPPED = "WrApPeD0_-" * 34
_JWE_DIR = "eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0" + ".." + f"{_JWE_IV}.{_JWE_CIPHERTEXT}.{_JWE_TAG}"
_JWE_LONG_HEADER = "eyJ" + "hbGci" * 240 + ".." + f"{_JWE_IV}.{_JWE_CIPHERTEXT}.{_JWE_TAG}"
_JWE_RSA = "eyJhbGciOiJSU0EtT0FFUCIsImVuYyI6IkEyNTZHQ00ifQ" + f".{_JWE_WRAPPED}.{_JWE_IV}.{_JWE_CIPHERTEXT}.{_JWE_TAG}"
_PGP_BODY = [
    "",
    "lQVYBGNfAkEBDADExAmPlEbOdY0aBcDeFgHiJkLmNoPqRsTuVwXyZ",
    "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
    "=AbCd",
]
# Split, as the other key blocks in these tests are, so a secret scanner reading this file sees no key block.
_PGP_SECRET_KEY = "\n".join(
    ["-----BEGIN PGP " + "SECRET KEY BLOCK-----", *_PGP_BODY, "-----END PGP " + "SECRET KEY BLOCK-----"]
)
_PGP_KEY = "\n".join(
    ["-----BEGIN PGP " + "PRIVATE KEY BLOCK-----", *_PGP_BODY, "-----END PGP " + "PRIVATE KEY BLOCK-----"]
)
_TELEGRAM = "123456789:AAH" + "AbCdEfGhIjKlMnOpQrStUvWxYz012345"  # its secret: "AA" and 33 more
_AKIA = "AKIA" + "ABCDEFGH2JKLMNOP"  # a key id's 16 are base32: a digit among them

#: "<digits>:AA..." that is no bot token: an SSH key blob, uppercase hex, base64 with a leading zero byte, and
#: "<epoch>:<base64url>" log lines whose data begins "AA".
_LOOKALIKES = [
    "534823407285:AAAAB3NzaC1yc2EAAAADAQABAAABAQ"
    + "S1UlcWgF7F8je3UF0xDzki41D26AyCuQomdjVHaV33oxIeVh6/ATMQTOo/y+nTyfnx",
    "322399564144:AA5BC1DDF190D13EB9FBCD33109C762718F2C437AFC9DFB9D08241A01995C28BAAD973BCC728BBF5AA",
    "433756004771:AAC24F182FAC3B026B2C4EE59162A544ECDC5815051692BB24092F962CA41DD66266D06D20EFBC4E4F",
    "861906601359:AAcHAizkoj04WKeflyqzuqWLiAEYTEFdyHTdhgARKyJ9ZwSfhtPL+IF4VnjVBAYPOeiugvseZBgvdHGbuQ",
    "1618631893:AAbOg-4q55R7ZhZTx9hFW65Xy1GWJJmpsUY1A6j4J1wWLUZZgyzJptCOaEhezw5SE5Az4g2ZOhNV info plain limit",
    "1768619833:AAVrm_xR0iejA_FV5ZrkpxlLkpYCKRi7eZ8HdmZuucHlQf9XjboRk0EG5w4z3p57IVRpa37igNzXJvbTwKd_kyuRCYG9wPzk im",
    "1748944116:AAdLITdPEpkXoNw93jMCkc8SKRfv7gO-t6cDRKcSD33mDcqtg69uEEqbPVSoQNKon06kvGEMAgwuFHJ1TWNyBBEOUA== bind",
    "1699001604:AABIlr8kQ1pnSdt3-BOu7xwn-bJP-ypR4_9HWW4gaKgwFvyqxkrejiO7DnczJV__K3fEcnhKXgC-KXwQMNGwvWBE2q6gfw== or",
    # Short ones, whose rest after 35 characters is as short as a glued word's: uppercase only (a SHA-1 in hex),
    # and base64 that goes on with "=" or "+".
    "1700000000:AAEF29DFD07532CD54A5ABAA6580A831ADAD2E55 commit",
    "1700000001:AA81nWtUBtlUxQdNsu1ueUW5bukCtuaG0Rxh6w== sent",
    "1700000002:AA9vyKwWnvMvcYl9ORRbcqlk4ArqKwkFIfrH4+uX sent",
]

REDACTORS = [
    lambda t: redact_secrets(t),
    lambda t: redact_log_text(t),
    lambda t: redact_bounded(t, 4000),
    lambda t: scrub_output(t),
]
REDACTOR_IDS = ["redact_secrets", "redact_log_text", "redact_bounded", "scrub_output"]

#: (the text, the parts of it that must not stay readable)
WHOLE = [
    (f"session {_JWE_DIR} set", [_JWE_IV, _JWE_CIPHERTEXT[:40], _JWE_TAG]),
    (f"session {_JWE_RSA} set", [_JWE_WRAPPED[:40], _JWE_IV, _JWE_CIPHERTEXT[:40], _JWE_TAG]),
    (f"seen sessn{_JWE_DIR}", [_JWE_IV, _JWE_CIPHERTEXT[:40], _JWE_TAG]),  # glued to the word before it
    # A header past the glued rule's 1,024 characters: the rule at a run's start takes it, its empty key part too.
    (f"session {_JWE_LONG_HEADER} set", [_JWE_LONG_HEADER[1000:1040], _JWE_IV, _JWE_CIPHERTEXT[:40], _JWE_TAG]),
    (f"key:\n{_PGP_SECRET_KEY}\ndone", [line for line in _PGP_BODY if line]),  # the older name of a PGP key block
    (f"key:\n{_PGP_KEY}\ndone", [line for line in _PGP_BODY if line]),
    (f"bot {_TELEGRAM}x rejected", [_TELEGRAM[10:]]),  # glued on the right: a letter
    (f"bot {_TELEGRAM}9 rejected", [_TELEGRAM[10:]]),  # a digit
    (f"set {_TELEGRAM}_bot_token rejected", [_TELEGRAM[10:]]),  # an underscore and a short word
    (f"https://api.telegram.org/bot{_TELEGRAM}/getMe", [_TELEGRAM[10:]]),  # in a Bot API URL, not glued
    (f"aws {_AKIA}x rejected", [_AKIA]),  # glued on the right: a lowercase letter
    (f"aws {_AKIA}_profile rejected", [_AKIA]),
]


@pytest.mark.parametrize(("text", "secrets"), WHOLE, ids=[f"{i}" for i in range(len(WHOLE))])
@pytest.mark.parametrize("redact", REDACTORS, ids=REDACTOR_IDS)
def test_a_jwe_a_pgp_private_key_and_a_token_glued_on_its_right_are_redacted(text: str, secrets, redact) -> None:
    out = redact(text)
    left = [secret for secret in secrets if secret in out]
    assert not left, f"left readable: {left} in {out!r}"
    assert REDACTED in out


def test_the_scrubber_keeps_a_pgp_private_key_block_s_lines() -> None:
    text = f"key:\n{_PGP_KEY}\ndone"
    assert scrub_output(text).splitlines() == ["key:", *[REDACTED] * (len(_PGP_BODY) + 2), "done"]


@pytest.mark.parametrize(
    "text",
    [
        "-----BEGIN PGP PUBLIC KEY BLOCK-----\nmQENBGNfAkEBCADExAmPlE\n-----END PGP PUBLIC KEY BLOCK-----",
        "-----BEGIN PGP SIGNATURE-----\niQEzBAEBCAAdFiEExAmPlE\n-----END PGP SIGNATURE-----",
        "AKIA" + "ABCDEFGHIJKLMNOP" + "Q",  # 21 capitals and digits: an uppercase word, not a key id
        "AKIA" + "ABCDEFGHIJKLMNOP" + "7",
        "ASIA" + "NDEVELOPMENTBANK" + "reports",  # 20 capitals glued to a lowercase word, no digit among them
        "AKIA" + "ABCDEFGHIJKLMNOP" + "_x",
        *_LOOKALIKES,
        "1700000000:" + "build_cache_key_for_the_nightly_runs",  # 35 characters glued on, but no "AA"
        "release 2026.11.18..2026.11.19 notes",
        "see eyJhbGciOiJIUzI1NiJ9..",  # a header and two dots: no third part
    ],
)
def test_near_misses_of_these_shapes_are_left_alone(text: str) -> None:
    assert redact_secrets(text) == text
    assert scrub_output(text) == text


@pytest.mark.parametrize(
    "text",
    [
        " eyJ" + "a" * 20 + ".." + "b" * 200_000,
        (" eyJ" + "a" * 8 + "..") * 14_000,
        (" eyJ" + "a" * 8 + "." + "b" * 8 + "." + "c" * 8 + ".") * 6_000,
        " x" + "aeyJ" * 255 + "." + "b" * 200_000,  # every glued start reads the same payload, which has no end
        "-----BEGIN PGP PRIVATE KEY BLOCK " * 6_000,
        "-----BEGIN " + "A" * 200_000,
        "1234567:AA" + "a" * 200_000,
        "AKIA" + "A" * 200_000,
    ],
    ids=[
        "jwe-run",
        "jwe-repeated",
        "five-parts-repeated",
        "glued-starts-one-payload",
        "pgp-repeated",
        "begin-caps",
        "telegram-run",
        "akia-run",
    ],
)
def test_the_new_shapes_stay_linear(text: str) -> None:
    import time

    start = time.perf_counter()
    redact_secrets(text)
    scrub_output(text)
    assert time.perf_counter() - start < 5.0
