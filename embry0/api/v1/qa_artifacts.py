"""QA artifact serving — presigned GET redirects + SSE log streams.

Path layout (bucket: qa-artifacts):
    <job_id>/<attempt_n>/result.json
    <job_id>/<attempt_n>/logs/full.log
    <job_id>/<attempt_n>/logs/<service>.log
    <job_id>/<attempt_n>/screenshots/<phase>/<ts>-<slug>.png
    <job_id>/<attempt_n>/traces/<criterion-slug>.zip

The "latest attempt" is the highest <attempt_n> directory present in MinIO
under <job_id>/. Routes that don't specify an attempt resolve it dynamically.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from typing import Any, cast

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from embry0.api.deps import get_docker, get_qa_minio

router = APIRouter()

_SAFE_ARTIFACT_PATH = re.compile(r"^[A-Za-z0-9._:\-]+(/[A-Za-z0-9._:\-]+)*$")
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9_\-]+$")
# Service names: docker-compose service-name spec — alnum + _ - ., MUST start
# with an alnum character so the value can never look like a CLI flag (e.g.
# `--no-color`) when expanded into the docker argv list.
_SAFE_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
# App names (monorepo apps): alnum + `_-.`. Keep the leading-alnum lock so a
# hostile app name can never look like a CLI flag if it ever flows through
# argv elsewhere.
_SAFE_APP = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
# Artifact kinds the per-sub-task passthrough endpoint will serve. Anything
# outside this allow-list is a 400 (not 404) so a typo is distinguishable
# from "this kind happens to have no files yet".
_ARTIFACT_KINDS: frozenset[str] = frozenset({"screenshots", "network", "console", "traces"})
# Per-extension content types for the artifact passthrough. Default is
# octet-stream so an unexpected extension still downloads safely.
_ARTIFACT_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".har": "application/json",
    ".json": "application/json",
    ".log": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".zip": "application/zip",
}
# Bound concurrent `docker compose logs -f` subprocesses. 8 is generous for a
# single dashboard user (one live tail per visible service) but still small
# enough that an unauthenticated client can't fork-bomb the orchestrator by
# opening hundreds of streams. Excess requests get a 503 so the client can
# back off rather than hanging on an unbounded queue.
_MAX_LOG_STREAMS = 8
_active_log_streams = 0
_log_stream_lock = asyncio.Lock()

# Hard cap on the buffered ``result.json`` body in :func:`get_result`. The
# orchestrator dereferences a presigned URL on the agent's behalf and parses
# the body in-memory before returning it as JSON, so a hostile or buggy agent
# could otherwise upload a multi-GB ``result.json`` and OOM the orchestrator
# the first time anyone hits the dashboard. 5 MiB is comfortably more than a
# real result (a couple of KB to maybe ~100 KB with screenshots-as-base64) and
# small enough that an attacker can't get anywhere with it.
_MAX_RESULT_BYTES = 5 * 1024 * 1024  # 5 MiB

# Hard cap on a single per-sub-task artifact's body served via the
# passthrough endpoint. The orchestrator buffers the full body in memory
# (the MinIO SDK is sync, so streaming through the executor would require
# bouncing each chunk back to the loop — net change much larger than 30
# lines and harder to reason about). Pre-check the object size with
# ``stat_object`` and 413 before pulling any bytes.
#
# 50 MiB lands well above the realistic ceiling for inline-displayed
# artifacts: a multi-page screenshot is ~1-3 MiB, a HAR file with many
# requests but no response bodies tops out around 5 MiB, and a
# Playwright trace zip is typically 10-30 MiB. Anything larger is almost
# certainly a misconfiguration (e.g. response bodies inlined into a HAR)
# that the orchestrator should refuse rather than OOM on.
_MAX_ARTIFACT_BYTES = 50 * 1024 * 1024  # 50 MiB


def _check_safe(path: str) -> None:
    # Reject empty segments and segments that are entirely dots (`.`, `..`,
    # `...`, `....`, ...) before the regex check — the regex's character class
    # allows `.`, so a segment like `...` would otherwise sneak through.
    for seg in path.split("/"):
        if not seg or set(seg) == {"."}:
            raise HTTPException(status_code=404, detail="not found")
    if not _SAFE_ARTIFACT_PATH.match(path):
        raise HTTPException(status_code=404, detail="not found")


def _check_safe_job_id(job_id: str) -> None:
    # Defense-in-depth: job_id is concatenated into the MinIO prefix, so reject
    # anything that isn't a plain identifier even though FastAPI's path-param
    # decoding already strips slashes.
    if not job_id or not _SAFE_JOB_ID.match(job_id):
        raise HTTPException(status_code=404, detail="not found")


async def _resolve_latest_attempt(minio: Any, job_id: str) -> int | None:
    """Highest <attempt_n> with any object in MinIO under <job_id>/."""
    objs = await minio.list_objects("qa-artifacts", prefix=f"{job_id}/")
    attempts: set[int] = set()
    for o in objs:
        # o = "JOB/2/result.json"  -> "2"
        parts = o.split("/")
        if len(parts) >= 2 and parts[1].isdigit():
            attempts.add(int(parts[1]))
    return max(attempts) if attempts else None


# NOTE: Route ordering matters. `get_latest_screenshot` MUST be declared before
# `get_artifact` — FastAPI matches routes in declaration order, and the
# `{path:path}` catch-all in `get_artifact` would otherwise swallow
# `/screenshots/latest` and treat it as an arbitrary artifact path.
@router.get("/jobs/{job_id}/artifacts/screenshots/latest")
async def get_latest_screenshot(
    job_id: str,
    qa_minio: Any = Depends(get_qa_minio),
) -> RedirectResponse:
    # Validate input BEFORE any storage work.
    _check_safe_job_id(job_id)

    # Find every .png under any attempt's screenshots/ for this job. The prefix
    # is `{job_id}/` (not scoped to a single attempt) so a job that has rolled
    # over to a new attempt while the dashboard is polling still surfaces the
    # newest screenshot regardless of which attempt produced it.
    #
    # Use `list_objects_with_meta` so we get `last_modified` from the listing
    # itself — this endpoint is polled every ~5s by the dashboard, and the
    # previous "list + N stat_object" approach was N+1 round trips per poll.
    objs = await qa_minio.list_objects_with_meta("qa-artifacts", prefix=f"{job_id}/")
    screenshots = [o for o in objs if o["key"].endswith(".png") and "/screenshots/" in o["key"]]
    if not screenshots:
        raise HTTPException(status_code=404, detail="no screenshots yet")

    # We sort by MinIO's `last_modified` rather than trusting the filename
    # timestamp because:
    #   1) Clock drift inside the sandbox vs. MinIO can make the embedded
    #      timestamp disagree with the actual upload time.
    #   2) An agent could (in principle) upload screenshots out of order or
    #      with a non-conforming filename; we want the freshest *upload*, not
    #      the freshest *filename*.
    #
    # Tie-break: `(last_modified, attempt_int, key)` — equal-mtime screenshots
    # (possible during high-throughput bursts) resolve deterministically by
    # higher attempt number first, then by key as a final tiebreaker. Keys
    # whose `<attempt>` segment is missing or non-numeric are skipped
    # defensively rather than crashing the endpoint.
    def _sort_key(item: dict[str, Any]) -> tuple[Any, ...] | None:
        key = item["key"]
        parts = key.split("/")
        try:
            attempt_int = int(parts[1])
        except (IndexError, ValueError):
            return None
        return (item["last_modified"], attempt_int, key)

    keyed_with_none = [(s, _sort_key(s)) for s in screenshots]
    keyed: list[tuple[dict[str, Any], tuple[Any, ...]]] = [(s, k) for s, k in keyed_with_none if k is not None]
    if not keyed:
        raise HTTPException(status_code=404, detail="no screenshots yet")
    keyed.sort(key=lambda pair: pair[1], reverse=True)
    latest_key = keyed[0][0]["key"]
    url = await qa_minio.presign_get("qa-artifacts", latest_key, expires_seconds=300)
    return RedirectResponse(url=url, status_code=302)


async def _redirect_to_artifact(qa_minio: Any, job_id: str, path: str) -> RedirectResponse:
    """Resolve latest attempt and return a 302 to the MinIO presigned GET URL.

    Shared by both the catch-all `get_artifact` route and the `follow=false`
    fallback in `get_log`. Centralised so the SSE endpoint isn't coupled to
    `get_artifact`'s function-as-dependency-callable signature.
    """
    # Validate inputs BEFORE any storage work.
    _check_safe_job_id(job_id)
    _check_safe(path)

    # Resolve latest attempt for this job.
    n = await _resolve_latest_attempt(qa_minio, job_id)
    if n is None:
        raise HTTPException(status_code=404, detail="no attempts found")
    key = f"{job_id}/{n}/{path}"
    url = await qa_minio.presign_get("qa-artifacts", key, expires_seconds=300)
    return RedirectResponse(url=url, status_code=302)


# NOTE: Route ordering matters. `get_log` MUST be declared before `get_artifact`
# for the same reason `get_latest_screenshot` is — the `{path:path}` catch-all
# in `get_artifact` would otherwise swallow `/logs/<service>` and treat it as
# a static artifact path even when `?follow=true` is requested.
@router.get("/jobs/{job_id}/artifacts/logs/{service}", response_model=None)
async def get_log(
    job_id: str,
    service: str,
    follow: bool = False,
    qa_minio: Any = Depends(get_qa_minio),
    docker: Any = Depends(get_docker),
) -> RedirectResponse | StreamingResponse:
    # Validate inputs BEFORE any storage work. We use the `{service}` path
    # converter (no `:path`) so a slash in the service name yields a 404 from
    # FastAPI's router rather than reaching this handler — but defend in depth
    # anyway in case of future refactors.
    _check_safe_job_id(job_id)
    if "/" in service or not _SAFE_SERVICE.match(service):
        raise HTTPException(status_code=404, detail="not found")

    if not follow:
        # Static-file path: redirect to MinIO presigned GET.
        return await _redirect_to_artifact(qa_minio, job_id, f"logs/{service}.log")

    base = docker._build_base_cmd()  # noqa: SLF001
    project = f"qa_{job_id}"
    # docker compose -p <project> logs -f --tail 200 -- <service>
    # The `--` separator ensures `service` can never be interpreted as a flag
    # by the docker CLI even if (defence-in-depth) `_SAFE_SERVICE` ever loosens.
    cmd = base + ["compose", "-p", project, "logs", "-f", "--tail", "200", "--", service]

    # Bound concurrent streams. Acquire a slot BEFORE spawning so we don't
    # leak processes when at capacity, and release it inside `_stream`'s
    # `finally` so a client disconnect frees the slot immediately.
    global _active_log_streams
    async with _log_stream_lock:
        if _active_log_streams >= _MAX_LOG_STREAMS:
            raise HTTPException(status_code=503, detail="too many concurrent log streams")
        _active_log_streams += 1

    # Spawn the subprocess BEFORE returning the StreamingResponse so spawn
    # failures (docker missing, malformed argv, ENOENT) become a 5xx with a
    # useful detail rather than an empty 200 + an SSE stream that closes
    # immediately.
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (FileNotFoundError, OSError) as exc:
        async with _log_stream_lock:
            _active_log_streams -= 1
        raise HTTPException(status_code=500, detail=f"failed to spawn docker: {exc}") from exc

    async def _stream() -> AsyncGenerator[bytes, None]:
        global _active_log_streams
        try:
            assert proc.stdout is not None
            while True:
                try:
                    raw = await proc.stdout.readline()
                except asyncio.LimitOverrunError:
                    # Default StreamReader limit is 64KiB; chatty services can
                    # emit a single line longer than that. Drop the rest of the
                    # offending line and emit a truncation marker so the client
                    # sees that something was elided rather than the stream
                    # crashing mid-flight.
                    yield b"data: [line truncated >64KiB]\n\n"
                    # Drain whatever bytes are buffered for this overrun line
                    # so the next readline() starts at the next \n boundary.
                    try:
                        await proc.stdout.read(2**20)
                    except asyncio.LimitOverrunError:
                        # Successive overruns: keep yielding markers and trying.
                        continue
                    continue
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip()
                # SSE format: "data: <line>\n\n"
                yield f"data: {line}\n\n".encode()
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except TimeoutError:
                    proc.kill()
                    # Reap the zombie so the orchestrator process table doesn't
                    # accumulate <defunct> docker entries on disconnect storms.
                    await proc.wait()
            async with _log_stream_lock:
                _active_log_streams -= 1

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


@router.get("/jobs/{job_id}/qa/attempts")
async def list_attempts(
    job_id: str,
    qa_minio: Any = Depends(get_qa_minio),
) -> dict[str, Any]:
    """Enumerate attempts present in MinIO with per-attempt metadata.

    Returns ``{"attempts": [{attempt_n, has_result_json, screenshots_count}, ...]}``
    sorted ascending by ``attempt_n``. Returns an empty list (200, not 404) when
    no QA artifacts exist for the job — the dashboard uses this as a "does this
    job have any QA artifacts?" probe and needs to distinguish "no artifacts
    yet" from "invalid job id" (404).
    """
    _check_safe_job_id(job_id)

    objs = await qa_minio.list_objects("qa-artifacts", prefix=f"{job_id}/")
    attempts: dict[int, dict[str, Any]] = {}
    # No pagination — single-job listings are bounded by the per-job retention policy.
    # If a job ever accumulates >10k screenshots this should switch to chunked listing.
    for o in objs:
        # Keys look like "<job_id>/<attempt_n>/<rest>". Skip anything that
        # doesn't have a numeric attempt segment (defensive — same filter as
        # `_resolve_latest_attempt`).
        parts = o.split("/")
        if len(parts) < 2 or not parts[1].isdigit():
            continue
        n = int(parts[1])
        a = attempts.setdefault(
            n,
            {"attempt_n": n, "has_result_json": False, "screenshots_count": 0},
        )
        if o.endswith("/result.json"):
            a["has_result_json"] = True
        if o.endswith(".png") and "/screenshots/" in o:
            a["screenshots_count"] += 1
    return {"attempts": sorted(attempts.values(), key=lambda x: x["attempt_n"])}


@router.get("/jobs/{job_id}/qa/attempts/{attempt_n}/result")
async def get_result(
    job_id: str,
    attempt_n: int,
    qa_minio: Any = Depends(get_qa_minio),
) -> dict[str, Any]:
    """Server-side download of ``result.json`` for a specific attempt.

    Resolves a presigned GET URL via MinIO, fetches the body server-side, and
    returns the parsed JSON so the dashboard can render structured tables
    without a second round trip.
    """
    _check_safe_job_id(job_id)
    # Defense-in-depth: FastAPI parses `attempt_n` as int, but reject 0 /
    # negatives explicitly before they get concatenated into the MinIO key.
    if attempt_n <= 0:
        raise HTTPException(status_code=404, detail="not found")

    key = f"{job_id}/{attempt_n}/result.json"
    url = await qa_minio.presign_get("qa-artifacts", key, expires_seconds=60)
    # Stream the upstream response so we can enforce ``_MAX_RESULT_BYTES``
    # before the whole body lands in memory. ``httpx.AsyncClient.get`` would
    # buffer the entire response unconditionally — fine for trusted endpoints,
    # but ``result.json`` is uploaded by an in-sandbox agent and must be
    # treated as untrusted. Map transport-level failures (timeout / connect
    # error) to 504 / 502 here so they don't bubble out as a generic 500 from
    # FastAPI's exception handler.
    buf = bytearray()
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            async with client.stream("GET", url) as r:
                if r.status_code == 404:
                    raise HTTPException(status_code=404, detail="result.json not found for this attempt")
                if r.status_code != 200:
                    raise HTTPException(status_code=502, detail=f"upstream {r.status_code}")

                # Trust ``Content-Length`` when present and abort BEFORE
                # touching the body. A malformed header (non-int) falls
                # through to the streaming check below.
                cl_header = r.headers.get("content-length")
                if cl_header is not None:
                    try:
                        if int(cl_header) > _MAX_RESULT_BYTES:
                            raise HTTPException(
                                status_code=413,
                                detail=f"result.json too large ({cl_header} bytes)",
                            )
                    except ValueError:
                        pass

                async for chunk in r.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > _MAX_RESULT_BYTES:
                        raise HTTPException(status_code=413, detail="result.json exceeded size cap")
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail=f"upstream timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc

    # MinIO can return a 200 with a non-JSON error body (rare, but possible
    # if a misconfigured bucket policy intercepts the GET, or an LB serves an
    # HTML error page). Catch JSONDecodeError and return a clear 502 instead
    # of letting it propagate as a 500 from FastAPI's response serializer.
    try:
        return cast(dict[str, Any], json.loads(bytes(buf)))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"upstream returned non-JSON body: {exc}") from exc


@router.get("/jobs/{job_id}/artifacts/{path:path}")
async def get_artifact(
    job_id: str,
    path: str,
    qa_minio: Any = Depends(get_qa_minio),
) -> RedirectResponse:
    return await _redirect_to_artifact(qa_minio, job_id, path)


# ---------------------------------------------------------------------------
# Per-sub-task artifact passthrough (Phase 5B)
#
# Sub-task ids are formed as `<parent_job_id>__<app_name>` (see
# `subtask_nodes.py:84`). For each sub-task the agent uploads bucketed
# artifacts into MinIO under:
#
#     <sub_job_id>/<attempt_n>/<kind>/<filename>
#
# where `<kind>` is one of `screenshots | network | console | traces`. The
# dashboard wants to render those inline (screenshots as a grid, console as
# expandable text, network failures parsed as JSON), so the orchestrator
# proxies the bytes through with the existing dashboard auth — no presigned
# URLs leaked to the browser.
# ---------------------------------------------------------------------------


def _check_safe_app(app: str) -> None:
    """Reject app names that aren't a single safe segment.

    The app name is concatenated into the MinIO prefix as
    `<run_id>__<app>/...`. Disallow anything that could break out of that
    segment (slashes, leading dots) or look like a CLI flag (leading `-`).
    """
    if not app or "/" in app or not _SAFE_APP.match(app):
        raise HTTPException(status_code=400, detail="bad app name")


def _check_safe_filename(filename: str) -> None:
    """Reject filenames that try to break out of the artifact directory.

    The brief: any `..`, `/`, or leading `.` is rejected. We also enforce
    `_SAFE_ARTIFACT_PATH`'s single-segment shape so the route can never see
    an embedded slash even if FastAPI's path-param decoding lets one through.
    """
    if not filename or "/" in filename or filename.startswith(".") or ".." in filename:
        raise HTTPException(status_code=400, detail="bad artifact filename")
    # Defense-in-depth: reject anything outside the artifact charset.
    if not re.match(r"^[A-Za-z0-9._:\-]+$", filename):
        raise HTTPException(status_code=400, detail="bad artifact filename")


def _check_artifact_kind(kind: str) -> None:
    if kind not in _ARTIFACT_KINDS:
        raise HTTPException(status_code=400, detail="bad artifact kind")


def _media_type_for(filename: str) -> str:
    """Pick a media type by extension, falling back to octet-stream."""
    lowered = filename.lower()
    for ext, mt in _ARTIFACT_MEDIA_TYPES.items():
        if lowered.endswith(ext):
            return mt
    return "application/octet-stream"


async def _find_latest_attempt_with_artifact(minio: Any, sub_job_id: str, kind: str, filename: str) -> int | None:
    """Return the highest attempt under <sub_job_id>/ whose <kind>/<filename> exists.

    Mirrors `_resolve_latest_attempt` but additionally requires that the
    specific `<kind>/<filename>` object is present in that attempt's tree —
    a newer attempt that hasn't uploaded this kind yet shouldn't shadow an
    older attempt that did.
    """
    objs = await minio.list_objects("qa-artifacts", prefix=f"{sub_job_id}/")
    attempts: set[int] = set()
    suffix = f"/{kind}/{filename}"
    for o in objs:
        # o = "<sub>/<n>/<kind>/<filename>" — accept only exact matches.
        parts = o.split("/")
        if len(parts) < 2 or not parts[1].isdigit():
            continue
        if o == f"{sub_job_id}/{parts[1]}{suffix}":
            attempts.add(int(parts[1]))
    return max(attempts) if attempts else None


async def _find_latest_attempt_for_kind(minio: Any, sub_job_id: str, kind: str) -> int | None:
    """Return the highest attempt that has any object under <sub>/<n>/<kind>/.

    Used by the listing endpoint, which doesn't know a specific filename.
    Returns None when the sub-task has no <kind> uploads yet on any attempt.
    """
    objs = await minio.list_objects("qa-artifacts", prefix=f"{sub_job_id}/")
    attempts: set[int] = set()
    for o in objs:
        parts = o.split("/")
        # Require <sub>/<n>/<kind>/<filename> shape; the basename slot must
        # be non-empty so we don't count "directory marker" keys.
        if len(parts) < 4 or not parts[1].isdigit():
            continue
        if parts[2] == kind and parts[3]:
            attempts.add(int(parts[1]))
    return max(attempts) if attempts else None


@router.get("/qa/runs/{run_id}/apps/{app}/artifacts/{kind}")
async def list_app_artifacts(
    run_id: str,
    app: str,
    kind: str,
    qa_minio: Any = Depends(get_qa_minio),
) -> dict[str, list[str]]:
    """List basenames available for `kind` on the latest attempt of this sub-task.

    Returns ``{"filenames": []}`` (200, not 404) when the sub-task has no
    uploads of this kind — the dashboard treats the empty list as "no
    artifacts captured" and renders an empty-state message rather than an
    error.
    """
    _check_safe_job_id(run_id)
    _check_safe_app(app)
    _check_artifact_kind(kind)

    sub = f"{run_id}__{app}"
    n = await _find_latest_attempt_for_kind(qa_minio, sub, kind)
    if n is None:
        return {"filenames": []}

    objs = await qa_minio.list_objects("qa-artifacts", prefix=f"{sub}/{n}/{kind}/")
    # Strip the prefix so the client sees just the basenames it needs to pass
    # back to the GET endpoint. Defensive: drop empty / nested entries (the
    # agent shouldn't be writing nested dirs under <kind>/, but if it ever
    # does we don't want to surface those as filenames).
    prefix = f"{sub}/{n}/{kind}/"
    filenames = sorted(
        o[len(prefix) :] for o in objs if o.startswith(prefix) and "/" not in o[len(prefix) :] and len(o) > len(prefix)
    )
    return {"filenames": filenames}


@router.get("/qa/runs/{run_id}/apps/{app}/artifacts/{kind}/{filename}")
async def get_app_artifact(
    run_id: str,
    app: str,
    kind: str,
    filename: str,
    qa_minio: Any = Depends(get_qa_minio),
) -> Response:
    """Stream a per-sub-task artifact (screenshot / HAR / console log) from MinIO.

    Path layout in MinIO: ``<run_id>__<app>/<attempt_n>/<kind>/<filename>``.
    `kind` is one of: screenshots, network, console, traces. Path-traversal
    hardened: any '..', '/', leading '.', or non-allowlisted kind → 400.
    """
    _check_safe_job_id(run_id)
    _check_safe_app(app)
    _check_artifact_kind(kind)
    _check_safe_filename(filename)

    sub = f"{run_id}__{app}"
    n = await _find_latest_attempt_with_artifact(qa_minio, sub, kind, filename)
    if n is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    key = f"{sub}/{n}/{kind}/{filename}"

    # HEAD-equivalent: ask MinIO for the object size before pulling any
    # bytes. A multi-GB artifact (misconfigured agent, malicious upload)
    # would otherwise OOM the orchestrator the first time anyone clicks
    # the panel. ``stat_object`` is a single S3 HEAD round-trip — cheap
    # compared to ``get_object``.
    stat = await qa_minio.stat_object("qa-artifacts", key)
    size = stat.get("size")
    if isinstance(size, int) and size > _MAX_ARTIFACT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"artifact too large ({size} bytes > {_MAX_ARTIFACT_BYTES} cap)",
        )

    body = await qa_minio.get_object_bytes("qa-artifacts", key)
    return Response(
        content=body,
        media_type=_media_type_for(filename),
        headers={"Cache-Control": "private, max-age=300"},
    )
