"""Sandbox profiles API."""

from fastapi import APIRouter, Depends, HTTPException

from legion.api.deps import get_profiles_repo
from legion.api.schemas import SandboxProfileRequest
from legion.storage.repositories.sandbox_profiles import SandboxProfilesRepository

router = APIRouter()


@router.get("/sandbox-profiles")
async def list_profiles(profiles: SandboxProfilesRepository = Depends(get_profiles_repo)) -> list[dict]:
    return await profiles.list()


@router.post("/sandbox-profiles", status_code=201)
async def create_profile(
    req: SandboxProfileRequest, profiles: SandboxProfilesRepository = Depends(get_profiles_repo)
) -> dict:
    await profiles.upsert(
        name=req.name,
        base_image=req.base_image,
        additional_packages=req.additional_packages,
        setup_commands=req.setup_commands,
        memory=req.memory,
        cpus=req.cpus,
        pids_limit=req.pids_limit,
        cap_drop=req.cap_drop,
        cap_add=req.cap_add,
        security_opt=req.security_opt,
        agent_timeout_seconds=req.agent_timeout_seconds,
        container_timeout_seconds=req.container_timeout_seconds,
    )
    return {"name": req.name, "status": "created"}


@router.get("/sandbox-profiles/{name}")
async def get_profile(name: str, profiles: SandboxProfilesRepository = Depends(get_profiles_repo)) -> dict:
    profile = await profiles.get(name)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.put("/sandbox-profiles/{name}")
async def update_profile(
    name: str, req: SandboxProfileRequest, profiles: SandboxProfilesRepository = Depends(get_profiles_repo)
) -> dict:
    await profiles.upsert(
        name=name,
        base_image=req.base_image,
        additional_packages=req.additional_packages,
        setup_commands=req.setup_commands,
        memory=req.memory,
        cpus=req.cpus,
        pids_limit=req.pids_limit,
        cap_drop=req.cap_drop,
        cap_add=req.cap_add,
        security_opt=req.security_opt,
        agent_timeout_seconds=req.agent_timeout_seconds,
        container_timeout_seconds=req.container_timeout_seconds,
    )
    return {"name": name, "status": "updated"}


@router.delete("/sandbox-profiles/{name}")
async def delete_profile(name: str, profiles: SandboxProfilesRepository = Depends(get_profiles_repo)) -> dict:
    await profiles.delete(name)
    return {"name": name, "status": "deleted"}
