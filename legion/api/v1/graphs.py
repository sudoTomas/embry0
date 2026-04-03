"""Graph execution API."""
from fastapi import APIRouter, Depends, HTTPException

from legion.api.deps import get_workflow_registry
from legion.api.schemas import GraphExecuteRequest
from legion.workflows.registry import WorkflowRegistry

router = APIRouter()


@router.get("/graphs/workflows")
async def list_workflows(registry: WorkflowRegistry = Depends(get_workflow_registry)) -> list[dict]:
    return registry.list()


@router.post("/graphs/execute", status_code=202)
async def execute_graph(req: GraphExecuteRequest, registry: WorkflowRegistry = Depends(get_workflow_registry)) -> dict:
    workflow = registry.get(req.workflow)
    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow '{req.workflow}' not found")
    return {"status": "accepted", "workflow": req.workflow, "message": "Graph execution queued"}


@router.get("/graphs/{job_id}/state")
async def get_graph_state(job_id: str) -> dict:
    return {"job_id": job_id, "state": None, "message": "Checkpoint inspection not yet implemented"}


@router.post("/graphs/{job_id}/resume")
async def resume_graph(job_id: str) -> dict:
    return {"job_id": job_id, "status": "resumed", "message": "Graph resume not yet implemented"}
