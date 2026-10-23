"""A fake OpenAI-compatible HTTP server: real sockets, scripted answers, every request recorded.

Serves ``POST <root>/chat/completions`` from a queue of ``(status, body)`` answers and
``GET <root>/models`` (and ``/key``) with ``get_status``. Runs in a thread, so an
asyncio test can call it with a real httpx client.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeOpenAIServer:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.get_status = 200
        self._answers: deque[tuple[int, Any]] = deque()
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        """The API root, as a catalog entry or a custom base URL names it."""
        assert self._server is not None, "server not started"
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def answer(self, body: Any, status: int = 200) -> None:
        self._answers.append((status, body))

    def chat_requests(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["method"] == "POST"]

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # keep test output clean
                pass

            def _reply(self, status: int, body: Any) -> None:
                payload = json.dumps(body).encode() if not isinstance(body, bytes) else body
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                fake.requests.append({"method": "GET", "path": self.path, "headers": dict(self.headers)})
                if self.path.endswith(("/models", "/key")):
                    self._reply(fake.get_status, {"object": "list", "data": [{"id": "m"}]})
                else:
                    self._reply(404, {"error": {"message": "not found"}})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                fake.requests.append(
                    {"method": "POST", "path": self.path, "headers": dict(self.headers), "json": json.loads(raw)}
                )
                if not self.path.endswith("/chat/completions"):
                    self._reply(404, {"error": {"message": "not found"}})
                    return
                status, body = fake._answers.popleft() if fake._answers else (500, {"error": {"message": "no answer"}})
                self._reply(status, body)

        return Handler

    @contextmanager
    def running(self) -> Iterator[FakeOpenAIServer]:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield self
        finally:
            self._server.shutdown()
            self._server.server_close()
            thread.join(timeout=5)


def completion(
    *,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    cached_tokens: int = 0,
    **message_fields: Any,
) -> dict[str, Any]:
    """A chat-completions response body with one choice."""
    message: dict[str, Any] = {"role": "assistant", "content": content, **message_fields}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    usage: dict[str, Any] = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    if cached_tokens:
        usage["prompt_tokens_details"] = {"cached_tokens": cached_tokens}
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": usage,
    }


def tool_call(call_id: str, name: str, arguments: Any) -> dict[str, Any]:
    """One ``tool_calls`` entry; a dict ``arguments`` is sent JSON-encoded, as the API does."""
    encoded = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": encoded}}
