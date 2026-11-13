"""Answering a stored document too deep to serve (#431): its JSON text, never a 500.

The serializer behind a route's response model (pydantic-core) stops at
:data:`embry0.json_text.SERVABLE_DEPTH` levels of containers, and the one
behind a route with none (FastAPI's ``jsonable_encoder``) recurses once per
level, to the interpreter's limit (about 1,000; #479). The write paths
refuse a document past :data:`embry0.json_text.STORED_DEPTH` (``ServableFields``),
but a row stored before they did, or written by something other than the API,
can still hold one, and the read that shows it, or any list of rows it is in,
answered 500.

:class:`ServedRoute` is the route class of every router that answers stored
rows. It costs nothing on an answer that serializes: it only keeps the value
the endpoint returned. When serializing that value fails, and it holds a value
nested deeper than ``STORED_DEPTH``, it answers it again with every such value
of a row as its JSON text (:func:`served_answer`), and logs
``stored_document_served_as_text``. Any other failure is raised as before.
"""

from __future__ import annotations

import functools
import inspect
import weakref
from collections.abc import Callable, Coroutine, Mapping
from contextvars import ContextVar
from typing import Any

import structlog
from fastapi import Request, Response
from fastapi.routing import APIRoute
from pydantic import TypeAdapter

from embry0.json_text import STORED_DEPTH, nested_deeper_than, served

logger = structlog.get_logger(__name__)

#: The value the request's endpoint returned, kept for :meth:`ServedRoute.get_route_handler` to answer again.
_ANSWERED: ContextVar[list[Any] | None] = ContextVar("embry0_served_answer", default=None)

_ANY = TypeAdapter(Any)


def _rows(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, Mapping) for item in value)


def _served_row(row: Mapping[str, Any], *, nested: bool = True) -> dict[str, Any]:
    """``row`` with each field :func:`~embry0.json_text.served`; a field listing rows serves each row's fields
    (one level: a row in such a list serves its own fields plainly)."""
    return {
        key: [_served_row(item, nested=False) for item in value] if nested and _rows(value) else served(value)
        for key, value in row.items()
    }


def served_answer(answer: Any) -> Any:
    """An answer, as JSON-ready Python values, with each stored document too deep to serve as its JSON text.

    A row (an object) serves each field as it is, or, nested deeper than
    ``STORED_DEPTH``, as its text; a field that lists rows (``{"jobs": [...]}``)
    and an answer that is a list of rows serve each row the same way. So one
    row too deep costs only its own deep field, and every other row and field
    reads as before.
    """
    if isinstance(answer, Mapping):
        return _served_row(answer)
    if _rows(answer):
        return [_served_row(item) for item in answer]
    return served(answer)


#: Each endpoint's remembering wrapper, made once: a function served at two paths (the repository at its address
#: with and without the separator) stays one endpoint, as the route-order checks expect.
_WRAPPED: weakref.WeakKeyDictionary[Callable[..., Any], Callable[..., Any]] = weakref.WeakKeyDictionary()


def _remembering(endpoint: Callable[..., Any]) -> Callable[..., Any]:
    """``endpoint``, keeping what it returns for the route's handler; a sync endpoint is left as it is."""
    if not inspect.iscoroutinefunction(endpoint):
        return endpoint
    if endpoint in _WRAPPED:
        return _WRAPPED[endpoint]

    @functools.wraps(endpoint)
    async def remembered(*args: Any, **kwargs: Any) -> Any:
        answer = await endpoint(*args, **kwargs)
        kept = _ANSWERED.get()
        if kept is not None:
            kept.append(answer)
        return answer

    _WRAPPED[endpoint] = remembered
    return remembered


class ServedAnswers(APIRoute):
    """A route whose handler, when serializing what a remembering endpoint returned fails on a value too deep,
    answers it with that value as its JSON text. :class:`ServedRoute` makes its endpoint remember; a route that
    calls another's endpoint (an old path) inherits whatever that one does."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def served_handler(request: Request) -> Response:
            kept: list[Any] = []
            token = _ANSWERED.set(kept)
            try:
                return await handler(request)
            except (ValueError, RecursionError) as exc:
                # Only a failure serializing what the endpoint answered, with a value too deep to serve in it:
                # pydantic-core's "depth exceeded" for a route with a response model (a ValueError), and for a
                # route with none, FastAPI's jsonable_encoder, which recurses once per level (#479).
                if not kept:
                    raise
                plain = kept[-1] if self.response_field is None else self._plain(kept[-1])
                if not nested_deeper_than(plain, STORED_DEPTH):
                    raise
                logger.warning(
                    "stored_document_served_as_text",
                    path=self.path,
                    error=str(exc)[:200],
                    hint="a stored value nests deeper than the answer can serialize; rewrite it to repair the row",
                )
                return Response(
                    _ANY.dump_json(served_answer(plain)),
                    status_code=self.status_code or 200,
                    media_type="application/json",
                )
            finally:
                _ANSWERED.reset(token)

        return served_handler

    def _plain(self, answer: Any) -> Any:
        """The answer as the response model gives it, in Python values (that mode walks a deep value as it is). It
        validated before serializing it failed, so it validates now."""
        field = self.response_field
        assert field is not None  # noqa: S101 - get_route_handler calls this only for a route with a response model
        value, _errors = field.validate(answer, {}, loc=("response",))
        return field.serialize(
            value,
            mode="python",
            include=self.response_model_include,
            exclude=self.response_model_exclude,
            by_alias=self.response_model_by_alias,
            exclude_unset=self.response_model_exclude_unset,
            exclude_defaults=self.response_model_exclude_defaults,
            exclude_none=self.response_model_exclude_none,
        )


class ServedRoute(ServedAnswers):
    """A route whose answer serves a stored document too deep to serialize as its JSON text (module docstring)."""

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        super().__init__(path, _remembering(endpoint), **kwargs)
