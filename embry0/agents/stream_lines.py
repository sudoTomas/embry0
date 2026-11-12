"""The next line of a stream whose lines can run past the reader's limit (#386, #466).

``asyncio.StreamReader.readline()`` does not raise ``LimitOverrunError`` for a
line longer than the reader's limit: it raises ``ValueError``. And when the
line's newline has not arrived yet, it clears what it buffered, so the rest of
the long line is read as a line of its own. :func:`read_line` drops such a line
through its newline instead, so the next read starts at the line after it.

Stdlib only: the sandbox images bake ``embry0/agents/``, and the MCP stdio
client reads its server's answers with it there.
"""

from __future__ import annotations

import asyncio


async def read_line(reader: asyncio.StreamReader) -> bytes | None:
    """The next line of ``reader``: ``b""`` at its end, ``None`` for a line over the reader's limit.

    A line longer than the limit is dropped through its newline (waiting for
    it when it has not arrived yet), so the next read starts at the line after
    it. The last line of a stream that ends without a newline is returned
    without one.
    """
    try:
        return await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError as exc:
        return exc.partial  # the last line, without a newline; b"" at the end
    except asyncio.LimitOverrunError as exc:
        buffered = exc.consumed
    # readuntil leaves the long line in the buffer: drop what is buffered of it
    # (up to its newline when that is there) and look for its end again.
    while True:
        await reader.readexactly(buffered)
        try:
            await reader.readuntil(b"\n")
        except asyncio.IncompleteReadError:
            return None  # the stream ended inside the long line
        except asyncio.LimitOverrunError as exc:
            buffered = exc.consumed
        else:
            return None
