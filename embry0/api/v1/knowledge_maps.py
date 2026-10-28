"""Knowledge maps API (spec 2026-10-27): upload a map, read it, mark its parts, send them to agents.

``PUT /maps/{slug}`` stores a scanner's ``embry0.map/1`` upload (gzip or
plain JSON). ``GET /maps/{slug}`` serves its parts and links as stored, with
an ETag. A part's detail, the marks people left and the work each part was
sent for have their own routes, so a viewer loads the big map once and polls
the small things.

A bloat mark on a part that lives in a repository keeps a ``weed`` lead
open; taking the last one back closes it. ``POST /maps/{slug}/send`` hands
parts to agents: a removal accepts the part's lead (the sender's bloat mark
is recorded first, because sending a removal is that decision), a fix opens
an embry0 issue and dispatches it, an analysis starts a read-only job. Every
send is recorded with the job it started or the error it met.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from embry0.api.deps import request_actor, require_admin
from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.api.schemas import RunWithRequest
from embry0.api.v1.run_with import checked_run_with
from embry0.services.knowledge_maps import (
    MAX_DECODED_BYTES,
    MAX_SEND,
    SLUG,
    SOURCE_PREFIX,
    BatchTooLarge,
    MapError,
    Mark,
    batch_task,
    map_lead,
    mark_counts,
    parse_map,
    part_repo,
    part_text,
    part_url,
    removable,
    send_task,
    source_key,
)
from embry0.services.leads import as_utc_iso, lead_id
from embry0.services.run_with import apply_run_with

logger = structlog.get_logger(__name__)
router = APIRouter()

_ACTION_SEND = "send a map part"


def _maps(request: Request) -> Any:
    repo = getattr(request.app.state, "knowledge_maps_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="knowledge maps are not initialized")
    return repo


def _leads(request: Request) -> Any:
    leads = getattr(request.app.state, "leads_repo", None)
    if leads is None:
        raise HTTPException(status_code=503, detail="leads are not initialized")
    return leads


def _slug(slug: str) -> str:
    if not SLUG.match(slug):
        raise HTTPException(status_code=404, detail="no such map")
    return slug


def _base_url(request: Request) -> str:
    return str(getattr(request.app.state.config, "dashboard_public_url", "") or "http://localhost:8200")


async def _parts(request: Request, slug: str) -> dict[str, Any]:
    """The map's header and its parts by id, parsed once per upload and kept."""
    stored = await _maps(request).graph_text(slug)
    if stored is None:
        raise HTTPException(status_code=404, detail="no such map")
    text, uploaded_at = stored
    cache: dict[str, Any] = request.app.state.__dict__.setdefault("knowledge_map_parts", {})
    hit = cache.get(slug)
    if hit is None or hit["uploaded_at"] != uploaded_at:
        graph = json.loads(text)
        hit = {
            "uploaded_at": uploaded_at,
            "title": graph.get("title") or slug,
            "repo": graph.get("repo"),
            "nodes": {n["id"]: n for n in graph.get("nodes") or []},
        }
        cache[slug] = hit
    return hit


def _marks_of(rows: list[dict[str, Any]]) -> list[Mark]:
    return [Mark(str(r["marked_by"]), str(r["verdict"]), str(r.get("note") or ""), r["marked_at"]) for r in rows]


def _mark_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "by": row["marked_by"],
        "verdict": row["verdict"],
        "note": row.get("note") or "",
        "at": as_utc_iso(row.get("marked_at")),
    }


def _work_view(row: dict[str, Any], base: str) -> dict[str, Any]:
    job = row.get("job_id")
    return {
        "kind": row["kind"],
        "repo": row["repo"],
        "lead_id": row.get("lead_id"),
        "job_id": job,
        "job_url": f"{base.rstrip('/')}/jobs/{job}" if job else None,
        "status": row.get("job_status") or ("failed" if row.get("error") else None),
        "pr_url": row.get("pr_url"),
        "cost_usd": float(row["total_cost_usd"]) if row.get("total_cost_usd") is not None else None,
        "error": row.get("error") or row.get("error_message"),
        "by": row.get("sent_by"),
        "note": row.get("note") or "",
        "sent_at": as_utc_iso(row.get("sent_at")),
        "finished_at": as_utc_iso(row.get("finished_at")),
    }


async def _sync_lead(
    request: Request, slug: str, part: dict[str, Any], node: dict[str, Any], now: datetime
) -> str | None:
    """Keep the part's weed lead open while someone marks it bloat, closed when nobody does; its id, or None.

    A part an agent cannot remove (a whole system or repository, :func:`removable`) has no lead: one opened
    for it before is closed.
    """
    repo = part_repo(part["repo"], node)
    if repo is None:
        return None
    marks = _marks_of(await _maps(request).marks(slug, node["id"]))
    key = source_key(slug, node["id"])
    if not removable(part["repo"], node) or not any(m.verdict == "bloat" for m in marks):
        await _leads(request).close(lead_id("weed", repo, key), now=now)
        return None
    detail = await _maps(request).detail(slug, node["id"])
    lead = map_lead(
        slug=slug,
        map_title=part["title"],
        map_repo=part["repo"],
        node=node,
        detail=detail,
        marks=marks,
        url=part_url(_base_url(request), slug, node["id"]),
    )
    if lead is None:
        return None
    await _leads(request).upsert([lead], now=now)
    return lead.id


# ---- maps ----


@router.get("/maps")
async def list_maps(request: Request) -> dict[str, Any]:
    rows = await _maps(request).summaries()
    return {
        "maps": [
            {
                "slug": r["slug"],
                "title": r["title"],
                "repo": r.get("repo"),
                "commit": r.get("commit_sha"),
                "source": r.get("source") or "",
                "generated_at": as_utc_iso(r.get("generated_at")),
                "uploaded_at": as_utc_iso(r.get("uploaded_at")),
                "uploaded_by": r.get("uploaded_by") or "",
                "nodes": r.get("node_count") or 0,
                "edges": r.get("edge_count") or 0,
            }
            for r in rows
        ]
    }


@router.put("/maps/{slug}")
async def put_map(slug: str, request: Request) -> dict[str, Any]:
    """Store a scanner's upload; a part that left the map closes its lead, a marked part's lead is refreshed."""
    _slug(slug)
    raw = await request.body()
    if "gzip" in request.headers.get("content-encoding", "").lower():
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as f:
                raw = f.read(MAX_DECODED_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise HTTPException(status_code=400, detail="the body is not valid gzip") from exc
    if len(raw) > MAX_DECODED_BYTES:
        raise HTTPException(status_code=413, detail=f"a map is at most {MAX_DECODED_BYTES // (1024 * 1024)} MB of JSON")
    try:
        parsed = parse_map(json.loads(raw), slug)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="the body is not JSON") from exc
    except MapError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # A key limited to an allowlist may upload only a map of a repository on it, never an organization map.
    await enforce_repo_allowlist(request, parsed.repo, action="upload a map")
    now = datetime.now(UTC)
    try:
        generated = datetime.fromisoformat(parsed.generated_at.replace("Z", "+00:00")) if parsed.generated_at else None
    except ValueError:
        generated = None
    await _maps(request).put(
        slug=slug,
        title=parsed.title,
        repo=parsed.repo,
        commit_sha=parsed.commit,
        source=parsed.source,
        generated_at=generated,
        uploaded_by=request_actor(request),
        graph=parsed.graph,
        details=parsed.details,
        now=now,
    )
    part = await _parts(request, slug)
    closed = 0
    for row in await _leads(request).open_by_source_prefix("weed", f"{SOURCE_PREFIX}{slug}:"):
        node_id = str((row.get("data") or {}).get("node") or "")
        if node_id not in part["nodes"] and await _leads(request).close(str(row["id"]), now=now):
            closed += 1
    marked = {str(m["node_id"]) for m in await _maps(request).marks(slug) if m["verdict"] == "bloat"}
    refreshed = 0
    for node_id in sorted(marked & set(part["nodes"])):
        if await _sync_lead(request, slug, part, part["nodes"][node_id], now):
            refreshed += 1
    logger.info("knowledge_map_uploaded", slug=slug, nodes=len(parsed.graph["nodes"]), leads=refreshed, closed=closed)
    return {
        "slug": slug,
        "nodes": len(parsed.graph["nodes"]),
        "edges": len(parsed.graph["edges"]),
        "leads": refreshed,
        "leads_closed": closed,
    }


@router.get("/maps/{slug}")
async def get_map(slug: str, request: Request) -> Response:
    """The parts and links as stored: one fetch draws the whole map. ETag per upload."""
    stored = await _maps(request).graph_text(_slug(slug))
    if stored is None:
        raise HTTPException(status_code=404, detail="no such map")
    text, uploaded_at = stored
    etag = '"' + hashlib.sha256(f"{slug}:{uploaded_at.isoformat()}".encode()).hexdigest()[:20] + '"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=text, media_type="application/json", headers=headers)


@router.delete("/maps/{slug}", dependencies=[require_admin])
async def delete_map(slug: str, request: Request) -> dict[str, Any]:
    if not await _maps(request).delete(_slug(slug)):
        raise HTTPException(status_code=404, detail="no such map")
    request.app.state.__dict__.get("knowledge_map_parts", {}).pop(slug, None)
    return {"slug": slug, "deleted": True}


@router.get("/maps/{slug}/parts/{node_id:path}")
async def get_part(slug: str, node_id: str, request: Request) -> dict[str, Any]:
    """One part: what the map says about it, everyone's marks, and the work it was last sent for."""
    part = await _parts(request, _slug(slug))
    node = part["nodes"].get(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="no such part on this map")
    maps = _maps(request)
    work = await maps.work(slug, node_id)
    repo = part_repo(part["repo"], node)
    lead = await _leads(request).get(lead_id("weed", repo, source_key(slug, node_id))) if repo else None
    return {
        "map": {"slug": slug, "title": part["title"], "repo": part["repo"]},
        "node": node,
        "repo": repo,
        "removable": removable(part["repo"], node),
        "detail": await maps.detail(slug, node_id),
        "marks": [_mark_view(m) for m in await maps.marks(slug, node_id)],
        "work": _work_view(work[0], _base_url(request)) if work else None,
        "lead": {"id": lead["id"], "state": lead["state"]} if lead else None,
    }


# ---- marks ----


class MarkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=300)
    verdict: Literal["bloat", "keep", "unsure", "clear"]
    note: str = Field(default="", max_length=500)
    #: Who marked it: the signed-in person, or whom a scanner's key acts for.
    by: str = Field(min_length=1, max_length=100)


@router.get("/maps/{slug}/marks")
async def list_marks(
    slug: str, request: Request, by: str | None = Query(default=None, description="Whose verdict is 'mine'")
) -> dict[str, Any]:
    rows = await _maps(request).marks(_slug(slug))
    return {"marks": mark_counts(rows, by)}


@router.post("/maps/{slug}/marks")
async def post_mark(slug: str, body: MarkBody, request: Request) -> dict[str, Any]:
    """Mark a part, or take a mark back; the part's weed lead follows the bloat marks."""
    part = await _parts(request, _slug(slug))
    node = part["nodes"].get(body.node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="no such part on this map")
    await enforce_repo_allowlist(request, part_repo(part["repo"], node), action="mark a map part")
    maps, now = _maps(request), datetime.now(UTC)
    if body.verdict == "clear":
        await maps.clear_mark(slug, body.node_id, body.by)
    else:
        await maps.set_mark(slug, body.node_id, body.by, body.verdict, body.note.strip(), now=now)
    lead = await _sync_lead(request, slug, part, node, now)
    return {
        "node_id": body.node_id,
        "marks": [_mark_view(m) for m in await maps.marks(slug, body.node_id)],
        "lead_id": lead,
    }


# ---- sending parts to agents ----


class SendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: list[str] = Field(min_length=1, max_length=MAX_SEND)
    #: remove, fix or analysis; none sends each part for what its sender's mark says (bloat: remove, else analysis).
    kind: Literal["remove", "fix", "analysis"] | None = None
    run_with: RunWithRequest | None = None
    note: str = Field(default="", max_length=1000)
    by: str = Field(min_length=1, max_length=100)
    #: One job for all the parts of each repository and kind (one pull request), instead of one job per part.
    together: bool = False


async def _start_removal(
    request: Request, slug: str, part: dict[str, Any], node: dict[str, Any], body: SendBody, run_with: Any
) -> tuple[str, str]:
    """Accept the part's weed lead: the sender's bloat mark first, then the lead's own start."""
    from embry0.api.v1.leads import start_lead
    from embry0.api.v1.proposals import record_lead_accept
    from embry0.services.proposals import still_open

    now = datetime.now(UTC)
    maps = _maps(request)
    if not removable(part["repo"], node):
        raise HTTPException(
            status_code=409,
            detail="this part is a whole system or repository, not a file in one: removing it is not a code change",
        )
    mine = next((m for m in await maps.marks(slug, node["id"]) if m["marked_by"] == body.by), None)
    if mine is None or mine["verdict"] != "bloat":
        await maps.set_mark(slug, node["id"], body.by, "bloat", body.note.strip()[:500], now=now)
    lid = await _sync_lead(request, slug, part, node, now)
    if lid is None:
        raise HTTPException(status_code=409, detail="this part lives in no repository, so no agent can remove it")
    proposals = getattr(request.app.state, "proposals_repo", None)
    asked = still_open(lid, await proposals.decisions(), now) if proposals is not None else False
    job_id = await start_lead(request, lid, by=body.by, run_with=run_with)
    if asked:
        row = await _leads(request).get(lid)
        await record_lead_accept(request, row, by=body.by, reason=body.note, job_id=job_id)
    return lid, job_id


async def _start_fix(
    request: Request, repo: str, node: dict[str, Any], task: str, by: str, slug: str, run_with: Any
) -> str:
    from embry0.api.v1.leads import _dispatch_issue

    issue_id = await request.app.state.issues_repo.create(
        title=f"Fix {node.get('label') or node['id']}"[:300],
        body=task,
        labels=["embry0-map", f"map:{slug}"],
        repo=repo,
        created_by="map-send",
    )
    return await _dispatch_issue(request, issue_id, "open", tracker="", by=by, run_with=run_with)


async def _start_analysis(request: Request, repo: str, title: str, task: str, by: str, run_with: Any) -> str:
    executor = getattr(request.app.state, "issue_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="executor not initialized")
    refusal = await executor.dispatch_refusal(actor=by)
    if refusal is not None:
        raise HTTPException(status_code=429, detail=refusal.public_message)
    jobs = request.app.state.jobs_repo
    job_id: str = await jobs.create(
        repo=repo,
        task=task,
        trace_id=f"trc-{uuid.uuid4().hex[:12]}",
        pipeline_template="analysis-default",
        pipeline_config=apply_run_with(None, run_with),
        dispatched_by=by,
    )
    executor.submit_job(job_id, repo=repo, title=f"[map] {title}", body=task, kind="map")
    return job_id


_VERB = {"remove": "Remove", "fix": "Fix", "analysis": "Analyse"}


async def _send_together(
    request: Request,
    slug: str,
    part: dict[str, Any],
    repo: str,
    kind: str,
    members: list[tuple[str, dict[str, Any]]],
    body: SendBody,
    run_with: Any,
) -> tuple[dict[str, Any], dict[str, str]]:
    """One job for several parts of one repository: a removal accepts each part's lead with that one job.

    Returns the job's summary and each part's lead id (removals only).
    """
    from embry0.api.v1.leads import _dispatch_issue
    from embry0.api.v1.proposals import record_lead_accept
    from embry0.services.proposals import still_open

    maps, now = _maps(request), datetime.now(UTC)
    leads: dict[str, str] = {}
    job: dict[str, Any] = {"repo": repo, "kind": kind, "parts": len(members), "job_id": None, "error": None}
    try:
        if kind == "remove":
            mine = {str(m["node_id"]): m for m in await maps.marks(slug) if m["marked_by"] == body.by}
            for node_id, node in members:
                if mine.get(node_id, {}).get("verdict") != "bloat":
                    await maps.set_mark(slug, node_id, body.by, "bloat", body.note.strip()[:500], now=now)
                lid = await _sync_lead(request, slug, part, node, now)
                if lid:
                    leads[node_id] = lid
        parts = [(node, await maps.detail(slug, node_id)) for node_id, node in members]
        try:
            task = batch_task(kind, repo=repo, parts=parts, map_title=part["title"], note=body.note.strip(), by=body.by)
        except BatchTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        only = members[0][1]
        title = (
            f"{_VERB[kind]} {only.get('label') or only['id']}"
            if len(members) == 1
            else f"{_VERB[kind]} {len(members)} parts of the {part['title']} map"
        )[:300]
        if kind == "analysis":
            job["job_id"] = await _start_analysis(request, repo, title, task, body.by, run_with)
        else:
            labels = ["embry0-map", f"map:{slug}", *(["embry0-lead", "lead:weed"] if kind == "remove" else [])]
            issue_id = await request.app.state.issues_repo.create(
                title=title, body=task, labels=labels, repo=repo, created_by="map-send"
            )
            job["job_id"] = await _dispatch_issue(request, issue_id, "open", tracker="", by=body.by, run_with=run_with)
        proposals = getattr(request.app.state, "proposals_repo", None)
        decisions = await proposals.decisions() if proposals is not None and leads else {}
        for lid in leads.values():
            if still_open(lid, decisions, now):
                row = await _leads(request).get(lid)
                if row is not None:
                    await record_lead_accept(request, row, by=body.by, reason=body.note, job_id=job["job_id"])
    except HTTPException as exc:
        job["error"] = str(exc.detail)
    return job, leads


@router.post("/maps/{slug}/send", status_code=202)
async def send_parts(slug: str, body: SendBody, request: Request) -> dict[str, Any]:
    """Send parts to agents: one job each, or with ``together`` one job per repository and kind.

    A part that cannot go says why and the others still go.
    """
    part = await _parts(request, _slug(slug))
    maps = _maps(request)
    mine = {str(m["node_id"]): m["verdict"] for m in await maps.marks(slug) if m["marked_by"] == body.by}
    checked: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
    for node_id in dict.fromkeys(body.node_ids):
        node = part["nodes"].get(node_id)
        can_remove = node is not None and removable(part["repo"], node)
        kind = body.kind or ("remove" if mine.get(node_id) == "bloat" and can_remove else "analysis")
        result: dict[str, Any] = {"node_id": node_id, "kind": kind, "lead_id": None, "job_id": None, "error": None}
        repo = part_repo(part["repo"], node) if node else None
        try:
            if node is None:
                raise HTTPException(status_code=404, detail="no such part on this map")
            if repo is None:
                raise HTTPException(status_code=409, detail="this part lives in no repository, so no agent can take it")
            await enforce_repo_allowlist(request, repo, action=_ACTION_SEND)
            if repo not in checked:
                checked[repo] = await checked_run_with(request, body.run_with, repo)
            run_with = checked[repo]
            if kind == "remove" and not can_remove:
                raise HTTPException(
                    status_code=409,
                    detail="this part is a whole system or repository, not a file in one: removing it is not a code change",
                )
            if body.together:
                groups.setdefault((repo, kind), []).append((node_id, node))
                continue
            if kind == "remove":
                result["lead_id"], result["job_id"] = await _start_removal(request, slug, part, node, body, run_with)
            else:
                text = part_text(node, await maps.detail(slug, node_id), part["title"])
                task = send_task(kind, repo=repo, node=node, text=text, note=body.note.strip(), by=body.by)
                start = (
                    _start_fix(request, repo, node, task, body.by, slug, run_with)
                    if kind == "fix"
                    else _start_analysis(request, repo, str(node.get("label") or node["id"]), task, body.by, run_with)
                )
                result["job_id"] = await start
        except HTTPException as exc:
            result["error"] = str(exc.detail)
        if repo is not None and node is not None and not (result["error"] or "").startswith("no such part"):
            await maps.add_send(
                slug=slug,
                node_id=node_id,
                kind=kind,
                repo=repo,
                sent_by=body.by,
                note=body.note.strip(),
                lead_id=result["lead_id"],
                job_id=result["job_id"],
                error=result["error"],
                now=datetime.now(UTC),
            )
        logger.info(
            "knowledge_map_part_sent",
            slug=slug,
            node_id=node_id,
            kind=kind,
            job_id=result["job_id"],
            error=result["error"],
        )
        results.append(result)
    jobs: list[dict[str, Any]] = []
    for (repo, group_kind), members in groups.items():
        job, leads = await _send_together(request, slug, part, repo, group_kind, members, body, checked[repo])
        jobs.append(job)
        for node_id, _node in members:
            await maps.add_send(
                slug=slug,
                node_id=node_id,
                kind=group_kind,
                repo=repo,
                sent_by=body.by,
                note=body.note.strip(),
                lead_id=leads.get(node_id),
                job_id=job["job_id"],
                error=job["error"],
                now=datetime.now(UTC),
            )
            results.append(
                {
                    "node_id": node_id,
                    "kind": group_kind,
                    "lead_id": leads.get(node_id),
                    "job_id": job["job_id"],
                    "error": job["error"],
                }
            )
        logger.info(
            "knowledge_map_parts_sent_together",
            slug=slug,
            repo=repo,
            kind=group_kind,
            parts=len(members),
            job_id=job["job_id"],
            error=job["error"],
        )
    sent = sum(1 for r in results if r["job_id"])
    return {"sent": sent, "failed": len(results) - sent, "results": results, "jobs": jobs}


@router.get("/maps/{slug}/work")
async def list_work(slug: str, request: Request) -> dict[str, Any]:
    """Each part's newest send and its job: running, a pull request, merged, failed."""
    rows = await _maps(request).work(_slug(slug))
    base = _base_url(request)
    return {"work": {str(r["node_id"]): _work_view(r, base) for r in rows}}
