"""Internal endpoint — sandboxes only — that mints batches of presigned
MinIO URLs scoped to the caller's (job_id, attempt_n).

Path: /api/v1/internal/qa/...

Auth: bearer token in the request body (NOT a cookie/header) so the
sandbox can curl this without going through CSRF middleware. The token
is the same per-sandbox token issued by ProxyManager and registered
with SandboxTokenRegistry at sandbox start.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from embry0.api.schemas import QAPresignBatchRequest, QAPresignBatchResponse
from embry0.execution.qa.presign import PresignAuthError, QAPresignService

router = APIRouter()


@router.post("/internal/qa/presign", response_model=QAPresignBatchResponse)
async def presign_batch(req: QAPresignBatchRequest, request: Request) -> dict[str, Any]:
    # Mint URLs against the SANDBOX-facing MinIO client (e.g.
    # ``minio-proxy:9100``), not the internal one (``minio:9000``). The MinIO
    # signature covers the request Host header, so the sandbox's Host on the
    # eventual PUT/GET must match what the orchestrator signed here. Falling
    # back to the internal client would mint URLs the sandbox cannot use.
    # Phase 1.5.
    minio = getattr(request.app.state, "qa_minio_sandbox", None) or getattr(request.app.state, "qa_minio", None)
    if minio is None:
        raise HTTPException(status_code=503, detail="QA artifact storage unavailable")
    tokens = request.app.state.qa_token_registry
    svc = QAPresignService(minio=minio, tokens=tokens, bucket="qa-artifacts")
    try:
        return await svc.mint_batch(
            sandbox_token=req.sandbox_token,
            paths=req.paths,
            expires_seconds=req.expires_seconds,
            direction=req.direction,
        )
    except PresignAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
