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

from athanor.api.schemas import QAPresignBatchRequest, QAPresignBatchResponse
from athanor.execution.qa.presign import PresignAuthError, QAPresignService

router = APIRouter()


@router.post("/internal/qa/presign", response_model=QAPresignBatchResponse)
async def presign_batch(req: QAPresignBatchRequest, request: Request) -> dict[str, Any]:
    minio = getattr(request.app.state, "qa_minio", None)
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
