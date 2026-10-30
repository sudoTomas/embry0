"""Done when: the finish line a coding job works to (spec 2026-10-26, done when).

docs/superpowers/specs/2026-10-26-done-when-design.md. Triage drafts, for a
code job, 2-5 checkable conditions with how each is shown, plus what is out of
scope (``done_when_guide``); a ticket's own acceptance criteria are the base,
and the fallback when triage gives none. A person can replace the list on the
job page. The developer (or the Slopmaster) gets it as its contract, with
pacing, and ends its final JSON with a report on each item; embry0 reads that
report strictly (``check_report``). Review and QA judge against the same list.

Which ticket headings hold a ticket's criteria, and how many times an unmet
report sends the work back, are the settings document's ``done_when`` section
(``embry0.settings.sections.done_when``), as the job read it when it started
(``embry0.services.job_settings``). The functions here take them as
arguments, so none of them can fall back to a value of its own.

The list is one JSON object, on ``jobs.done_when`` (migration 120) and on
``state["done_when"]``::

    {"items": [{"id": "d1", "done": "...", "shown_by": "..."}],
     "out_of_scope": ["..."], "source": "triage"|"ticket"|"person",
     "edited_by": None, "edited_at": None, "report": None}
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

#: The most items a list keeps; triage is asked for 2-5.
MAX_ITEMS = 6
#: The most out-of-scope lines a list keeps.
MAX_OUT_OF_SCOPE = 6
#: Bounds on each written field, so a list stays a list.
ITEM_CHARS = 300
EVIDENCE_CHARS = 500
#: The key of the developer's report in its final JSON.
REPORT_KEY = "done_when"
SOURCES = ("triage", "ticket", "person")

_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)]|\[[ xX]\])\s+(?:\[[ xX]\]\s+)?(?P<text>.+?)\s*$")
_SHOWN_SPLIT = re.compile(r"\s+(?:—|–|--|\|)\s+")

_GUIDE_HEAD = """\
## Done when

For every code job, set `done_when`: the finish line the developer works to,
and the list review and QA judge the change against. Without it a developer
does not know when to stop.
  "done_when": {
    "items": [{"done": "<a condition>", "shown_by": "<how it is shown>"}],
    "out_of_scope": ["<what this job does not take on>"]
  }
Rules:
- 2 to 5 items. Each is a condition a reviewer can check from the branch or
  the pull request, with how it is shown: a named test passes, a command
  prints something, a log line appears, a document says something, a written
  diagnosis names the cause with its evidence.
"""
_GUIDE_TAIL = """\
  keep their meaning, add `shown_by`, add none of your own beyond what the
  task asks.
- A diagnose or investigate task ends in a deliverable that can be reached,
  e.g. {"done": "the cause is written down with the log and code evidence
  that shows it, or 'cannot tell' with what is missing", "shown_by": "a
  section in the pull request description"}, plus the fix when the task asks
  for one. Never an open-ended "investigate X".
- out_of_scope names the nearby work this job must not take on (other
  services, refactors, "while I'm here" changes), 0 to 4 lines.
- Plain words, no ids. Omit `done_when` for jobs that are not code."""


def _headings_phrase(ticket_sections: Sequence[str]) -> str:
    """``"A"``, ``"A" or "B"``, ``"A", "B" or "C"``: the headings as triage is told them."""
    quoted = [f'"{heading}"' for heading in ticket_sections]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + f" or {quoted[-1]}"


def done_when_guide(ticket_sections: Sequence[str]) -> str:
    """The triage prompt's "Done when" section, naming the ticket headings that hold a ticket's criteria."""
    article = "an" if ticket_sections[0][:1].lower() in "aeiou" else "a"
    line = f"- When the task has {article} {_headings_phrase(ticket_sections)} section, those are your items:\n"
    return _GUIDE_HEAD + line + _GUIDE_TAIL


def _text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _items(raw: Iterable[Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for entry in raw:
        if isinstance(entry, Mapping):
            done = _text(entry.get("done") or entry.get("condition") or entry.get("text"), ITEM_CHARS)
            shown = _text(entry.get("shown_by") or entry.get("shown") or entry.get("how"), ITEM_CHARS)
        elif isinstance(entry, str):
            done, _, shown = (part.strip() for part in _split_shown(entry))
            done, shown = _text(done, ITEM_CHARS), _text(shown, ITEM_CHARS)
        else:
            continue
        if done:
            items.append({"id": f"d{len(items) + 1}", "done": done, "shown_by": shown})
        if len(items) == MAX_ITEMS:
            break
    return items


def _split_shown(line: str) -> tuple[str, str, str]:
    """``condition — how it is shown`` as (condition, separator, shown); no separator, no shown."""
    match = _SHOWN_SPLIT.search(line)
    if match is None:
        return line, "", ""
    return line[: match.start()], match.group(0), line[match.end() :]


def _scope(raw: Any) -> list[str]:
    if not isinstance(raw, list | tuple):
        return []
    lines = [_text(line, ITEM_CHARS) for line in raw if isinstance(line, str)]
    return [line for line in lines if line][:MAX_OUT_OF_SCOPE]


def parse_done_when(raw: Any, *, source: str = "triage") -> dict[str, Any] | None:
    """A done-when list from what triage (or a person) gave, or None when it holds no item.

    Lenient on shape: an item may be an object (``done``/``shown_by``) or a
    line ``condition — how it is shown``; a bare list is the items. Ids are
    renumbered ``d1``..``dn`` so a report can name them.
    """
    if isinstance(raw, list | tuple):
        raw = {"items": raw}
    if not isinstance(raw, Mapping):
        return None
    entries = raw.get("items")
    items = _items(entries if isinstance(entries, list | tuple) else ())
    if not items:
        return None
    return {
        "items": items,
        "out_of_scope": _scope(raw.get("out_of_scope")),
        "source": source if source in SOURCES else "triage",
        "edited_by": None,
        "edited_at": None,
        "report": None,
    }


def _has_bullet(section: str) -> bool:
    return any(_BULLET.match(line) for line in section.splitlines())


def ticket_criteria(task: str, headings: Sequence[str]) -> str:
    """The ticket's own criteria, as written: the one rule the ticket's list and review read them by.

    ``headings`` are the job's ``done_when.ticket_sections``. The first
    section, in the order of ``headings`` and then of the ticket, that lists
    a bullet; else the first of them with any text (criteria written as
    prose still reach review); else ``""``.
    """
    from embry0.workflows.issue_to_pr.evidence import sections_under

    filled = ""
    for heading in headings:
        for section in sections_under(task or "", heading):
            if _has_bullet(section):
                return section
            filled = filled or section
    return filled


def from_ticket(task: str, *, ticket_sections: Sequence[str]) -> dict[str, Any] | None:
    """The bullets of the ticket's criteria (``ticket_criteria``) as a list (``source: ticket``), or None."""
    section = ticket_criteria(task, ticket_sections)
    lines = [m.group("text") for line in section.splitlines() if (m := _BULLET.match(line))]
    return parse_done_when({"items": lines}, source="ticket") if lines else None


def resolve(triage_raw: Any, task: str, *, ticket_sections: Sequence[str]) -> dict[str, Any] | None:
    """Triage's list when it gave a usable one, else the ticket's own criteria (``from_ticket``), else None."""
    return parse_done_when(triage_raw, source="triage") or from_ticket(task, ticket_sections=ticket_sections)


def edited(raw: Any, *, by: str, now: datetime | None = None) -> dict[str, Any] | None:
    """A person's list: their items, no report (the old one named other ids)."""
    parsed = parse_done_when(raw, source="person")
    if parsed is None:
        return None
    return {**parsed, "edited_by": by, "edited_at": (now or datetime.now(UTC)).isoformat()}


def as_list(value: Any) -> dict[str, Any] | None:
    """A ``jobs.done_when`` value as a dict: the pool may hand JSONB back as text."""
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except ValueError:
            return None
    return dict(value) if isinstance(value, Mapping) and items_of(value) else None


async def current(jobs_repo: Any, job_id: str, fallback: Any) -> dict[str, Any] | None:
    """The job's list as it stands on its row (a person may have edited it), else ``fallback``.

    Read at the start of every coding run, so the next run works to an edit.
    An unreadable row keeps ``fallback``: the list never blocks a run.
    """
    if jobs_repo is not None and job_id:
        try:
            row = await jobs_repo.get(job_id)
        except Exception:  # noqa: BLE001 - the state's copy serves
            row = None
        stored = as_list(row.get("done_when")) if isinstance(row, Mapping) else None
        if stored is not None:
            return stored
    return as_list(fallback)


async def person_list(jobs_repo: Any, job_id: str, fallback: Any) -> dict[str, Any] | None:
    """The list a person set on the job, or None when the list (if any) is not theirs."""
    found = await current(jobs_repo, job_id, fallback)
    return found if found is not None and found.get("source") == "person" else None


def items_of(done_when: Any) -> list[dict[str, Any]]:
    """The list's items, or [] for a job without one."""
    if not isinstance(done_when, Mapping):
        return []
    raw = done_when.get("items")
    if not isinstance(raw, list):
        return []
    return [dict(i) for i in raw if isinstance(i, Mapping) and i.get("id") and i.get("done")]


def _item_line(item: Mapping[str, Any]) -> str:
    shown = f" (shown by: {item['shown_by']})" if item.get("shown_by") else ""
    return f"- [{item['id']}] {item['done']}{shown}"


def sent_back(retries: int) -> str:
    """What an unmet report does, for ``retries`` retries: "sends the work back once, then pauses the job"."""
    if retries <= 0:
        return "pauses the job"
    times = "once" if retries == 1 else f"up to {retries} times"
    return f"sends the work back {times}, then pauses the job"


def after_retries(retries: int) -> str:
    """How a pause says the retries are spent: " after a retry", " after 2 retries", or nothing for none."""
    if retries <= 0:
        return ""
    return " after a retry" if retries == 1 else f" after {retries} retries"


def contract(done_when: Any, *, retries: int, followup: str | None = None) -> str:
    """The developer's prompt section: the list as its contract, how it finishes, and the report it ends with.

    ``retries`` is the job's ``done_when.retries``: how many times an unmet
    report sends the work back before the job pauses.
    """
    items = items_of(done_when)
    if not items:
        return ""
    lines = ["## Done when (your finish line)", "This job is done when every item below holds:"]
    lines += [_item_line(i) for i in items]
    scope = (done_when or {}).get("out_of_scope") or []
    if scope:
        lines.append("Out of scope (do not take these on):")
        lines += [f"- {s}" for s in scope]
    lines += [
        "Work to this list, not beyond it. Pace yourself against your turn budget: by half of it commit "
        "what you have; by three quarters stop exploring and finish against the list; as soon as every "
        "item holds, commit, push and stop.",
        f'End your final JSON with "{REPORT_KEY}": one entry per item, '
        '[{"id": "d1", "met": true, "evidence": "<the test, command output, file or section that shows it>"}]. '
        "Say met: false, with what is missing, for any item you did not reach. embry0 checks this report: "
        f"an unmet or unreported item {sent_back(retries)}.",
    ]
    if followup:
        lines += ["", followup]
    return "\n".join(lines)


def pacing_line(max_turns: int) -> str:
    """The turn-budget note's pacing, in turns, for a coding run working to a done-when list."""
    half, three_quarters = max(1, max_turns // 2), max(1, (max_turns * 3) // 4)
    return (
        f"Pacing (done when): by turn {half} commit what you have; by turn {three_quarters} stop exploring and "
        "finish against the done-when list; once every item holds, commit, push and stop."
    )


def check_report(done_when: Any, output: str, *, now: datetime | None = None) -> dict[str, Any]:
    """The developer's report on ``done_when``, read strictly from its final JSON.

    An item counts as met only when the report names its id with ``met`` true
    and evidence; anything else, unreported included, is unmet. No report at
    all leaves every item unmet (``reported`` false).
    """
    from embry0.orchestration.agent_json import last_object_with

    items = items_of(done_when)
    data = last_object_with(output or "", REPORT_KEY)
    raw = (data or {}).get(REPORT_KEY)
    entries = raw if isinstance(raw, list) else []
    by_id: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if isinstance(entry, Mapping) and isinstance(entry.get("id"), str) and entry["id"] not in by_id:
            by_id[entry["id"]] = entry
    checked: list[dict[str, Any]] = []
    for item in items:
        entry = by_id.get(str(item["id"]))
        evidence = _text((entry or {}).get("evidence"), EVIDENCE_CHARS)
        met = entry is not None and entry.get("met") is True and bool(evidence)
        if entry is None:
            evidence = "not reported"
        elif entry.get("met") is True and not evidence:
            evidence = "reported met with no evidence"
        checked.append({"id": item["id"], "met": met, "evidence": evidence})
    return {
        "items": checked,
        "unmet": [c["id"] for c in checked if not c["met"]],
        "reported": isinstance(raw, list),
        "checked_at": (now or datetime.now(UTC)).isoformat(),
    }


def unmet_items(done_when: Any, report: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The items ``report`` left unmet, with what the report said about each."""
    if not report:
        return []
    said = {str(c.get("id")): str(c.get("evidence") or "") for c in report.get("items") or [] if isinstance(c, Mapping)}
    unmet = set(report.get("unmet") or [])
    return [{**i, "evidence": said.get(str(i["id"]), "")} for i in items_of(done_when) if i["id"] in unmet]


def followup_text(done_when: Any, report: Mapping[str, Any]) -> str:
    """A retry's instructions: the items still open, and the report to send again."""
    open_items = unmet_items(done_when, report)
    lines = [
        "Done-when check: your last run pushed, but these items are not met"
        + ("" if report.get("reported") else " (your final JSON had no done_when report)")
        + ". Finish them, then commit, push and report every item again:"
    ]
    lines += [f"{_item_line(i)} — last report: {i['evidence'] or 'nothing'}" for i in open_items]
    return "\n".join(lines)


def unmet_summary(done_when: Any, report: Mapping[str, Any] | None) -> str:
    """The unmet items in one line, for the pause and the status words."""
    return "; ".join(f"{i['done']}" for i in unmet_items(done_when, report))


def review_block(done_when: Any) -> str:
    """The review prompt's finish line: the list and the developer's report on it."""
    items = items_of(done_when)
    if not items:
        return ""
    lines = ["Done when (the job's finish line; judge the change against each item):"]
    lines += [_item_line(i) for i in items]
    scope = (done_when or {}).get("out_of_scope") or []
    if scope:
        lines.append("Out of scope: " + "; ".join(scope))
    report = (done_when or {}).get("report")
    if isinstance(report, Mapping) and report.get("items"):
        lines.append("The developer's report (a claim to verify, not proof):")
        lines += [
            f"- [{c.get('id')}] {'met' if c.get('met') else 'NOT met'}: {c.get('evidence') or ''}"
            for c in report["items"]
            if isinstance(c, Mapping)
        ]
    lines.append(
        "An item the diff does not meet is a reason for changes_requested; work outside the list "
        "(out of scope, or beyond what an item asks) is a finding too."
    )
    return "\n".join(lines)


def qa_criteria(done_when: Any) -> list[str]:
    """The list as QA acceptance criteria: each condition with how it is shown."""
    return [f"{i['done']} (shown by: {i['shown_by']})" if i.get("shown_by") else i["done"] for i in items_of(done_when)]
