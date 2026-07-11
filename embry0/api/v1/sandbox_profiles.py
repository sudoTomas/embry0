"""Sandbox profiles API."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from embry0.api.deps import get_profiles_repo
from embry0.api.schemas import SandboxProfileRequest
from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository
from embry0.storage.seeds.sandbox_profiles_builtin import BUILTIN_SANDBOX_PROFILES

router = APIRouter()


@router.get("/sandbox-profiles")
async def list_profiles(profiles: SandboxProfilesRepository = Depends(get_profiles_repo)) -> list[dict[str, Any]]:
    return await profiles.list()


@router.post("/sandbox-profiles", status_code=201)
async def create_profile(
    req: SandboxProfileRequest, profiles: SandboxProfilesRepository = Depends(get_profiles_repo)
) -> dict[str, Any]:
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
        description=req.description,
        dind_enabled=req.dind_enabled,
        idle_timeout_seconds=req.idle_timeout_seconds,
        extra_networks=req.extra_networks,
        env_defaults=req.env_defaults,
        # is_builtin NOT exposed — it's server-controlled
    )
    return {"name": req.name, "status": "created"}


@router.get("/sandbox-profiles/{name}")
async def get_profile(name: str, profiles: SandboxProfilesRepository = Depends(get_profiles_repo)) -> dict[str, Any]:
    profile = await profiles.get(name)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.put("/sandbox-profiles/{name}")
async def update_profile(
    name: str, req: SandboxProfileRequest, profiles: SandboxProfilesRepository = Depends(get_profiles_repo)
) -> dict[str, Any]:
    existing = await profiles.get(name)
    if existing and existing.get("is_builtin"):
        raise HTTPException(
            status_code=403,
            detail=(
                f"'{name}' is a builtin profile and cannot be edited via the API. "
                f"To revert local changes, use POST /sandbox-profiles/{name}/reset."
            ),
        )
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
        description=req.description,
        dind_enabled=req.dind_enabled,
        idle_timeout_seconds=req.idle_timeout_seconds,
        extra_networks=req.extra_networks,
        env_defaults=req.env_defaults,
        # is_builtin NOT exposed — it's server-controlled
    )
    return {"name": name, "status": "updated"}


@router.delete("/sandbox-profiles/{name}")
async def delete_profile(name: str, profiles: SandboxProfilesRepository = Depends(get_profiles_repo)) -> dict[str, Any]:
    try:
        await profiles.delete(name)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"name": name, "status": "deleted"}


@router.post("/sandbox-profiles/{name}/reset")
async def reset_profile(name: str, profiles: SandboxProfilesRepository = Depends(get_profiles_repo)) -> dict[str, Any]:
    """Reset a builtin profile to its seed values. Returns 404 for non-builtins."""
    seed = BUILTIN_SANDBOX_PROFILES.get(name)
    if seed is None:
        raise HTTPException(
            status_code=404,
            detail=(f"'{name}' is not a builtin profile (only {sorted(BUILTIN_SANDBOX_PROFILES)} can be reset)"),
        )
    await profiles.upsert(
        name=name,
        is_builtin=True,
        _allow_builtin_overwrite=True,
        **seed,
    )
    refreshed = await profiles.get(name)
    return refreshed or {}
