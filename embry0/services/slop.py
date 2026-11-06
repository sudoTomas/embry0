"""Slop is not a bug (spec 2026-10-25, slop dungeon).

docs/superpowers/specs/2026-10-25-slop-dungeon-design.md. A finding or a code
job has a nature: a **bug** (the behavior differs from what is intended), **slop**
(the behavior is what the code says, and the trouble is its shape) or **noise**
(nothing to do). Handing slop to a developer as a bug gets the symptom patched
and the slop deepened, so the watcher and triage classify first, and a slop job
runs as a cleanup by the Slopmaster.

This module holds the one shape of that verdict (the slopometer), its lenient
parser, the words the Slopmaster and a slop finding's ticket are given, and
the Slop Dungeon's score. What the policy decides (the kinds a verdict may
name, what the classifiers and the reviewer are told, the ticket's heading,
the route and the meter levels acted on) is the settings document's
``slop_policy`` section (:mod:`embry0.settings.sections.slop_policy`): each
function here takes the part it needs from the caller, who read the section
for the job's or the finding's repository.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from datetime import date
from typing import Any

from embry0.settings.sections.slop_policy import KIND, MAX_KIND_LENGTH

NATURES: tuple[str, ...] = ("bug", "slop", "noise")
MAX_EVIDENCE = 8
_WHY_CHARS = 300
_PATH_CHARS = 300

#: The agent that cleans slop, on the developer node (row_behaviour.CODING_AGENT_TYPES).
SLOPMASTER = "slopmaster"

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


def _kind(raw: Any) -> str:
    """A kind as a verdict names it once read: lowercase, ``-`` and spaces as ``_``."""
    return str(raw).strip().lower().replace("-", "_").replace(" ", "_")


def parse_work_nature(raw: Any, *, kinds: Collection[str] | None) -> dict[str, Any] | None:
    """The slopometer's verdict in its one shape, or None when there is none.

    Lenient on purpose: a classifier's verdict steers routing and framing, it
    never fails a job. A nature outside bug/slop/noise means no verdict; a
    kind not in ``kinds`` (the ``slop_policy.kinds`` of the job's or the
    finding's repository) is dropped; a score is clamped to 0..100 (a slop
    verdict with no score reads as 100, a bug as 0); evidence keeps its first
    entries. ``kinds=None`` reads back a verdict stored after it was parsed:
    each kind it names is kept when it has a kind's shape, so a kind an
    organization added is not dropped by a reader of another.
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
    named: list[str] = []
    for kind in kinds_raw if isinstance(kinds_raw, list) else []:
        k = _kind(kind)
        known = k in kinds if kinds is not None else len(k) <= MAX_KIND_LENGTH and KIND.fullmatch(k) is not None
        if known and k not in named:
            named.append(k)
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
    verdict: dict[str, Any] = {"nature": nature, "slop_score": score, "kinds": named, "evidence": evidence}
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


def parse_contract_lines(text: str, *, kinds: Collection[str]) -> dict[str, Any] | None:
    """The watcher's NATURE:/SLOP_SCORE:/SLOP_KINDS:/SLOP_EVIDENCE: lines, as a verdict naming only ``kinds``.

    Read from the head of a proposal only (before ``BODY:``), so a body that
    quotes these words is not read as a verdict. None without a NATURE line.
    """
    head = text.split("\nBODY:", 1)[0]
    nature = _LINE_RE["nature"].search(head)
    if nature is None:
        return None
    score = _LINE_RE["score"].search(head)
    named = _LINE_RE["kinds"].search(head)
    evidence = [{"path": m.group(1).strip(), "why": m.group(2).strip()} for m in _LINE_RE["evidence"].finditer(head)]
    return parse_work_nature(
        {
            "nature": nature.group(1),
            "slop_score": int(score.group(1)) if score else None,
            "kinds": named.group(1) if named else [],
            "evidence": evidence,
        },
        kinds=kinds,
    )


#: The watcher's contract lines, rendered into its analysis prompt before BODY:.
WATCHER_CONTRACT_LINES = """\
NATURE: <bug|slop — see "Bug or slop" above>
SLOP_SCORE: <0-100>
SLOP_KINDS: <comma-separated kinds, for slop>
SLOP_EVIDENCE: <path> | <why>   (up to 8 lines, for slop)"""


def _kinds_words(verdict: Mapping[str, Any]) -> str:
    return ", ".join(str(k).replace("_", " ") for k in verdict.get("kinds") or []) or "unspecified"


def ticket_section(verdict: Mapping[str, Any], *, heading: str) -> str:
    """The section a slop finding's draft ticket opens with, under ``heading`` (``slop_policy.ticket_heading``)."""
    lines = [
        f"## {heading}",
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


def lean_names(level_names: Iterable[str], min_level: int | None) -> tuple[str, ...]:
    """The Slop-O-Meter level names from ``min_level`` up (``slop_policy.triage_lean_min_level``); none for None."""
    if min_level is None:
        return ()
    return tuple(level_names)[min_level - 1 :]


def level_phrase(names: Iterable[str]) -> str:
    """Level names as a sentence names them: ``A``, ``A or B``, ``A, B or C``."""
    words = list(names)
    if len(words) <= 1:
        return "".join(words)
    return f"{', '.join(words[:-1])} or {words[-1]}"


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
    meters: Iterable[Mapping[str, Any]],
    *,
    min_level: Callable[[str], int | None],
) -> list[dict[str, Any]]:
    """One Slop Dungeon per repository and area that has open slop, recent cleanups or a sloppy meter, worst first.

    Each carries its Slop-O-Meter reading (``meter``, None when not scored).
    An area whose reading is at its repository's ``min_level`` or worse
    (``slop_policy.dungeon_min_level``, Spaghetti Bolognese by default) has
    a dungeon even with no open slop finding; with None, its meter alone
    makes none.
    """
    open_scores: dict[tuple[str, str | None], list[int]] = {}
    for lead in leads:
        if lead.get("nature") != "slop" or not lead.get("repo"):
            continue
        key = (str(lead["repo"]), lead.get("area") or None)
        open_scores.setdefault(key, []).append(int(lead.get("slop_score") or 0))
    by_scope = {(str(m["repo"]), m.get("area") or None): m for m in meters}
    keys = set(open_scores) | {k for k, n in cleaned.items() if n}
    keys |= {k for k, m in by_scope.items() if _sloppy(int(m.get("level") or 1), min_level(k[0]))}
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


def _sloppy(level: int, min_level: int | None) -> bool:
    return min_level is not None and level >= min_level
