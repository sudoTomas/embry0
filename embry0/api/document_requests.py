"""What the routes that store an operator-written document share: reading its body, and ``If-Match``.

The settings document (#160) and the progression content pack (#176) both
take a whole document in a request body and both name the version a write
expects to replace. One body reader, so a document past the limit is
refused (413) before it is held in memory, whether or not the request said
its length; and one ``If-Match`` parser, so both read the header alike.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

#: What a malformed ``If-Match`` is told.
IF_MATCH_EXAMPLE = 'If-Match must be the ETag a GET returned, e.g. "3"'


def if_match_versions(value: str | None) -> set[int] | None:
    """The versions ``If-Match`` accepts; None for any (no header, or ``*``).

    Strong comparison, as RFC 9110 has it for ``If-Match``: a weak tag
    (``W/"3"``) never matches, so a header of weak tags only accepts
    nothing. A tag that is not a quoted number is a 422.
    """
    if value is None or not value.strip():
        return None
    accepted: set[int] = set()
    for tag in (part.strip() for part in value.split(",")):
        if tag == "*":
            return None
        weak = tag.startswith("W/")
        text = tag.removeprefix("W/")
        if not (len(text) >= 3 and text[0] == text[-1] == '"' and text[1:-1].isdigit()):
            raise HTTPException(status_code=422, detail=IF_MATCH_EXAMPLE)
        if not weak:
            accepted.add(int(text[1:-1]))
    return accepted


async def read_capped_body(request: Request, limit: int, too_large: str) -> bytes:
    """The request body, refused with 413 (``too_large``) once it passes ``limit`` bytes.

    A ``Content-Length`` already past the limit is refused before anything
    is read; a body sent without one (chunked) is read in pieces and
    refused as soon as it passes the limit, so it is never held whole.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=too_large)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail=too_large)
        chunks.append(chunk)
    return b"".join(chunks)
