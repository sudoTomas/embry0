"""Asks (spec 2026-10-27): the feed a chat assistant sends from, and the answers it passes back.

- ``GET /asks/due``: the asks to send now, each with its decision rendered,
  its code and the actions a chat may offer; a reminder is marked as one.
  Empty while ``asks.enabled`` is false in the settings document, which is
  read on every request, each ask by its decision's organization's values.
- ``POST /asks/{id}/sent``: the assistant sent it (``message_ref`` names the message).
- ``POST /asks/answer``: a button was pressed: the code, the action and the
  person who pressed it. The answer goes through the paths the dashboard
  uses; a second press returns the first answer.
- ``GET /asks``: the latest asks, for the dashboard.
- ``GET /asks/of``: everything waiting on one person or group, so a chat can
  say how many there are and talk them through.
- ``GET /asks/decision/{code}``: one decision by its code, as much as there is to say about it.
- ``POST /asks/jobs/{job_id}/answer`` and ``/resume``: a job's question or pause, passed on for whoever started it.

A ``propose`` key does all of these: no model sits between a press and its answer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from embry0.api.issue_allowlist import hidden_issue_ids, hide_input_unless_covered
from embry0.api.repo_allowlist import covered_slugs, hide_unless_covered, key_covers, restricted_key_name
from embry0.api.schemas.person_name import PersonName
from embry0.api.session import TIER_ADMIN, TIER_OPERATOR
from embry0.services.asks import AskPolicy, ask_settings, asked_order, release, working
from embry0.settings.sections.asks import ASKS, AsksSettings
from embry0.settings.store import Scoped, SettingsStore
from embry0.storage.repositories.jobs import job_repository

logger = structlog.get_logger(__name__)
router = APIRouter()

#: What a chat may answer by itself; everything else opens the dashboard.
CHAT_ACTIONS = {"offered": ("start", "decline", "later", "send_to"), "done": ("merge", "send_to")}
#: How long a chat's "Later" waits, as the dashboard's does.
LATER_DAYS = 7


def _repo(request: Request, name: str) -> Any:
    repo = getattr(request.app.state, name, None)
    if repo is None:
        raise HTTPException(status_code=503, detail=f"{name} is not initialized")
    return repo


async def ask_policy(request: Request) -> Scoped[AsksSettings]:
    """The ``asks`` section of the settings document as it stands now, read per repository."""
    store: SettingsStore | None = getattr(request.app.state, "settings", None)
    if store is None:
        raise HTTPException(status_code=503, detail="settings storage not initialized")
    return (await store.snapshot()).scoped(ASKS)


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def chat_actions(decision: dict[str, Any]) -> list[str]:
    """The decision's actions a chat button may take; a merge only through a pending merge approval."""
    allowed = CHAT_ACTIONS.get(str(decision.get("stage")), ())
    acts = [a for a in decision.get("actions") or [] if a in allowed]
    if "merge" in acts and not (decision.get("links") or {}).get("merge_approval"):
        acts.remove("merge")
    return acts


def dashboard_link(request: Request, decision: dict[str, Any]) -> str:
    base = str(getattr(request.app.state.config, "dashboard_public_url", "") or "").rstrip("/")
    stage = "offered" if decision.get("stage") == "offered" else "done"
    return f"{base}/decisions?stage={stage}" if base else ""


async def _dashboard_link_for(request: Request, who: str, decision: dict[str, Any]) -> str:
    """``dashboard_link`` for whoever can use it. The dashboard refuses a person with no role there, so they get none;
    a group's chat has people who can."""
    if not who.startswith("group:"):
        users = getattr(request.app.state, "users_repo", None)
        found = await users.get_by_email(who) if users is not None else None
        if found is None or found.tier not in (TIER_OPERATOR, TIER_ADMIN):
            return ""
    return dashboard_link(request, decision)


async def _person(request: Request, who: str) -> dict[str, Any]:
    """Whom an ask is for. ``key`` is its ``recipient`` as stored, which ``GET /asks/of`` reads them by."""
    users = getattr(request.app.state, "users_repo", None)
    key = who.lower()
    if who.startswith("group:"):
        name = who.removeprefix("group:")
        return {"key": key, "group": name, "github_login": None, "email": None, "name": name}
    if who.startswith("github:"):
        login = who.removeprefix("github:")
        return {"key": key, "github_login": login, "email": None, "name": login}
    found = await users.get_by_email(who) if users is not None else None
    return {
        "key": key,
        "email": who,
        "name": (found.name if found is not None else "") or who,
        "github_login": found.github_login if found is not None else None,
    }


async def open_decisions(app: Any) -> list[dict[str, Any]]:
    """Every open decision, worst first, whatever any caller's key may read: what the ask loop reads
    every tick and the feed on every request.

    A person's unanswered asks count toward their limit whether or not the
    caller's key covers their decisions, and each counts in its decision's
    organization, so the feed reads every decision, as the ask loop does, and
    hands out only those the key may read (#272). None is cut off by the
    list's page: a decision left out would read as answered, and the loop
    would withdraw its asks (#273). The decisions list builds every decision
    before it cuts any, so reading them all costs no more than reading the
    first page. That holds for the quests to review too, every one waiting,
    not the inbox's newest 30 (#296). They carry no report in ``details``:
    neither reader shows a card, and the feed reads the reports of the asks it
    hands out (:func:`~embry0.api.v1.decisions.with_reports`).
    """
    from embry0.api.v1.decisions import waiting
    from embry0.api.v1.organizations import app_request

    return await waiting(app_request(app), stage="all", owner=None, repo=None, include_held=True, report_chars=0)


@router.get("/asks/due")
async def asks_due(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    peek: bool = Query(
        default=False,
        description=(
            "Return the page without handing it out: nothing is leased, and its first sends are not stamped, "
            "so no expiry starts"
        ),
    ),
) -> dict[str, Any]:
    """The asks to send now: due reminders, then queued ones, worst first, while their person holds
    fewer than ``asks.max_open_per_person`` unanswered asks about that organization's decisions; a
    hand-off goes first and past that limit. Each
    ask is held to its decision's organization's values: its working hours for a reminder, its
    ``remind_hours`` and its limit. Only asks whose decision the caller's key may read are handed
    out, but every unanswered ask counts toward its person's limit. An empty list while
    ``asks.enabled`` is false in the settings document, whatever is stored; ``enabled`` says which.

    A call is the assistant's hand-out: each ask on the page is leased to it
    for ``asks.lease_minutes`` (#293). Until it is reported sent or the lease
    lapses, it counts toward its person's limit and goes to no other poll; once
    the lease lapses unreported it is handed out again, before any other ask of
    its person. Each first send is stamped ``handed_out_at`` the first time and
    expires ``asks.expire_days`` later unless it is reported sent (#295). On the
    page, queued asks never handed out come before those handed out again. The
    decision and the leases are taken under one lock, so two polls never hand
    out the same ask or one past its person's limit. ``?peek=1`` returns the
    same page and leases and stamps nothing, for a person or a probe that only
    looks."""
    from embry0.api.v1.decisions import listed_to, with_reports
    from embry0.api.v1.ui import ui_settings
    from embry0.services.decisions import labelled

    section = await ask_policy(request)
    if not section.deployment().enabled:
        return {"asks": [], "count": 0, "enabled": False}
    asks = _repo(request, "asks_repo")
    now = datetime.now(UTC)
    decisions = await open_decisions(request.app)
    policy = AskPolicy(section, decisions)
    by_id = {str(d["id"]): d for d in decisions}
    order = asked_order(decisions)

    def shown(ask: Mapping[str, Any]) -> bool:
        return listed_to(request, by_id[str(ask["decision_id"])])

    page: list[tuple[Mapping[str, Any], bool]] = []
    waiting: Counter[str] = Counter()

    def pick(rows: list[dict[str, Any]]) -> list[tuple[str, bool]]:
        # An ask whose decision is not open any more was answered or moved on: the ask loop withdraws it.
        # The rest count toward their person's limit, the ones in repositories the caller's key does not
        # cover too; only what the caller may read is handed out.
        live = [a for a in rows if str(a["decision_id"]) in by_id]
        waiting.clear()
        waiting.update(str(a["recipient"]).lower() for a in live if shown(a))
        page[:] = release(live, order, now, policy, shown)[:limit]
        return [(str(ask["id"]), reminder) for ask, reminder in page]

    if peek:
        pick(await asks.live())
    else:
        lease = timedelta(minutes=section.deployment().lease_minutes)
        leased = await asks.lease_out(pick, now=now, until=now + lease)
        # What changed since the decision, a report of its send or a close, is not handed out.
        page[:] = [(ask, reminder) for ask, reminder in page if str(ask["id"]) in leased]
    # The decisions were read without their reports; the page's cards carry theirs, as GET /decisions shows them.
    cards = {str(d["id"]): d for d in await with_reports(request, [by_id[str(a["decision_id"])] for a, _ in page])}
    known = await asks.ever_sent() if page else set()
    # The decisions were built for no caller; each card speaks the game's words as the caller reads the game (#177).
    game = (await ui_settings(request)).game if page else True
    out: list[dict[str, Any]] = []
    for ask, reminder in page:
        decision = labelled(cards[str(ask["decision_id"])], game=game)
        key = str(ask["recipient"]).lower()
        out.append(
            {
                "id": ask["id"],
                "code": await asks.code(str(ask["decision_id"])),
                "recipient": await _person(request, str(ask["recipient"])),
                "waiting": waiting[key],
                "at_once": policy.settings(str(ask["decision_id"])).max_open_per_person,
                "first": key not in known,
                "reminder": reminder,
                "decision": decision,
                "actions": chat_actions(decision),
                "dashboard_url": await _dashboard_link_for(request, str(ask["recipient"]), decision),
            }
        )
    hours = ask_settings(section.deployment())
    return {
        "asks": out,
        "count": len(out),
        "enabled": True,
        "day": now.astimezone(ZoneInfo(hours.timezone)).date().isoformat(),
        "working": working(now, hours),
    }


class SentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_ref: str = Field(default="", max_length=500)


async def decision_repos(request: Request, decision_ids: Sequence[str]) -> dict[str, str]:
    """Each decision's repository: read back from the row it comes from, else from the open decisions, which
    alone name an audit finding's. A decision in neither is left out, and a key with an allowlist reads it as
    one in a repository it does not cover."""
    found: dict[str, str] = await _repo(request, "asks_repo").decision_repos(decision_ids)
    if any(d not in found for d in decision_ids):
        found |= {str(d["id"]): str(d.get("repo") or "") for d in await open_decisions(request.app)}
    return found


def _not_waiting() -> HTTPException:
    return HTTPException(status_code=409, detail="that ask is not waiting to be sent")


@router.post("/asks/{ask_id}/sent")
async def ask_sent(ask_id: str, body: SentBody, request: Request) -> dict[str, Any]:
    """The assistant sent the ask (or its reminder). An ask about a decision in a repository the caller's key
    does not cover answers as one that is not waiting to be sent. A second report within ``asks.lease_minutes`` of
    the first, with no reminder handed out since, is the same message sent by a second poll: it does not count as
    the reminder."""
    asks = _repo(request, "asks_repo")
    if restricted_key_name(request) is not None:
        ask = await asks.get(ask_id)
        if ask is None:
            raise _not_waiting()
        repo = (await decision_repos(request, [str(ask["decision_id"])])).get(str(ask["decision_id"]))
        await hide_unless_covered(request, repo, _not_waiting(), action="report an ask sent")
    # A second report within the lease of the first is a second poll's copy, not the reminder (#293).
    lease = timedelta(minutes=(await ask_policy(request)).deployment().lease_minutes)
    row = await asks.sent(ask_id, message_ref=body.message_ref, now=datetime.now(UTC), duplicate_within=lease)
    if row is None:
        raise _not_waiting()
    return {k: _iso(v) for k, v in row.items()}


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=4, max_length=16)
    action: Literal["start", "decline", "later", "send_to", "merge"]
    #: The email of the person who pressed the button (the assistant checked who they are).
    #: Never an ``embry0:`` actor's name.
    person: PersonName = Field(min_length=3, max_length=320)
    reason: str = Field(default="", max_length=2000)
    #: For send_to: the email of the teammate, and a note.
    to: str = Field(default="", max_length=320)
    note: str = Field(default="", max_length=2000)


async def _may_decide(request: Request, decision: dict[str, Any], person: str) -> bool:
    """The hand-off's person while one is open; else an owner (by GitHub login) or an admin. A decision nobody owns
    that goes to a group (``asks.unowned_group``) is anyone's embry0 knows."""
    users = getattr(request.app.state, "users_repo", None)
    me = await users.get_by_email(person) if users is not None else None
    held = (decision.get("assigned_to") or {}).get("email")
    if held:
        return held == person.lower() or (me is not None and me.tier == "admin")
    if me is None:
        return False
    owners = {str(o).lstrip("@").lower() for o in decision.get("owners") or []}
    if not owners and (await ask_policy(request)).deployment().unowned_group:
        return True
    return me.tier == "admin" or (bool(me.github_login) and me.github_login.lower() in owners)


async def _do(request: Request, decision: dict[str, Any], body: AnswerBody) -> dict[str, Any]:
    """Take the action through the path the dashboard uses, as ``body.person``."""
    from embry0.api.v1 import proposals

    links = decision.get("links") or {}
    if body.action == "send_to":
        from embry0.api.v1.handoffs import hand_off

        return await hand_off(request, str(decision["id"]), to=body.to, note=body.note, actor=body.person)
    if body.action == "merge":
        from embry0.services.handoffs import settle
        from embry0.services.merge_approvals import DecisionRefused

        service = _repo(request, "merge_approvals")
        try:
            # The gate above stood in for the dashboard's admin gate: an owner, the hand-off's person, or an admin.
            result = await service.decide(
                str(links["merge_approval"]),
                decision="approve",
                decided_by=body.person,
                chat_id="asks",
                via="dashboard",
            )
        except DecisionRefused as refused:
            raise HTTPException(status_code=refused.status_code, detail=refused.detail) from None
        await settle(request.app.state, str(decision["id"]), "merge", body.person)
        return dict(result)
    proposal_id = str(links.get("proposal") or "")
    if body.action == "start":
        return await proposals.accept_proposal(proposal_id, proposals.AcceptBody(by=body.person), request)
    if body.action == "decline":
        return await proposals.decline_proposal(
            proposal_id, proposals.AnswerBody(by=body.person, reason=body.reason), request
        )
    until = datetime.now(UTC) + timedelta(days=LATER_DAYS)
    return await proposals.snooze_proposal(proposal_id, proposals.SnoozeBody(by=body.person, until=until), request)


@router.post("/asks/answer")
async def answer_ask(body: AnswerBody, request: Request) -> dict[str, Any]:
    """A button pressed in a chat: answered once, through the dashboard's paths; a second press returns the first."""
    from embry0.api.v1.decisions import get_decision

    asks = _repo(request, "asks_repo")
    decision_id = await asks.decision_of(body.code.strip().upper())
    if decision_id is None:
        raise HTTPException(status_code=404, detail="no decision has that code")
    if restricted_key_name(request) is not None:
        # A decision in a repository the key does not cover, open or answered, answers as a code no decision has.
        await hide_unless_covered(
            request,
            (await decision_repos(request, [decision_id])).get(decision_id),
            HTTPException(status_code=404, detail="no decision has that code"),
            action="answer an ask",
        )
    last = await asks.last_answer(decision_id)
    try:
        decision = await get_decision(decision_id, request)
    except HTTPException:
        if last is not None:
            return {"already": True, "answer": last.get("answer"), "by": last.get("answered_by"), "asks": []}
        raise HTTPException(status_code=409, detail="that quest was answered or moved on elsewhere") from None
    if body.action not in chat_actions(decision):
        raise HTTPException(status_code=409, detail=f"{body.action} is not open on that quest from a chat")
    if not await _may_decide(request, decision, body.person):
        raise HTTPException(
            status_code=403, detail="it is not theirs to decide: an owner, the person it was sent to, or an admin"
        )
    result = await _do(request, decision, body)
    closed = await asks.answered(decision_id, answer=body.action, by=body.person.lower(), now=datetime.now(UTC))
    shown = [{"id": a["id"], "recipient": a["recipient"], "message_ref": a.get("message_ref")} for a in closed]
    left = Counter(str(a["recipient"]).lower() for a in await asks.live())
    waiting = {who: left[who] for who in {str(a["recipient"]).lower() for a in closed}}
    notifier = getattr(request.app.state, "webhook_notifier", None)
    if notifier is not None:
        notifier.emit_soon(
            "ask.answered",
            {
                "decision_id": decision_id,
                "code": body.code.upper(),
                "answer": body.action,
                "by": body.person,
                "asks": shown,
            },
        )
    logger.info("ask_answered", decision_id=decision_id, action=body.action, by=body.person)
    return {
        "already": False,
        "answer": body.action,
        "by": body.person,
        "result": result,
        "asks": shown,
        "waiting": waiting,
    }


@router.get("/asks/recap")
async def asks_recap(
    request: Request,
    since: datetime = Query(description="When the last recap went out"),
) -> dict[str, Any]:
    """Each person's and group's numbers for the morning, for a chat to send once a day: how many wait on them
    (``waiting``), the decisions they answered since ``since`` (``worked``; a group's, whoever there answered) and the
    asks that reached them since (``new``). Someone with nothing to say is left out. ``GET /asks/due`` says which day
    it is and whether it is working hours. Through a key with a repository allowlist, only decisions in the
    repositories it covers."""
    from embry0.api.v1.decisions import listed_to

    asks = _repo(request, "asks_repo")
    since = since if since.tzinfo else since.replace(tzinfo=UTC)
    by_id = {str(d["id"]): d for d in await open_decisions(request.app)}
    moved = await asks.moved_since(since)
    if restricted_key_name(request) is not None:
        repos = await asks.decision_repos(str(m["decision_id"]) for m in moved if str(m["decision_id"]) not in by_id)
        moved = [
            m
            for m in moved
            if (
                listed_to(request, by_id[d])
                if (d := str(m["decision_id"])) in by_id
                else key_covers(request, repos.get(d))
            )
        ]
    waiting = Counter(
        str(a["recipient"]).lower()
        for a in await asks.live()
        if str(a["decision_id"]) in by_id and listed_to(request, by_id[str(a["decision_id"])])
    )
    worked: dict[str, set[str]] = {}
    new: Counter[str] = Counter()
    for m in moved:
        who = str(m["recipient"]).lower()
        if m["state"] == "answered" and m.get("answered_at") and m["answered_at"] >= since:
            by = who if who.startswith("group:") else str(m.get("answered_by") or who).lower()
            worked.setdefault(by, set()).add(str(m["decision_id"]))
        if m["created_at"] >= since and m["state"] != "withdrawn":
            new[who] += 1
    everyone = sorted(w for w in {*waiting, *worked, *new} if not w.startswith("github:"))
    return {
        "recaps": [
            {
                "recipient": await _person(request, who),
                "waiting": waiting[who],
                "worked": len(worked.get(who, ())),
                "new": new[who],
            }
            for who in everyone
        ]
    }


@router.get("/asks/of")
async def asks_of(
    request: Request,
    person: str = Query(min_length=3, max_length=320, description="An ask's recipient: an email, or group:<name>"),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Everything waiting on one person or group, what they were sent first, then worst first: what a chat reads to
    say how many there are and talk them through. ``waiting`` and ``repos`` count them all, ``asks`` lists ``limit``.
    Through a key with a repository allowlist, only those about decisions in the repositories it covers."""
    from embry0.api.v1.decisions import listed_to
    from embry0.api.v1.ui import ui_settings
    from embry0.services.decisions import labelled

    asks = _repo(request, "asks_repo")
    who = person.strip().lower()
    decisions = await open_decisions(request.app)
    by_id = {str(d["id"]): d for d in decisions}
    order = asked_order(decisions)
    mine = [
        a
        for a in await asks.live()
        if str(a["recipient"]).lower() == who
        and str(a["decision_id"]) in by_id
        and listed_to(request, by_id[str(a["decision_id"])])
    ]
    mine.sort(key=lambda a: (a.get("state") == "queued", order[str(a["decision_id"])]))
    repos = Counter(str(by_id[str(a["decision_id"])].get("repo") or "") for a in mine)
    out: list[dict[str, Any]] = []
    # The decisions were built for no caller; each speaks the game's words as the caller reads the game (#177).
    game = (await ui_settings(request)).game if mine else True
    for ask in mine[:limit]:
        d = labelled(by_id[str(ask["decision_id"])], game=game)
        out.append(
            {
                "code": await asks.code(str(ask["decision_id"])),
                "sent": ask.get("state") != "queued",
                "held": bool(d.get("held")),
                "repo": d.get("repo"),
                "area": d.get("area"),
                "kind": d.get("kind_label"),
                "verdict": (d.get("verdict") or {}).get("label"),
                "title": d.get("title"),
                "why": d.get("why"),
                "yes_starts": d.get("yes_starts"),
                "actions": chat_actions(d),
            }
        )
    return {
        "person": who,
        "waiting": len(mine),
        "repos": [{"repo": repo, "count": n} for repo, n in repos.most_common()],
        "asks": out,
        **await _their_waiting_jobs(request, who),
    }


async def _their_waiting_jobs(request: Request, who: str) -> dict[str, list[dict[str, Any]]]:
    """The jobs ``who`` started that wait on them: each pending blocking question, and each pause. A group starts none."""
    from embry0.api.v1.decisions import listed_to

    jobs = getattr(request.app.state, "jobs_repo", None)
    inputs = getattr(request.app.state, "inputs_repo", None)
    if jobs is None or who.startswith("group:"):
        return {"questions": [], "paused": []}
    questions: list[dict[str, Any]] = []
    paused: list[dict[str, Any]] = []
    started = [job for job in await jobs.started_and_waiting(who) if listed_to(request, job)]
    # A job the key reads names no issue it does not read (embry0.api.issue_allowlist): such an issue's title is not
    # the job's, and its questions about such an issue are left out.
    hidden = await hidden_issue_ids(request, (job.get("issue_id") for job in started))
    for job in started:
        title = job.get("task_title") if job.get("issue_id") in hidden else job.get("title")
        about = {"job_id": job["job_id"], "repo": job.get("repo"), "title": title}
        if job["status"] == "paused":
            paused.append(about)
        elif inputs is not None:
            questions += [
                {**about, "input_id": i["id"], "question": i["question"]}
                for i in await inputs.list_by_job(job["job_id"], repos=covered_slugs(request))
                if i.get("status") == "pending" and i.get("importance") == "blocking"
            ]
    return {"questions": questions, "paused": paused}


async def _their_job(request: Request, job_id: str, person: str, *, action: str) -> dict[str, Any]:
    """The job, when ``person`` may steer it: whoever started it, or an admin. A job no person started, whose question
    goes to the group chat (``asks.unowned_group``), is anyone's embry0 knows. One the caller's key does not cover
    answers as one that does not exist."""
    from embry0.services._executor_outcomes import person_who_started

    none = HTTPException(status_code=404, detail="no job has that id")
    job = await _repo(request, "jobs_repo").get(job_id)
    if job is None:
        raise none
    if restricted_key_name(request) is not None:
        # The job's repository: its repo, else its git context's (a job posted with a context alone stores none).
        await hide_unless_covered(request, job_repository(job), none, action=action)
    users = getattr(request.app.state, "users_repo", None)
    me = await users.get_by_email(person) if users is not None else None
    started_by = person_who_started(job)
    if me is None or not (
        me.tier == "admin"
        or (started_by or "").lower() == person.lower()
        or (started_by is None and (await ask_policy(request)).deployment().unowned_group)
    ):
        raise HTTPException(status_code=403, detail="it is not theirs to steer: whoever started the job, or an admin")
    return dict(job)


class JobAnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The email of the person answering (the assistant checked who they are).
    person: PersonName = Field(min_length=3, max_length=320)
    input_id: str = Field(min_length=1, max_length=64)
    answer: str = Field(min_length=1, max_length=50000)


@router.post("/asks/jobs/{job_id}/answer")
async def answer_job_question(job_id: str, body: JobAnswerBody, request: Request) -> dict[str, Any]:
    """A question on a job, answered in a chat for whoever started it, through the paths the dashboard uses."""
    from embry0.api.schemas.inputs import AnswerInputRequest
    from embry0.api.v1.issues import answer_issue_input_as
    from embry0.api.v1.jobs import AnswerJobInputRequest, answer_job_input_as

    await _their_job(request, job_id, body.person, action="answer a question on a job")
    inputs = _repo(request, "inputs_repo")
    question = await inputs.get(body.input_id)
    none = HTTPException(status_code=404, detail="that job has no such question")
    if question is None or question.get("job_id") != job_id:
        raise none
    # A question about an issue the key does not read answers as one the job does not have (embry0.api.issue_allowlist).
    await hide_input_unless_covered(request, question, none, action="answer a question on a job")
    if question.get("issue_id"):
        answered = await answer_issue_input_as(
            str(question["issue_id"]),
            body.input_id,
            AnswerInputRequest(answer=body.answer),
            request,
            _repo(request, "issues_repo"),
            inputs,
            _repo(request, "issue_executor"),
            body.person,
        )
        return answered.model_dump(mode="json")
    return await answer_job_input_as(
        job_id,
        body.input_id,
        AnswerJobInputRequest(answer=body.answer),
        request,
        _repo(request, "jobs_repo"),
        body.person,
    )


class JobResumeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The email of the person answering (the assistant checked who they are).
    person: PersonName = Field(min_length=3, max_length=320)
    #: Keep going, or stop. Taking the work as it is stays on the dashboard.
    choice: Literal["continue_retrying", "abandon"]
    guidance: str = Field(default="", max_length=50000)


@router.post("/asks/jobs/{job_id}/resume")
async def resume_their_job(job_id: str, body: JobResumeBody, request: Request) -> dict[str, Any]:
    """A paused job, kept going or stopped in a chat for whoever started it, through the path the dashboard uses."""
    from embry0.api.schemas.jobs import JobResumeRequest
    from embry0.api.v1.jobs import resume_job_as

    await _their_job(request, job_id, body.person, action="resume a job")
    return await resume_job_as(
        job_id,
        JobResumeRequest(choice=body.choice, guidance=body.guidance),
        request,
        _repo(request, "jobs_repo"),
        body.person,
    )


class JobStopBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The email of the person answering (the assistant checked who they are).
    person: PersonName = Field(min_length=3, max_length=320)


@router.post("/asks/jobs/{job_id}/stop")
async def stop_their_job(job_id: str, body: JobStopBody, request: Request) -> dict[str, Any]:
    """A job stopped in a chat for whoever started it, through the paths the dashboard uses: a paused one is abandoned,
    with its work kept on the branch, and one still going or waiting on an answer is cancelled."""
    from embry0.api.schemas.jobs import JobResumeRequest
    from embry0.api.v1.jobs import cancel_job_as, resume_job_as

    job = await _their_job(request, job_id, body.person, action="stop a job")
    jobs = _repo(request, "jobs_repo")
    if job.get("status") == "paused":
        return await resume_job_as(job_id, JobResumeRequest(choice="abandon"), request, jobs, body.person)
    return await cancel_job_as(job_id, request, jobs, body.person)


@router.get("/asks/decision/{code}")
async def decision_by_code(code: str, request: Request) -> dict[str, Any]:
    """One decision by its code, all there is to say about it, and what a chat may answer: a code no decision has,
    or one in a repository the caller's key does not cover, answers 404."""
    from embry0.api.v1.decisions import get_decision

    none = HTTPException(status_code=404, detail="no decision has that code")
    decision_id = await _repo(request, "asks_repo").decision_of(code.strip().upper())
    if decision_id is None:
        raise none
    if restricted_key_name(request) is not None:
        repo = (await decision_repos(request, [decision_id])).get(decision_id)
        await hide_unless_covered(request, repo, none, action="read a decision by its code")
    try:
        decision = await get_decision(decision_id, request)
    except HTTPException:
        raise HTTPException(status_code=409, detail="that quest was answered or moved on elsewhere") from None
    return {"code": code.strip().upper(), "decision": decision, "actions": chat_actions(decision)}


@router.get("/asks")
async def list_asks(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    """The latest asks. Through a key with a repository allowlist, only those about decisions in the repositories
    it covers. An ask names only its decision, whose repository is read back at the time from the row the decision
    comes from (a finished job, an issue, a watcher case, a lead, an answered proposal), else from the open
    decisions; an ask about an audit finding that is no longer offered and was never answered has no repository
    to read back, so such a key does not see it."""
    from embry0.api.v1.decisions import listed_to

    asks = _repo(request, "asks_repo")
    if restricted_key_name(request) is None:
        rows = await asks.recent(limit=limit)
    else:
        listed = {str(d["id"]) for d in await open_decisions(request.app) if listed_to(request, d)}
        rows = await asks.recent(limit=limit, repos=covered_slugs(request) or [], decision_ids=listed)
        rows = [r for r in rows if r["decision_id"] in listed or key_covers(request, r.get("decision_repo"))]
    for r in rows:
        r.pop("decision_repo", None)
    return {"asks": [{k: _iso(v) for k, v in r.items()} for r in rows], "count": len(rows)}
