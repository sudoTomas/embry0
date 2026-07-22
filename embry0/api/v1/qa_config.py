"""External per-repo QA config endpoints (EMB-48 store, EMB-50 onboarding).

GET returns the store's raw qa.yaml for a repo (404 when absent — the
in-repo fallback is invisible to this API by design; it reports only what
the store holds). PUT schema-validates the body as qa.yaml v2 and writes it
atomically — the manual counterpart of the onboard pipeline's write, and
EMB-49's bootstrap target. DELETE deactivates a repo (falls back to the
in-repo file on the next run).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from embry0.workflows.qa.qa_config_store import (
    external_qa_config_path,
    load_external_qa_yaml,
    save_external_qa_yaml,
)

router = APIRouter()


@router.get("/repos/{owner}/{repo}/qa-config")
async def get_qa_config(owner: str, repo: str) -> Response:
    text = load_external_qa_yaml(f"{owner}/{repo}")
    if text is None:
        raise HTTPException(status_code=404, detail="no external QA config stored for this repo")
    return Response(content=text, media_type="application/yaml")


@router.put("/repos/{owner}/{repo}/qa-config")
async def put_qa_config(owner: str, repo: str, request: Request) -> dict[str, str]:
    from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

    text = (await request.body()).decode("utf-8", errors="replace")
    if not text.strip():
        raise HTTPException(status_code=400, detail="empty body — send the qa.yaml v2 text")
    try:
        parse_qa_yaml_v2(text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"qa.yaml v2 validation failed: {exc}") from exc
    try:
        path = save_external_qa_yaml(f"{owner}/{repo}", text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"repo": f"{owner}/{repo}", "path": str(path)}


@router.delete("/repos/{owner}/{repo}/qa-config", status_code=204, response_model=None)
async def delete_qa_config(owner: str, repo: str) -> None:
    path = external_qa_config_path(f"{owner}/{repo}")
    if path is None or not path.is_file():
        return
    path.unlink()
