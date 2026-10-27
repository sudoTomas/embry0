"""Deterministic log-signature clustering (SH-P3.1, #289).

The watcher used to hand the model the newest N raw lines and let it do the
grouping. Two things go wrong with that. A volume spike crowds everything else
out of the window — measured on this deployment, **87 of 309 analysed ticks
(28.2%) carried 120+ lines**, and a 9,073-line frontend DNS storm on 2026-09-18
was never filed because it buried every other signature. And nothing tracks a
signature *over time*, so "is this new?" and "is this getting worse?" were
questions no tick could answer.

This module is the deterministic half: normalize each line to a template, hash
the template into a stable signature id, and cluster. It is pure — no I/O, no
database, no model — so it is testable on a real corpus and gives the same
answer twice.

Design notes worth keeping:

* **Normalization order matters.** UUIDs before bare hex before numbers, or a
  UUID's digits get eaten by the number rule first and two different UUIDs stop
  collapsing to the same template.
* **Quoted identifiers are kept.** `duplicate key value violates unique
  constraint "idx_email_message_external_id"` is one signature per constraint,
  not one signature for all of them — the constraint name is the whole
  diagnostic value. Only digits inside quotes are normalized.
* **Templates are capped.** A Java stack frame chain can run to kilobytes; the
  head carries the identity.
* Clustering ranks by **count**, and `LogCluster.rate_per_hour` is derived from
  the cluster's own first/last timestamps rather than the tick window, so a
  burst that lasted 90 seconds does not read as a low hourly rate just because
  the window was an hour wide.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime


def _is_opaque_token(token: str) -> bool:
    """Does this look like a generated id rather than a meaningful word?

    Requires all three of digit, upper and lower case. That is what separates
    ``AAMkADAwMDBhYWFhLTExMTEt…`` from ``LazyInitializationException`` (no
    digits) and from ``MAX_CONNECTIONS_EXCEEDED`` (no lower case) — masking
    either of those would destroy the signature's diagnostic value.
    """
    if "." in token:
        return False
    return any(c.isdigit() for c in token) and any(c.islower() for c in token) and any(c.isupper() for c in token)


#: An upper-case record id: letters and digits, at least 8 long, at least 5 of them digits.
_MIN_RECORD_ID_LEN = 8
_MIN_RECORD_ID_DIGITS = 5


def _record_id(match: re.Match[str]) -> str:
    """``<id>`` for a generated record id such as an order or shipment number, the token otherwise.

    The opaque-token rule needs lower case too, so ids such as
    ``ORD00000000001`` and ``ORD7A000000042`` passed through it, and a
    recurring warning that named a different record each time became a new
    signature per record. Codes stay
    as they are (``E11000``, ``SHA256``, ``PGRST002``, ``HTTP2``): too short or
    too few digits.
    """
    token = match.group(0)
    digits = sum(c.isdigit() for c in token)
    return "<id>" if len(token) >= _MIN_RECORD_ID_LEN and digits >= _MIN_RECORD_ID_DIGITS else token


# Ordered deliberately — see the module docstring. Each entry is (pattern,
# replacement); they are applied in sequence to one line. A replacement may be
# a callable for rules that need to inspect the match before deciding.
_RULES: tuple[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]], ...] = (
    # Leading timestamps, in the formats this fleet actually emits.
    # Postgres:  2026-09-20 20:47:09.876 UTC
    # nginx:     2026/09/21 01:44:53
    # ISO/Java:  2026-09-20T20:47:09.876Z / +00:00
    (re.compile(r"^\s*\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\s*(?:UTC|Z|[+-]\d{2}:?\d{2})?\s*"), ""),
    # Bracketed pid / thread / worker, e.g. Postgres "[1913152]".
    (re.compile(r"\[\d+\]"), "[<n>]"),
    # nginx worker#pid and connection counter: "30#30: *1"
    (re.compile(r"\b\d+#\d+\b"), "<n>#<n>"),
    (re.compile(r"\*\d+\b"), "*<n>"),
    # Email addresses. Two reasons, both load-bearing. Clustering: the same
    # "MS Graph fetch failed" error split into 7 signatures, one per mailbox,
    # which is one fault, not seven. Exposure: #294 measured that redaction
    # removes 100% of emails but the watcher was sending log lines to models
    # unredacted — masking them in the template means the ranked table a model
    # reads carries no addresses at all. Sample lines still do, so they remain
    # the redactor's job.
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    # UUIDs — before hex, before numbers.
    (
        re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
        "<uuid>",
    ),
    # IPv4, optionally with a port.
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    # Long bare hex (object ids, sha, trace ids) — before the number rule.
    (re.compile(r"\b[0-9a-fA-F]{8,}\b"), "<hex>"),
    # Opaque tokens the hex rule cannot see: base64url / Graph ids / JWT-ish
    # blobs, e.g. an MS Graph notification id
    # "AAMkADAwMDBhYWFhLTExMTEt…Ex4m-AAPl3Qa7AAA=". Mixed case + digits with no
    # dots is the discriminator: it keeps dotted class names
    # ("org.springframework.boot.security.oauth2…") and long single-case words
    # intact, which carry real diagnostic meaning. Must precede the number
    # rule, which would otherwise shred the token into fragments.
    (re.compile(r"[A-Za-z0-9_+/=-]{20,}"), lambda m: "<id>" if _is_opaque_token(m.group(0)) else m.group(0)),
    # Upper-case record ids the rule above cannot see (no lower case), e.g. an
    # order number "ORD7A000000042". Before the number rule for the same reason.
    (re.compile(r"\b[A-Z][A-Z0-9]*\d[A-Z0-9]*\b"), _record_id),
    # Durations, sizes and other number-with-unit forms.
    (re.compile(r"\b\d+(?:\.\d+)?(ms|s|m|h|ns|us|µs|KB|MB|GB|kB|B)\b"), r"<n>\1"),
    # Any remaining number, including decimals and negatives.
    (re.compile(r"(?<![\w<])-?\d+(?:\.\d+)?(?![\w>])"), "<n>"),
    # Logback truncates thread names from the LEFT (`%.15t`), so
    # "[MessageBroker-11]" logs as "[essageBroker-11]" while
    # "[MessageBroker-5]" logs in full — the same logger split into two
    # signatures. Any bracketed field that starts with a letter and contains a
    # normalized number is a thread/worker slot, so mask the whole field.
    # "[backend]" (an app name, no number) is deliberately preserved,
    # and "[<n>]" (a bare pid) is left to the rule above.
    (re.compile(r"\[[A-Za-z][^\]]*<n>[^\]]*\]"), "[<thread>]"),
    # Collapse whitespace last so earlier patterns see the original spacing.
    (re.compile(r"\s+"), " "),
)

#: Templates longer than this are truncated — the head carries the identity.
TEMPLATE_MAX_CHARS = 320

#: Sample lines kept per cluster for the model to read.
DEFAULT_SAMPLES_PER_CLUSTER = 2


def normalize_line(line: str) -> str:
    """One raw log line -> its template, with variable parts masked.

    Deterministic and side-effect free: the same line always yields the same
    template, which is what makes the signature id stable across ticks.
    """
    out = line if isinstance(line, str) else ""
    for pattern, replacement in _RULES:
        out = pattern.sub(replacement, out)
    out = out.strip()
    if len(out) > TEMPLATE_MAX_CHARS:
        out = out[:TEMPLATE_MAX_CHARS] + "…"
    return out


def signature_id(template: str) -> str:
    """Stable short id for a template.

    Truncated SHA-256 rather than :func:`hash`, which is salted per process and
    would give a different id on every restart — the id is persisted and
    compared across ticks, so it has to survive one.
    """
    return hashlib.sha256(template.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass(frozen=True)
class LogEntry:
    """One log line with whatever context the caller has.

    ``service`` and ``timestamp`` come from the log *stream* (Loki labels),
    not from parsing the line, so they stay trustworthy when a line's own
    prefix is missing or malformed.
    """

    line: str
    timestamp: datetime | None = None
    service: str | None = None


@dataclass
class LogCluster:
    """One signature, and everything a tick knows about it in this window."""

    signature_id: str
    template: str
    count: int
    samples: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    @property
    def span_seconds(self) -> float:
        """Seconds between this cluster's own first and last line (0 if unknown)."""
        if self.first_seen is None or self.last_seen is None:
            return 0.0
        return max((self.last_seen - self.first_seen).total_seconds(), 0.0)

    @property
    def rate_per_hour(self) -> float | None:
        """Occurrences per hour over the cluster's OWN span, not the window.

        None when the span is unknown or instantaneous — a single line, or a
        burst inside one second, has no meaningful rate and inventing one
        (dividing by the window) understates a spike by orders of magnitude.
        """
        span = self.span_seconds
        if span <= 0:
            return None
        return self.count * 3600.0 / span


def cluster_entries(
    entries: Iterable[LogEntry | str],
    *,
    samples_per_cluster: int = DEFAULT_SAMPLES_PER_CLUSTER,
) -> list[LogCluster]:
    """Cluster log entries by signature, ranked by count (descending).

    Ties break on the template so the ordering is total and therefore
    reproducible — an unstable order would make the shadow comparison in #289
    look like disagreement when nothing had changed.
    """
    buckets: dict[str, LogCluster] = {}
    for raw in entries:
        entry = LogEntry(line=raw) if isinstance(raw, str) else raw
        if not entry.line or not entry.line.strip():
            continue
        template = normalize_line(entry.line)
        if not template:
            continue
        sig = signature_id(template)
        cluster = buckets.get(sig)
        if cluster is None:
            cluster = LogCluster(signature_id=sig, template=template, count=0)
            buckets[sig] = cluster
        cluster.count += 1
        if len(cluster.samples) < samples_per_cluster:
            cluster.samples.append(entry.line.strip())
        if entry.service and entry.service not in cluster.services:
            cluster.services.append(entry.service)
        if entry.timestamp is not None:
            if cluster.first_seen is None or entry.timestamp < cluster.first_seen:
                cluster.first_seen = entry.timestamp
            if cluster.last_seen is None or entry.timestamp > cluster.last_seen:
                cluster.last_seen = entry.timestamp
    return sorted(buckets.values(), key=lambda c: (-c.count, c.template))


def cluster_stats(clusters: Sequence[LogCluster]) -> dict[str, int]:
    """Totals for logging and the shadow comparison."""
    return {
        "clusters": len(clusters),
        "lines": sum(c.count for c in clusters),
        "singletons": sum(1 for c in clusters if c.count == 1),
    }


def format_cluster_table(
    clusters: Sequence[LogCluster],
    *,
    max_clusters: int = 25,
    max_sample_chars: int = 220,
    redact: Callable[[str], str] | None = None,
) -> str:
    """The ranked cluster block that replaces the raw tail in the prompt.

    Ranked by count, so a spike cannot push a rarer signature out of the
    window — the whole point of #289. The omitted tail is stated rather than
    silently dropped, because "there were 40 more signatures" is itself a
    finding the model should see.

    ``redact`` is applied to each sample BEFORE it is trimmed: a cut first
    could leave half a token that no rule recognises (#294).
    """
    if not clusters:
        return "(no matching log lines in this window)"
    shown = clusters[:max_clusters]
    parts: list[str] = []
    for index, cluster in enumerate(shown, start=1):
        head = f"{index}. [{cluster.count}×] sig={cluster.signature_id}"
        if cluster.services:
            head += f" services={','.join(sorted(cluster.services))}"
        rate = cluster.rate_per_hour
        if rate is not None:
            head += f" ~{rate:,.0f}/h over {cluster.span_seconds:,.0f}s"
        parts.append(head)
        parts.append(f"   template: {cluster.template}")
        for raw_sample in cluster.samples:
            sample = redact(raw_sample) if redact is not None else raw_sample
            trimmed = sample if len(sample) <= max_sample_chars else sample[:max_sample_chars] + "…"
            parts.append(f"   sample:   {trimmed}")
    hidden = clusters[max_clusters:]
    if hidden:
        parts.append(
            f"\n… and {len(hidden)} more signature(s) accounting for "
            f"{sum(c.count for c in hidden)} line(s), omitted by rank."
        )
    return "\n".join(parts)
