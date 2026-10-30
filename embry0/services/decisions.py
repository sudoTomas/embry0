"""Decisions (spec 2026-10-27): one shape for every choice a person makes about a quest.

A quest on offer (an owner proposal or lead) and a quest done (a finished job
waiting for its review) each become a :class:`Decision`: a verdict read before
anything else, the facts that decide it, one sentence of why, what a yes starts,
and the actions open. The dashboard's cards and a chat's cards render the same
thing, so what a person sees cannot drift between them.

Everything here is pure: the API gathers the rows and these functions shape them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from embry0.services.lead_kinds import KINDS

_SEVERITY_TONE = {"critical": "bad", "high": "bad", "medium": "warn", "low": "info"}
_TONE_RANK = {"bad": 0, "warn": 1, "good": 2, "info": 3}
OFFER_ACTIONS: tuple[str, ...] = ("start", "decline", "later", "send_to")
MAX_FACTS = 6


@dataclass(frozen=True)
class Fact:
    label: str
    value: str
    #: good, warn or bad when the fact itself leans the decision; '' when it only informs.
    tone: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "value": self.value, "tone": self.tone}


@dataclass(frozen=True)
class Decision:
    id: str
    #: offered (a quest on offer) or done (a quest to review).
    stage: str
    #: The proposal kind on offer, or the job's template when done.
    kind: str
    kind_label: str
    verdict: str
    tone: str
    title: str
    repo: str
    area: str | None
    at: datetime | None
    facts: tuple[Fact, ...]
    why: str
    yes_starts: str | None
    actions: tuple[str, ...]
    owners: tuple[str, ...] = ()
    #: Shown on the page but not asked in a chat (a low-readiness ticket, a weed, a crack below break).
    held: bool = False
    links: Mapping[str, str] = field(default_factory=dict)
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "stage": self.stage,
            "kind": self.kind,
            "kind_label": self.kind_label,
            "verdict": {"label": self.verdict, "tone": self.tone},
            "title": self.title,
            "repo": self.repo,
            "area": self.area,
            "at": self.at.isoformat() if isinstance(self.at, datetime) else None,
            "facts": [f.to_dict() for f in self.facts],
            "why": self.why,
            "yes_starts": self.yes_starts,
            "actions": list(self.actions),
            "owners": list(self.owners),
            "held": self.held,
            "assigned_to": None,
            "links": dict(self.links),
            "details": self.details,
        }


def _first_sentence(text: str | None, limit: int = 220) -> str:
    flat = " ".join(str(text or "").split())
    if not flat:
        return ""
    m = re.search(r"(?<=[.!?])\s", flat)
    sentence = flat[: m.start()] if m and m.start() <= limit else flat
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


def _facts(candidates: Iterable[Fact | None]) -> tuple[Fact, ...]:
    return tuple(f for f in candidates if f is not None and f.value)[:MAX_FACTS]


def _people(names: Sequence[str]) -> str:
    names = [n for n in names if n]
    if len(names) <= 2:
        return ", ".join(names)
    return f"{names[0]}, {names[1]} +{len(names) - 2}"


def _evidence(p: Mapping[str, Any]) -> str:
    if p.get("kind") == "finding" and p.get("path"):
        return f"{p['path']}:{p['line']}" if p.get("line") else str(p["path"])
    paths = [str(x) for x in p.get("paths") or [] if x]
    if not paths:
        return ""
    return paths[0] + (f" (+{len(paths) - 1})" if len(paths) > 1 else "")


def _seen(p: Mapping[str, Any]) -> str:
    occurrences = p.get("occurrences")
    if isinstance(occurrences, int) and occurrences > 1:
        return f"{occurrences}×"
    return ""


def offered(p: Mapping[str, Any], *, ask_min: int) -> Decision:
    """A quest on offer, from a proposal as the proposals API shows it (with its brief and held flag).

    ``ask_min`` is ``leads.ask_min`` for the proposal's repository: a backlog
    ticket's readiness below it is marked, as it is the ticket a chat does not ask.
    """
    kind = str(p.get("kind") or "")
    brief = p.get("brief") if isinstance(p.get("brief"), Mapping) else None
    severity = str(p.get("severity") or "").lower()
    known = KINDS.get(kind)
    kind_label = known.label if known else kind.title()
    if severity in _SEVERITY_TONE:
        verdict, tone = severity.title(), _SEVERITY_TONE[severity]
    else:
        verdict, tone = kind_label, "info"
    source = p.get("source") if isinstance(p.get("source"), Mapping) else {}
    readiness = source.get("readiness") if isinstance(source, Mapping) else None
    size = (brief or {}).get("size") or p.get("effort")
    created = p.get("created_at")
    return Decision(
        id=f"offer:{p['id']}",
        stage="offered",
        kind=kind,
        kind_label=kind_label,
        verdict=verdict,
        tone=tone,
        title=str((brief or {}).get("title") or p.get("title") or ""),
        repo=str(p.get("repo") or ""),
        area=p.get("area"),
        at=datetime.fromisoformat(created) if isinstance(created, str) else created,
        facts=_facts(
            [
                Fact("Evidence", _evidence(p)),
                Fact("Size", str(size or "")),
                Fact("Seen", _seen(p)),
                Fact("Readiness", f"{readiness}/100", "warn" if readiness < ask_min else "")
                if isinstance(readiness, int | float)
                else None,
                Fact("Owners", _people(list(p.get("owners") or []))),
            ]
        ),
        why=_first_sentence((brief or {}).get("problem") or p.get("summary")),
        yes_starts=_first_sentence((brief or {}).get("fix")) or (known and known.starts) or str(p.get("action") or ""),
        actions=OFFER_ACTIONS,
        owners=tuple(str(o) for o in p.get("owners") or []),
        held=bool(p.get("held")),
        links={k: str(v) for k, v in (("proposal", p.get("id")), ("url", p.get("url"))) if v},
        details=str(p.get("details") or ""),
    )


# ---- a quest done ----


def done_actions(outcome: str, pr_url: str | None) -> tuple[str, ...]:
    """The answers a finished quest offers, the likely one first (the dashboard's review bar, as one rule)."""
    if pr_url and outcome not in ("merged", "closed"):
        acts: tuple[str, ...] = ("merge", "send_back", "reject", "run_again")
    elif outcome in ("failed", "recalled"):
        acts = ("run_again", "dismiss")
    elif outcome == "done":
        acts = ("good", "run_again", "bad")
    else:
        acts = ("good", "bad", "dismiss")
    return (*acts, "send_to")


def _done_when(done_when: Any) -> Fact | None:
    report = done_when.get("report") if isinstance(done_when, Mapping) else None
    items = report.get("items") if isinstance(report, Mapping) else None
    if not isinstance(items, list) or not items:
        return None
    met = sum(1 for i in items if isinstance(i, Mapping) and i.get("met") is True)
    return Fact("Done-when", f"{met}/{len(items)} met", "good" if met == len(items) else "warn")


def _checks(ask: Mapping[str, Any], verification: Mapping[str, Any] | None) -> Fact | None:
    state = str(ask.get("checks_state") or "").lower()
    if state in ("success", "passed", "pass"):
        return Fact("Checks", "passing", "good")
    if state in ("failure", "failed", "error"):
        return Fact("Checks", "failing", "bad")
    if state == "pending":
        return Fact("Checks", "running", "warn")
    verdict = str((verification or {}).get("verdict") or "").lower()
    if verdict in ("pass", "passed", "approved", "verified"):
        return Fact("Checks", "verified", "good")
    if verdict in ("fail", "failed", "rejected"):
        return Fact("Checks", "verification failed", "bad")
    return None


def _pr(pr: Mapping[str, Any] | None, pr_url: str | None) -> Fact | None:
    if not pr_url:
        return None
    number = (pr or {}).get("number") or (re.search(r"/pull/(\d+)", pr_url) or [None, ""])[1]
    parts = [f"#{number}" if number else "open"]
    if pr and pr.get("additions") is not None:
        parts.append(f"+{pr['additions']} −{pr.get('deletions') or 0}")
    if pr and pr.get("files") is not None:
        parts.append(f"{pr['files']} file" + ("" if pr["files"] == 1 else "s"))
    return Fact("Pull request", " · ".join(parts))


def _spent(cost: Any, seconds: Any) -> str:
    parts = []
    if isinstance(cost, int | float) and cost > 0:
        parts.append(f"${cost:,.2f}")
    if isinstance(seconds, int | float) and seconds > 0:
        minutes = round(seconds / 60)
        parts.append(f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{max(minutes, 1)}m")
    return " · ".join(parts)


def _sensitive(recommendation: Mapping[str, Any]) -> Fact | None:
    reasons = [str(r) for r in recommendation.get("reasons") or [] if r]
    flagged = [r for r in reasons if re.search(r"sensitive|denylist|never merge|protected", r, re.IGNORECASE)]
    if not flagged:
        return None
    return Fact("Careful", _first_sentence(flagged[0], 90), "warn")


def done_verdict(
    outcome: str, *, recommendation: Mapping[str, Any], checks: Fact | None, done_when: Fact | None, failure: Any
) -> tuple[str, str]:
    if outcome == "failed":
        return "Failed", "bad"
    if outcome == "recalled":
        return "Recalled", "info"
    if outcome == "merged":
        return "Merged", "good"
    if outcome == "closed":
        return "Closed", "info"
    if outcome == "out":
        return "Underway", "info"
    if outcome == "pr_open":
        merge = str(recommendation.get("verdict") or "").lower() in ("merge", "approve", "ready")
        clean = (checks is None or checks.tone == "good") and (done_when is None or done_when.tone == "good")
        if merge and clean and checks is not None:
            return "Ready to merge", "good"
        if checks is not None and checks.tone == "bad":
            return "Checks failing", "bad"
        return "Needs a look", "warn"
    return ("Report ready", "info") if not failure else ("Needs a look", "warn")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


#: A finished job's card label: the game's word while the game layer is on, a plain one while
#: the settings document turns it off (``ui.game``, #177).
DONE_LABEL = "Quest done"
DONE_LABEL_PLAIN = "Job done"


def done(q: Mapping[str, Any], *, game: bool = True) -> Decision:
    """A quest to review, from a finished job's row and what is known about it.

    ``game`` is whether the game layer is on (``ui.game``): its card is
    labelled ``Quest done`` while it is, ``Job done`` while it is not.

    ``q`` carries the job's ``job_id``, ``repo``, ``area``, ``title``, ``outcome``,
    ``pr_url``, ``template``, ``finished_at``, ``cost_usd``, ``seconds``,
    ``failure``, ``work_nature``, ``task``, and when known ``pr`` (the pull
    request's numbers), ``verification``, ``approval_ask`` (the merge
    approval's checks and recommendation), ``done_when`` and ``brief`` (the
    job brief: title, what, why, done, size).
    """
    outcome = str(q.get("outcome") or "")
    brief = _mapping(q.get("brief"))
    ask = _mapping(q.get("approval_ask"))
    recommendation = _mapping(ask.get("recommendation"))
    checks = _checks(ask, _mapping(q.get("verification")) or None)
    done_when = _done_when(q.get("done_when"))
    verdict, tone = done_verdict(
        outcome, recommendation=recommendation, checks=checks, done_when=done_when, failure=q.get("failure")
    )
    nature = _mapping(q.get("work_nature"))
    why = (
        _first_sentence(q.get("failure"))
        if outcome == "failed" and q.get("failure")
        else _first_sentence(brief.get("why") or brief.get("what") or q.get("task"))
    )
    template = str(q.get("template") or "")
    return Decision(
        id=f"done:{q['job_id']}",
        stage="done",
        kind=template or "job",
        kind_label=DONE_LABEL if game else DONE_LABEL_PLAIN,
        verdict=verdict,
        tone=tone,
        title=str(brief.get("title") or q.get("title") or ""),
        repo=str(q.get("repo") or ""),
        area=q.get("area"),
        at=q.get("finished_at") if isinstance(q.get("finished_at"), datetime) else None,
        facts=_facts(
            [
                _pr(q.get("pr") if isinstance(q.get("pr"), Mapping) else None, q.get("pr_url")),
                checks,
                done_when,
                _sensitive(recommendation),
                Fact("Spent", _spent(q.get("cost_usd"), q.get("seconds"))),
                Fact("Nature", "slop cleanup") if str(nature.get("nature") or "") == "slop" else None,
            ]
        ),
        why=why,
        yes_starts=None,
        actions=done_actions(outcome, q.get("pr_url")),
        owners=tuple(str(o) for o in q.get("owners") or ()),
        links={
            k: str(v)
            for k, v in (("job", q.get("job_id")), ("pr", q.get("pr_url")), ("merge_approval", q.get("approval_id")))
            if v
        },
        details=str(q.get("report") or ""),
    )


def ordered(decisions: Iterable[Decision]) -> list[Decision]:
    """Worst first (bad, then warn, good, info), then the newest."""
    newest_first = sorted(decisions, key=lambda d: d.at.timestamp() if isinstance(d.at, datetime) else 0, reverse=True)
    return sorted(newest_first, key=lambda d: _TONE_RANK.get(d.tone, 3))
