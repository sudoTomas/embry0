"""Health check endpoints."""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health/ready")
async def health_ready(request: Request) -> dict:
    checks: dict[str, str] = {}
    db = getattr(request.app.state, "db", None)
    if db and db.pool:
        try:
            await db.fetchval("SELECT 1")
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "error"
    else:
        checks["database"] = "not_connected"
    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": status, "checks": checks}
