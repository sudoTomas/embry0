"""Slop is not a bug (spec 2026-10-25, slop dungeon).

docs/superpowers/specs/2026-10-25-slop-dungeon-design.md. A finding or a code
job has a nature: a **bug** (the behavior differs from what is intended), **slop**
(the behavior is what the code says, and the trouble is its shape) or **noise**
(nothing to do). Handing slop to a developer as a bug gets the symptom patched
and the slop deepened, so the watcher and triage classify first, and a slop job
runs as a cleanup by the Slopmaster.

This module holds the one shape of that verdict (the slopometer), its lenient
parser, the words the classifiers and the Slopmaster are given, and the Slop
Dungeon's score.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

NATURES: tuple[str, ...] = ("bug", "slop", "noise")
SLOP_KINDS: tuple[str, ...] = (
    "duplication",
    "dead_code",
    "half_wired",
    "contradicting_layers",
    "symptom_patch",
    "misleading_docs",
    "off_pattern",
)
MAX_EVIDENCE = 8
_WHY_CHARS = 300
_PATH_CHARS = 300

#: The agent that cleans slop, on the developer node (route_plan.CODING_AGENT_TYPES).
SLOPMASTER = "slopmaster"
#: The template a slop job runs (storage/seeds/pipeline_templates_builtin.py).
SLOP_TEMPLATE = "slop-cleanup"

#: What the classifiers are told slop is: triage's prompt and the watcher's contract both quote it.
CLASSIFIER_GUIDE = """\
Tell slop apart from bugs:
- bug: the behavior differs from what the code, its docs or the ticket intend.
  Fixing it changes behavior.
- slop: the behavior is what the code says, and the trouble is the code's
  shape. Cleaning it keeps behavior. Kinds: duplication (the same logic in
  more than one place), dead_code (paths, flags or files nothing reaches),
  half_wired (a feature or abstraction started and not finished),
  contradicting_layers (two layers that disagree about one thing),
  symptom_patch (guards, retries, fallbacks or catch-alls stacked on a symptom
  instead of its cause), misleading_docs (comments or docs that say what the
  code does not do), off_pattern (code that ignores the repository's own way).
- noise: nothing to do.
A guard, retry or catch-all stacked on a symptom is slop when removing it and
fixing the shape keeps the outcome. When the code does the wrong thing (keeps
retrying what can never succeed, drops or duplicates data, answers wrongly),
it is a bug, even when sloppy code caused it: say bug, and name the slop in
the evidence. Do not call a real defect slop to make it look smaller.
slop_score (0-100) is how much of the trouble is shape rather than behavior:
0 for a pure bug, 100 for pure slop."""

#: The Slopmaster's system prompt (its builtin agent definition).
SLOPMASTER_SYSTEM_PROMPT = """\
You are the Slopmaster. The task in front of you was judged SLOP, not a bug:
the code does what it says, and the trouble is how it is shaped. Your job is a
cleanup, not a fix. You work on a branch and open a pull request like the
developer does; the task prompt has the mechanics.

1. First find out what the code is meant to do: its tests, its callers, its
   docs, its history (git log, blame). Write that intent down in your notes
   before you change anything.
2. Keep that behavior exactly. Run the tests before and after. Where no test
   covers the path you clean, add a characterization test first.
3. Delete, merge, inline and simplify. Prefer a diff that removes more lines
   than it adds. One clear path beats two drifting copies.
4. Never add a guard, retry, fallback, catch-all or flag to make a symptom go
   away. If, while cleaning, you find the cause is a real bug, stop and say so
   in your summary instead of patching it.
5. Follow the repository's own patterns, and remove or correct the comment or
   doc that said something the code does not do.

Your summary says what was slop, what you removed or merged, and how the tests
show that behavior did not change."""

#: What the reviewer is told when the change under review is a slop cleanup.
SLOP_REVIEW_GUIDE = """\
## This change is a slop cleanup

It was judged slop, not a bug: it must keep behavior and improve shape.
Approve only when all of these hold, and request changes otherwise, saying which:
- behavior is unchanged: the tests pass, and no public behavior, API or output
  moved (removing what nothing reaches is fine);
- the change removes or merges more than it adds, or is plainly simpler;
- it adds no new guard, retry, fallback, catch-all or flag to hide a symptom."""


def _clamp_score(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return max(0, min(100, int(round(number))))


def parse_work_nature(raw: Any) -> dict[str, Any] | None:
    """The slopometer's verdict in its one shape, or None when there is none.

    Lenient on purpose: a classifier's verdict steers routing and framing, it
    never fails a job. A nature outside bug/slop/noise means no verdict; an
    unknown kind is dropped; a score is clamped to 0..100 (a slop verdict with
    no score reads as 100, a bug as 0); evidence keeps its first entries.
    """
    if not isinstance(raw, Mapping):
        return None
    nature = str(raw.get("nature") or "").strip().lower()
    if nature not in NATURES:
        return None
    score = _clamp_score(raw.get("slop_score"))
    if score is None:
        score = 100 if nature == "slop" else 0
    kinds_raw = raw.get("kinds") or []
    if isinstance(kinds_raw, str):
        kinds_raw = [k for k in re.split(r"[,\s]+", kinds_raw) if k]
    kinds: list[str] = []
    for kind in kinds_raw if isinstance(kinds_raw, list) else []:
        k = str(kind).strip().lower().replace("-", "_").replace(" ", "_")
        if k in SLOP_KINDS and k not in kinds:
            kinds.append(k)
    evidence: list[dict[str, str]] = []
    items = raw.get("evidence")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("path") or "").strip()[:_PATH_CHARS]
        why = str(item.get("why") or "").strip()[:_WHY_CHARS]
        if path or why:
            evidence.append({"path": path, "why": why})
        if len(evidence) >= MAX_EVIDENCE:
            break
    verdict: dict[str, Any] = {"nature": nature, "slop_score": score, "kinds": kinds, "evidence": evidence}
    cleanup = raw.get("cleanup")
    if isinstance(cleanup, Mapping):
        counts = {k: int(cleanup[k]) for k in ("added", "removed") if isinstance(cleanup.get(k), int)}
        if len(counts) == 2:
            verdict["cleanup"] = counts
    return verdict


def is_slop(verdict: Mapping[str, Any] | None) -> bool:
    return isinstance(verdict, Mapping) and verdict.get("nature") == "slop"


_LINE_RE = {
    "nature": re.compile(r"^NATURE:[ \t]*(\w+)[ \t]*$", re.MULTILINE),
    "score": re.compile(r"^SLOP_SCORE:[ \t]*(\d{1,3})\b", re.MULTILINE),
    "kinds": re.compile(r"^SLOP_KINDS:[ \t]*(.+)$", re.MULTILINE),
    "evidence": re.compile(r"^SLOP_EVIDENCE:[ \t]*([^|\n]*)\|(.+)$", re.MULTILINE),
}


def parse_contract_lines(text: str) -> dict[str, Any] | None:
    """The watcher's NATURE:/SLOP_SCORE:/SLOP_KINDS:/SLOP_EVIDENCE: lines, as a verdict.

    Read from the head of a proposal only (before ``BODY:``), so a body that
    quotes these words is not read as a verdict. None without a NATURE line.
    """
    head = text.split("\nBODY:", 1)[0]
    nature = _LINE_RE["nature"].search(head)
    if nature is None:
        return None
    score = _LINE_RE["score"].search(head)
    kinds = _LINE_RE["kinds"].search(head)
    evidence = [{"path": m.group(1).strip(), "why": m.group(2).strip()} for m in _LINE_RE["evidence"].finditer(head)]
    return parse_work_nature(
        {
            "nature": nature.group(1),
            "slop_score": int(score.group(1)) if score else None,
            "kinds": kinds.group(1) if kinds else [],
            "evidence": evidence,
        }
    )


#: The watcher's contract lines, rendered into its analysis prompt before BODY:.
WATCHER_CONTRACT_LINES = """\
NATURE: <bug|slop — see "Bug or slop" above>
SLOP_SCORE: <0-100>
SLOP_KINDS: <comma-separated kinds, for slop>
SLOP_EVIDENCE: <path> | <why>   (up to 8 lines, for slop)"""


def _kinds_words(verdict: Mapping[str, Any]) -> str:
    return ", ".join(str(k).replace("_", " ") for k in verdict.get("kinds") or []) or "unspecified"


def ticket_section(verdict: Mapping[str, Any]) -> str:
    """The **Slop, not a bug** section a slop finding's draft ticket opens with."""
    lines = [
        "## Slop, not a bug",
        f"The watcher judged this slop (slop score {verdict.get('slop_score')}/100: {_kinds_words(verdict)}). "
        "The code does what it says; the trouble is its shape. Clean it up — keep behavior, remove or merge "
        "code — instead of adding another guard or retry.",
    ]
    for item in verdict.get("evidence") or []:
        lines.append(f"- `{item.get('path')}`: {item.get('why')}".rstrip(": "))
    return "\n".join(lines) + "\n\n"


def slop_brief(verdict: Mapping[str, Any] | None) -> str:
    """The verdict as the cleanup's job prompt carries it ('' without one)."""
    if verdict is None or not is_slop(verdict):
        return ""
    lines = [
        "## Slop verdict",
        f"Triage judged this slop, not a bug: slop score {verdict.get('slop_score')}/100 ({_kinds_words(verdict)}).",
        "Keep behavior; change shape. Do not add guards, retries or fallbacks.",
    ]
    evidence = verdict.get("evidence") or []
    if evidence:
        lines.append("Evidence:")
        lines += [f"- {e.get('path') or '(no path)'}: {e.get('why') or ''}".rstrip(": ") for e in evidence]
    return "\n".join(lines)


def dungeon_score(scores: Iterable[int]) -> int:
    """A Slop Dungeon's score from its open slop: 1 − Π(1 − s/100), as a percentage.

    One heavy item or many light ones raise it toward 100; it falls as each is
    cleaned, and an empty dungeon scores 0.
    """
    clean = 1.0
    for score in scores:
        clean *= 1 - max(0, min(100, int(score))) / 100
    return int(round(100 * (1 - clean)))


#: The Slop-O-Meter level at which an area has a dungeon even with no open slop finding (Spaghetti Bolognese).
METER_DUNGEON_LEVEL = 2


def meter_view(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A Slop-O-Meter score row as the world and the repository page show it (spec 2026-10-26, slop-o-meter)."""
    if not row:
        return None
    computed = row.get("computed_at")
    return {
        "score": round(float(row.get("score") or 0.0), 4),
        "level": int(row.get("level") or 1),
        "name": str(row.get("name") or ""),
        "provisional": bool(row.get("provisional")),
        "computed_at": computed.isoformat() if isinstance(computed, date) else computed,
    }


def dungeons(
    leads: Iterable[Mapping[str, Any]],
    cleaned: Mapping[tuple[str, str | None], int],
    meters: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """One Slop Dungeon per repository and area that has open slop, recent cleanups or a sloppy meter, worst first.

    Each carries its Slop-O-Meter reading (``meter``, None when not scored);
    an area at Spaghetti Bolognese or worse has a dungeon even with no open
    slop finding.
    """
    open_scores: dict[tuple[str, str | None], list[int]] = {}
    for lead in leads:
        if lead.get("nature") != "slop" or not lead.get("repo"):
            continue
        key = (str(lead["repo"]), lead.get("area") or None)
        open_scores.setdefault(key, []).append(int(lead.get("slop_score") or 0))
    by_scope = {(str(m["repo"]), m.get("area") or None): m for m in meters}
    keys = set(open_scores) | {k for k, n in cleaned.items() if n}
    keys |= {k for k, m in by_scope.items() if int(m.get("level") or 1) >= METER_DUNGEON_LEVEL}
    out = [
        {
            "repo": repo,
            "area": area,
            "score": dungeon_score(open_scores.get((repo, area), [])),
            "open": len(open_scores.get((repo, area), [])),
            "cleaned": int(cleaned.get((repo, area), 0)),
            "meter": meter_view(by_scope.get((repo, area))),
        }
        for repo, area in keys
    ]
    return sorted(out, key=lambda d: (-d["score"], -d["open"], d["repo"], d["area"] or ""))
