"""Free-text redaction: the one redactor for secrets and personal data (#294).

Two entry points over one rule set, because the two sinks carry different risks:

- :func:`redact_secrets` — DIAGNOSTICS (agent stderr tails, runner error
  fields) stored in embry0's own database. Secrets only: a path, a digest or a
  request id is what makes a diagnostic useful, and those stay readable.
- :func:`redact_log_text` — LOG LINES and anything else sent to a third-party
  model provider or filed in a tracker: the watcher's briefing, context-proxy
  results, the evidence and review prompts, the watcher's draft tickets. The
  secret rules plus e-mail addresses, any long opaque run, and the operator's
  configured payload fields (:func:`register_redacted_fields`). Deliberately
  over-eager — a redacted commit SHA costs a reader little, a leaked key costs
  a rotation.

``docker_client._scrub_cmd_for_log`` covers ARGV (``-e KEY=VAL`` values and
the ``--config`` payload). Agent stderr is free text — a traceback, a CLI
error, a curl dump — so it needs a text scrubber: nothing covered tokens or
auth headers before #281.

Default-deny where the shape allows it: any ``KEY=value`` / ``"key": "value"``
whose key NAME looks secret is redacted whatever the value looks like, the way
the argv scrubber treats every ``-e`` value. Known token formats and the
process's own secret env values are redacted wherever they appear.

Regex-based and therefore NOT a guarantee: callers still bound what they pass
on. Every repetition that can meet attacker-sized input is bounded or anchored
so matching stays linear — callers run this in the event loop on log text they
do not control.

Pure and dependency-free: this module is baked into the sandbox images and
copied into the proxy image (``infra/Dockerfile.proxy``).
"""

from __future__ import annotations

import bisect
import os
import re
from collections.abc import Callable, Iterable
from typing import Any

REDACTED = "***REDACTED***"
#: What an e-mail address becomes. The same placeholder the log-signature
#: normaliser uses, so a redacted line and its template read alike.
EMAIL_PLACEHOLDER = "<email>"

# Storing a diagnostic tail must never become a way to store a log.
STDERR_TAIL_MAX_BYTES = 8 * 1024

# A key NAME: the secret word anywhere in one identifier. The match may start
# only where an identifier starts (the lookbehind) and the runs around the word
# are bounded. Unanchored and unbounded, as it was, the engine retried every
# position of a long dotted run against every split of it: 10 KB of "a.a.a…"
# took 6.5 s and 20 KB took 38 s (#294, measured on the previous pattern).
_SECRET_KEY_NAME = (
    r"(?<![A-Za-z0-9_.\-])[A-Za-z0-9_.\-]{0,64}?"
    r"(?:token|secret|passw(?:or)?d|pwd|api[_\-]?key|apikey|access[_\-]?key|credential|"
    r"private[_\-]?key|authorization|cookie|session[_\-]?(?:key|id)|signature)[A-Za-z0-9_.\-]{0,64}"
)


def _keep_quotes(match: re.Match[str]) -> str:
    """``key=<value>`` -> ``key=REDACTED``, keeping a quoted value's quotes so
    a JSON line still reads as JSON."""
    prefix, value = match.group(1), match.group(2)
    if value[:1] in "\"'":
        return f"{prefix}{value[0]}{REDACTED}{value[0]}"
    return f"{prefix}{REDACTED}"


_Replacement = str | Callable[[re.Match[str]], str]

_PATTERNS: tuple[tuple[re.Pattern[str], _Replacement], ...] = (
    # PEM blocks first — they span lines.
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL),
        REDACTED,
    ),
    # The runner's --config payload carries prompts and per-sandbox bearers.
    (re.compile(r"(--config(?:-json)?[= ]+)[^\n]+"), rf"\1{REDACTED}"),
    # HTTP auth headers, any scheme: "Authorization: Bearer x", "x-api-key: y".
    (
        re.compile(
            r"(?i)\b((?:proxy-)?authorization|x-api-key|x-auth-token|private-token|cookie|set-cookie)(\s*[:=]\s*)[^\r\n]+"
        ),
        rf"\1\2{REDACTED}",
    ),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}"), rf"\1 {REDACTED}"),
    # Credentials in a URL: scheme://user:pass@host. Every repetition is
    # BOUNDED: with an open-ended scheme class the engine rescans the whole
    # remaining text from each starting position looking for "://", which is
    # quadratic — a 200 KiB unbroken word (a base64 blob in a traceback is
    # exactly that) took 16s. A scheme is a handful of characters and neither
    # a userinfo nor a password runs to 256.
    (re.compile(r"([A-Za-z][A-Za-z0-9+.\-]{0,31}://)[^/\s:@]{1,256}:[^/\s@]{1,256}@"), rf"\1{REDACTED}@"),
    # KEY=value / KEY: value / "key": "value" with a secret-looking key name.
    (
        re.compile(rf"(?i)([\"']?{_SECRET_KEY_NAME}[\"']?\s*[:=]\s*)(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;&\"']+)"),
        _keep_quotes,
    ),
    # Known token formats, wherever they turn up.
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), REDACTED),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}"), REDACTED),
    (re.compile(r"\b(?:sk|xai|rk)-[A-Za-z0-9_\-]{12,}"), REDACTED),
    (re.compile(r"\blin_(?:api|oauth)_[A-Za-z0-9]{16,}"), REDACTED),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), REDACTED),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    # Telegram bot tokens, bare or inside a Bot API URL (".../bot<id>:<secret>/").
    (re.compile(r"(?<![0-9])[0-9]{6,12}:[A-Za-z0-9_\-]{35}(?![A-Za-z0-9_\-])"), REDACTED),
    # A Slack incoming-webhook URL is its own credential: the path is the secret.
    (re.compile(r"(hooks\.slack\.com/(?:services|workflows|triggers)/)[A-Za-z0-9/_\-]{1,256}"), rf"\1{REDACTED}"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), REDACTED),
)

_SECRET_ENV_NAME = re.compile(r"(?i)token|secret|passw|api_?key|credential|private_?key|database_url|dsn")
# A short value ("true", "1", "dev") would redact ordinary words all over the text.
_MIN_KNOWN_VALUE_LEN = 8

# Spec 2026-09-29 §5: secrets that never entered the environment (the
# credential registry) still have to be scrubbed. app.py registers them at
# startup; the credentials API re-registers after create/rotate.
_KNOWN_SECRETS: set[str] = set()
# The registry sorted for scrubbing, rebuilt only when it changes: it holds a
# value per live sandbox bearer, and secret_env_values() runs once per event.
_KNOWN_SORTED: list[str] | None = None


def _scrub_order(value: str) -> tuple[int, str]:
    """Longest first (a value containing another must go before it), then by
    value so the order is deterministic."""
    return (-len(value), value)


def register_known_secrets(values: Iterable[str], *, reset: bool = False) -> None:
    global _KNOWN_SORTED
    if reset:
        _KNOWN_SECRETS.clear()
    _KNOWN_SECRETS.update(v for v in values if isinstance(v, str) and len(v) >= _MIN_KNOWN_VALUE_LEN)
    _KNOWN_SORTED = None


def unregister_known_secrets(values: Iterable[str]) -> None:
    """Forget values that no longer need protecting, such as a sandbox
    bearer the proxies have revoked. Values never registered are ignored."""
    global _KNOWN_SORTED
    _KNOWN_SECRETS.difference_update(values)
    _KNOWN_SORTED = None


def known_secret_values() -> list[str]:
    return sorted(_KNOWN_SECRETS)


def secret_env_values(environ: dict[str, str] | None = None, *, extra: Iterable[str] = ()) -> list[str]:
    """Values of this process's secret-looking env vars plus the registered
    known secrets, longest first, so a secret that appears verbatim is caught
    even in a shape no pattern knows. Only the env part is sorted per call;
    the registry's order is cached. ``extra`` adds values a caller knows and
    this environment never held (a job's secret env vars, which only the
    sandbox's environment carried), under the same length floor."""
    global _KNOWN_SORTED
    if _KNOWN_SORTED is None:
        _KNOWN_SORTED = sorted(_KNOWN_SECRETS, key=_scrub_order)
    env = os.environ if environ is None else environ
    merged = list(_KNOWN_SORTED)
    values = {v for k, v in env.items() if _SECRET_ENV_NAME.search(k) and len(v) >= _MIN_KNOWN_VALUE_LEN}
    values.update(v for v in extra if isinstance(v, str) and len(v) >= _MIN_KNOWN_VALUE_LEN)
    for value in values:
        if value not in _KNOWN_SECRETS:
            bisect.insort(merged, value, key=_scrub_order)
    return merged


def redact_secrets(text: str, known_values: Iterable[str] = ()) -> str:
    """``text`` with secrets replaced by ``***REDACTED***``. Exact known
    values go first (a pattern could otherwise leave half of one behind)."""
    if not text:
        return text
    for value in known_values:
        if value and len(value) >= _MIN_KNOWN_VALUE_LEN:
            text = text.replace(value, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def tail_bytes(text: str, max_bytes: int = STDERR_TAIL_MAX_BYTES) -> str:
    """The last ``max_bytes`` of ``text`` (UTF-8), cut on a character
    boundary and, when it fits, on a line boundary. Diagnostics are prose:
    truncating them is fine, and the newest lines are the ones that explain
    an exit."""
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text
    cut = raw[-max_bytes:].decode("utf-8", errors="ignore")
    newline = cut.find("\n")
    if 0 <= newline < len(cut) - 1:
        cut = cut[newline + 1 :]
    return cut


LOG_FIELD_MAX_CHARS = 2000


def redacted_log_field(
    text: str | None,
    known_values: Iterable[str] | None = None,
    *,
    limit: int = LOG_FIELD_MAX_CHARS,
) -> str:
    """A free-text log field: secrets removed, then length-capped.

    Redact before capping. A cap that sliced a token in half would leave a
    fragment no pattern recognises, and that fragment is what would ship.
    """
    if not text:
        return ""
    values = secret_env_values() if known_values is None else list(known_values)
    return redact_secrets(text, values)[:limit]


def redacted_stderr_tail(stderr: str, known_values: Iterable[str] | None = None) -> str:
    """What may be STORED of an agent's stderr: redact the generous tail
    first, then cap — capping first could slice a token in half and leave a
    fragment no pattern recognises."""
    if not stderr or not stderr.strip():
        return ""
    values = secret_env_values() if known_values is None else list(known_values)
    generous = tail_bytes(stderr, STDERR_TAIL_MAX_BYTES * 4)
    dropped = stderr[: len(stderr) - len(generous)]
    if dropped and not dropped.endswith("\n"):
        # The window opened mid-line, possibly mid-token: a token's back half
        # matches no pattern, so the partial first word goes.
        generous = re.sub(r"^\S+", "", generous)
    return tail_bytes(redact_secrets(generous, values), STDERR_TAIL_MAX_BYTES).strip()


# ---- log text: what reaches a model provider or a tracker (#294) -----------

_PII_PATTERNS: tuple[tuple[re.Pattern[str], _Replacement], ...] = (
    # E-mail addresses. The lookbehind lets a match start only where a local
    # part starts, and every part is bounded, so a long dotted run costs one
    # bounded scan instead of a rescan from each position.
    (
        re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]{1,256}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){1,8}"),
        EMAIL_PLACEHOLDER,
    ),
    # Any remaining long opaque run carrying a digit: a hex digest, a base64
    # key, a session id, a key of a provider no rule above knows. Anchored to
    # the start of a run, so the lookahead scans each run once.
    (re.compile(r"(?<![A-Za-z0-9+/_\-])(?=[A-Za-z0-9+/_\-]*[0-9])[A-Za-z0-9+/_\-]{32,}={0,2}"), REDACTED),
)

# Operator-configured payload fields (LOG_REDACTION_FIELDS): key names whose
# VALUE is personal data in that operator's logs — "phone", "customer_name".
_FIELD_NAME_OK = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_fields_pattern: re.Pattern[str] | None = None


def register_redacted_fields(names: Iterable[str] | str) -> list[str]:
    """Set the payload field names whose values :func:`redact_log_text`
    removes, replacing any earlier set. Accepts a comma-separated string (the
    env var's shape). Names that are not plain identifiers are ignored rather
    than compiled into a pattern. Returns the names in effect."""
    global _fields_pattern
    raw = names.split(",") if isinstance(names, str) else names
    clean = sorted({n.strip() for n in raw if isinstance(n, str) and _FIELD_NAME_OK.fullmatch(n.strip())})
    _fields_pattern = (
        re.compile(
            rf"(?i)([\"']?(?<![A-Za-z0-9_.\-])(?:{'|'.join(re.escape(n) for n in clean)})[\"']?\s*[:=]\s*)"
            r"(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;&\"'})\]]+)"
        )
        if clean
        else None
    )
    return clean


def redact_log_text(text: str, known_values: Iterable[str] | None = None) -> str:
    """``text`` with secrets AND personal data replaced — for anything sent to
    a model provider or filed in a tracker. Idempotent.

    ``known_values`` defaults to this process's secret env values plus the
    registered known secrets; a caller redacting many lines computes
    :func:`secret_env_values` once and passes it.
    """
    if not text:
        return text
    text = redact_secrets(text, secret_env_values() if known_values is None else known_values)
    fields = _fields_pattern
    if fields is not None:
        text = fields.sub(_keep_quotes, text)
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# How much text beyond a caller's limit the redactor still sees, so a credential
# straddling the cut is matched whole rather than as an unrecognizable fragment.
REDACT_OVERLAP_CHARS = 256


def redact_bounded(
    text: str,
    limit: int,
    *,
    redact: Callable[[str], str] = redact_log_text,
) -> str:
    """Redact and cut to ``limit``, with the redactor seeing bounded input.

    Redacting the whole text first makes the cost depend on input the caller
    does not control; cutting first can hand the redactor half a credential.
    So the window is ``limit`` plus an overlap wide enough for a straddling
    token, and the result is cut again afterwards — a cut landing inside a
    marker leaves a harmless fragment of the marker, not of the secret.
    """
    if limit <= 0:
        return ""
    return redact(text[: limit + REDACT_OVERLAP_CHARS])[:limit]


# ---- one event, every string -------------------------------------------------

#: Top-level event keys that carry identifiers, never text. Left as they are
#: so a scrub can never break the pairing of a result with its call or of an
#: event with its run.
EVENT_KEYS_NOT_REDACTED = frozenset(
    {
        "type",
        "timestamp",
        "job_id",
        "event_seq",
        "run_id",
        "node",
        "agent",
        "parent_tool_use_id",
        "tool_id",
        "tool_name",
    }
)


def redact_event(event: dict[str, Any], known_values: Iterable[str]) -> dict[str, Any]:
    """A copy of ``event`` with every string value passed through
    :func:`redact_secrets`, recursively through dicts and lists, so a secret
    inside ``tool_input`` or a content block is caught too. The input is not
    mutated. Top-level correlation keys are left as they are."""
    values = list(known_values)

    def _walk(value: Any) -> Any:
        if isinstance(value, str):
            return redact_secrets(value, values)
        if isinstance(value, dict):
            return {k: _walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v) for v in value]
        return value

    return {k: (v if k in EVENT_KEYS_NOT_REDACTED else _walk(v)) for k, v in event.items()}
