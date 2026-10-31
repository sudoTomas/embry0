"""The lead-kind catalogue (#166): what each kind of work on offer is called, for any client.

- ``GET /lead-kinds``: ``{"kinds": [...]}``, every kind in the catalogue's
  order (``embry0/services/lead_kinds.py``), each with its plain names:
  ``id``, ``label``, ``plural``, ``about`` and ``found_by``, and ``lead``,
  whether it is stored as a lead (``GET /leads``).

The one place the dashboard reads a kind's names from, the game on or off:
the Decisions page's kind filter reads it, and so do the guild's quest board,
lead dialog and map. The game's words for a kind (its verb, creature, leader
and level) are not served here: each quest on ``GET /world``, a route of the
game, carries its own. The MCP tool ``lead_kinds`` reads it for an assistant.

Any signed-in caller may read it: it is the code's content, the same for
everyone and holding no secret. It changes only with a release, so its ETag
is a hash of what it serves: a client that holds it asks with
``If-None-Match`` and gets 304 until a deploy changes a kind, and
``no-cache`` makes a browser ask every time it reloads, so a new kind shows
on the first reload after the deploy.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final

from fastapi import APIRouter, Request, Response

from embry0.services.lead_kinds import catalogue

router = APIRouter()

#: Kept, but asked again on every use: the ETag makes the answer to that a 304 until a release changes it.
CACHE_CONTROL: Final = "private, no-cache"


def etag_of(body: Any) -> str:
    """The ETag of a body: a hash of what it says, so any change to a kind is a new tag."""
    return '"' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:20] + '"'


#: What the route serves: the catalogue is code, so it is built once per process.
BODY: Final = {"kinds": catalogue()}
ETAG: Final = etag_of(BODY)


@router.get("/lead-kinds", response_model=None)
async def read_lead_kinds(request: Request, response: Response) -> dict[str, Any] | Response:
    """Every kind of work on offer, in the catalogue's order, with what each is called."""
    headers = {"ETag": ETAG, "Cache-Control": CACHE_CONTROL}
    if ETAG in [tag.strip() for tag in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return BODY
