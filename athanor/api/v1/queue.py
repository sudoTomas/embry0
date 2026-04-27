"""Queue API."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/queue")
async def get_queue() -> dict:
    return {"depth": 0, "paused": False, "active_jobs": 0, "max_concurrent": 10}


@router.post("/queue/pause")
async def pause_queue() -> dict:
    return {"status": "paused"}


@router.post("/queue/resume")
async def resume_queue() -> dict:
    return {"status": "resumed"}
