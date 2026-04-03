"""Agents API — agent type registry metadata."""

from fastapi import APIRouter

from legion.agents.registry import AGENT_TYPES

router = APIRouter()


@router.get("/agents/types")
async def list_agent_types() -> list[dict]:
    """List all available agent types with their metadata."""
    return AGENT_TYPES
