"""ServedRoute (#431): a stored value too deep to serialize is answered as its JSON text; nothing else changes.

On a tiny app of its own, no database: a row with a deep field, a list of
rows, a model, an endpoint that raises, and an answer that fails to serialize
for another reason.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_serializer

from embry0.api.served import ServedRoute, served_answer
from embry0.json_text import STORED_DEPTH


def _nested(levels: int) -> Any:
    value: Any = "leaf"
    for _ in range(levels):
        value = {"a": value}
    return value


DEEP = {"notes": _nested(300)}


class Row(BaseModel):
    id: str
    config: dict[str, Any] | None = None


class Page(BaseModel):
    rows: list[Row]
    total: int


class JsonOnly(BaseModel):
    """A model whose JSON fails for a reason of its own, as no deep value does: its Python form is fine."""

    id: str

    @field_serializer("id", when_used="json")
    def _refuse(self, value: str) -> str:
        raise ValueError("this field cannot be written as JSON")


def _client() -> TestClient:
    router = APIRouter(route_class=ServedRoute)

    @router.get("/row")
    async def row() -> dict[str, Any]:
        return {"id": "r1", "config": DEEP, "tags": ["a"]}

    @router.get("/rows")
    async def rows() -> list[dict[str, Any]]:
        return [{"id": "r1", "config": DEEP}, {"id": "r2", "config": {"x": 1}}]

    @router.get("/page")
    async def page() -> Page:
        return Page(rows=[Row(id="r1", config=DEEP), Row(id="r2", config={"x": 1})], total=2)

    @router.post("/made", status_code=201)
    async def made() -> dict[str, Any]:
        return {"id": "r3", "config": DEEP}

    @router.get("/raises")
    async def raises() -> dict[str, Any]:
        raise ValueError("the endpoint's own error")

    @router.get("/missing")
    async def missing() -> dict[str, Any]:
        raise HTTPException(status_code=404, detail="no such row")

    @router.get("/not-finite")
    async def not_finite() -> Page:
        return Page.model_construct(rows=[Row.model_construct(id="r1", config={"x": math.nan})], total=object())

    @router.get("/not-text")
    async def not_text() -> dict[str, Any]:
        return {"id": "r1", "config": {"x": b"\xff\xfe"}}  # validates; JSON cannot write it, and it is not deep

    @router.get("/json-only")
    async def json_only() -> JsonOnly:
        return JsonOnly(id="r1")

    @router.get("/no-model", response_model=None)
    async def no_model() -> Any:
        return {"id": "r1", "x": math.nan}  # rendered without pydantic; the response refuses NaN

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_a_row_serves_its_deep_field_as_text_and_the_rest_as_it_is() -> None:
    answer = _client().get("/api/row")
    assert answer.status_code == 200, answer.text[:200]
    body = answer.json()
    assert json.loads(body["config"]) == DEEP, "the deep field is its JSON text"
    assert (body["id"], body["tags"]) == ("r1", ["a"]), f"the other fields are as they are: {body}"


def test_a_list_of_rows_and_a_model_serve_only_the_deep_field_as_text() -> None:
    client = _client()
    rows = client.get("/api/rows")
    assert rows.status_code == 200, rows.text[:200]
    assert [r["id"] for r in rows.json()] == ["r1", "r2"]
    assert json.loads(rows.json()[0]["config"]) == DEEP and rows.json()[1]["config"] == {"x": 1}
    page = client.get("/api/page")
    assert page.status_code == 200, page.text[:200]
    body = page.json()
    assert body["total"] == 2 and json.loads(body["rows"][0]["config"]) == DEEP, body
    assert body["rows"][1]["config"] == {"x": 1}, "a row beside it is served as it is"


def test_the_answer_keeps_the_route_s_status() -> None:
    answer = _client().post("/api/made")
    assert answer.status_code == 201, f"{answer.status_code}: {answer.text[:200]}"


def test_every_other_failure_stands() -> None:
    client = _client()
    assert client.get("/api/raises").status_code == 500, "an endpoint's own ValueError is no stored document"
    assert client.get("/api/missing").status_code == 404
    # Reading the answer again in Python values warns of the value its model does not expect, then raises as before.
    with pytest.warns(UserWarning, match="serialized value may not be as expected"):
        assert client.get("/api/not-finite").status_code == 500, "a failure with nothing too deep is raised as before"
    assert client.get("/api/not-text").status_code == 500, "a value JSON cannot write is no value too deep"
    assert client.get("/api/json-only").status_code == 500, "a model's own JSON failure is raised as before"


def test_a_route_with_no_response_model_raises_its_own_error() -> None:
    app = _client().app
    with pytest.raises(ValueError, match="Out of range float values"):
        TestClient(app).get("/api/no-model")


def test_served_answer_serves_rows_and_lists_of_rows() -> None:
    deep = _nested(STORED_DEPTH + 1)
    assert served_answer({"a": deep, "b": 1})["a"] == json.dumps(deep)
    assert served_answer([{"a": deep}])[0]["a"] == json.dumps(deep)
    nested = served_answer({"rows": [{"a": deep, "b": [1]}]})
    assert nested["rows"][0]["a"] == json.dumps(deep) and nested["rows"][0]["b"] == [1]
    assert served_answer({"rows": [{"in": [{"a": deep}]}]})["rows"][0]["in"] == json.dumps([{"a": deep}]), (
        "a row inside a listed row serves its fields plainly: one level of rows"
    )
