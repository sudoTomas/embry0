"""Redact secrets from a test command's output before it is stored (#299).

``output_tail`` is unreviewed text produced inside the sandbox, whose
environment holds ``XAI_API_KEY``, the Claude OAuth token, the proxy bearer
and per-owner forge tokens. A target that runs ``env``, ``set -x``, or a
framework that dumps its config on failure would otherwise write them to
``job_verifications.report`` and to a deliverable the API serves.

Same default-deny spirit as ``DockerClient._scrub_cmd_for_log``, one level
down: that one knows the argv it built, this one only sees text, so it
redacts on two signals — an assignment whose NAME looks secret, and a value
whose SHAPE is a known credential. Line structure is preserved so the text
still reads as a test log.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from embry0.safety.redaction import PRIVATE_KEY_BLOCK, redact_token_shapes
from embry0.safety.redaction import REDACTED as REDACTED  # the one placeholder; render.py reads it from here

# A name means "this value is a credential" when its LAST underscore-delimited
# word is one of these — so API_KEY, GITHUB_TOKEN and embry0_secret_key match
# while PATH, NODE_ENV and a log line reading "api_key_missing: ..." do not.
# A `__OWNER` suffix (GITHUB_TOKEN__ACME) is stripped before the test.
_SECRET_WORDS = ("token", "secret", "password", "passwd", "credential", "credentials", "auth", "key", "apikey")
_OWNER_SUFFIX_RE = re.compile(r"__[A-Za-z0-9_]+$")


def _is_secret_name(name: str) -> bool:
    return _OWNER_SUFFIX_RE.sub("", name).lower().rsplit("_", 1)[-1] in _SECRET_WORDS


# `KEY=value`, `KEY: value`, optionally quoted, optionally behind `declare -x`. Tried only where a word starts, past
# any digits it starts with (group 1, kept): tried from every character of a long word, the name ran to the word's
# end from each and the line took time quadratic in its length (a 40,000-character word, about 10 s). Within a word
# the match could only ever begin at its first letter or "_", so what it matches is unchanged.
_ASSIGNMENT_RE = re.compile(r"""(?<![A-Za-z0-9_])([0-9]*)([A-Za-z_][A-Za-z0-9_]*)\s*(=|:\s)\s*("[^"]*"|'[^']*'|\S+)""")
# Credential shapes that are secret wherever they appear, named or not, come from the one set of token rules
# (``embry0.safety.redaction.redact_token_shapes``, #57): a token glued to the word before it included. Only the
# bearer rule is this scrubber's own, kept narrower than the redactor's ("Basic" in a test log is a word).
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")


def _redact_private_keys(text: str) -> str:
    """Every line of a PEM private key block as ``REDACTED``, so the output keeps its lines (#57): the BEGIN line
    alone was redacted, and the key's body lines stayed readable."""
    return PRIVATE_KEY_BLOCK.sub(lambda m: "\n".join(REDACTED for _ in m.group(0).splitlines()), text)


def _redact_assignments(line: str) -> str:
    def replace(match: re.Match[str]) -> str:
        digits, name, sep, value = match.group(1), match.group(2), match.group(3), match.group(4)
        if not _is_secret_name(name) or not value.strip("\"'"):
            return match.group(0)
        quote = value[0] if value[:1] in ("'", '"') else ""
        return f"{digits}{name}{sep}{quote}{REDACTED}{quote}"

    return _ASSIGNMENT_RE.sub(replace, line)


def scrub_output(text: str, known_values: Iterable[str] = ()) -> str:
    """Return ``text`` with credential-looking values replaced.

    Conservative on both sides: it never drops or reorders lines (the result
    is still a readable log, and the parser runs on the raw text before
    this), and it redacts the whole value rather than part of it.

    ``known_values`` are secrets known by value — the credential registry's
    plaintexts (spec 2026-09-29 §5), which have no recognisable shape. They
    are replaced verbatim, longest first so a value never survives as the
    tail of a shorter one's redaction.
    """
    if not text:
        return text
    values = sorted({v for v in known_values if v}, key=len, reverse=True)
    lines = []
    for line in _redact_private_keys(text).splitlines():
        for value in values:
            line = line.replace(value, REDACTED)
        scrubbed = _redact_assignments(line)
        scrubbed = redact_token_shapes(_BEARER_RE.sub(REDACTED, scrubbed))
        lines.append(scrubbed)
    return "\n".join(lines)
