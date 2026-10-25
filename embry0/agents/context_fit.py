"""Keeping a chat-completions conversation inside a model's context window.

A model on the operator's own hardware often has a small window (tens of
thousands of tokens), and an agent's tool loop grows by every tool result it
reads. Sent over the window, the server refuses the request, and the loop
used to end there with the server's words as the only explanation. Before
each request whose window is known, the wire fits the conversation:

- it estimates the request's tokens from its characters, at a ratio the
  server's own ``prompt_tokens`` keeps calibrated;
- over the budget (the window less room for the answer), it elides the
  oldest tool results first, keeping the task and the latest exchange (the
  last assistant turn and the results it asked for) whole;
- if the conversation still does not fit, the run fails with
  ``ERR_CONTEXT_WINDOW_EXCEEDED`` and the sizes, instead of a request the
  server can only refuse.

Pure apart from the copies it makes, so the wire and the tests share it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

#: Tokens per character before the server has answered once: about 3.3
#: characters a token, on the high side for code and logs, so the first
#: estimate errs toward fitting.
DEFAULT_TOKENS_PER_CHAR = 0.3


class ContextWindowExceeded(RuntimeError):
    """The conversation does not fit the model's context window, even with old tool output elided."""

    def __init__(self, *, model: str, window: int, estimate: int) -> None:
        super().__init__(
            f"the conversation needs about {estimate} tokens, over the {window}-token context window of "
            f"{model} even with earlier tool output elided"
        )
        self.window = window
        self.estimate = estimate


def answer_reserve(window: int) -> int:
    """Tokens kept free for the model's answer: an eighth of the window, between 1,024 and 8,192."""
    return max(1024, min(8192, window // 8))


def request_chars(system: str, tools: Sequence[Any], messages: Sequence[Any]) -> int:
    """The characters a request carries, as the estimate counts them."""
    return len(system) + len(json.dumps(list(tools))) + len(json.dumps(list(messages)))


def _elided(message: dict[str, Any], window: int) -> dict[str, Any]:
    content = message.get("content")
    size = len(content) if isinstance(content, str) else len(json.dumps(content))
    return {
        **message,
        "content": f"[earlier tool output elided to fit the {window}-token context window: {size} characters]",
    }


def fit_messages(
    *,
    system: str,
    tools: Sequence[Any],
    messages: list[dict[str, Any]],
    window: int,
    model: str,
    tokens_per_char: float = DEFAULT_TOKENS_PER_CHAR,
) -> list[dict[str, Any]]:
    """``messages`` as they fit ``window``: unchanged when they do, else with old tool results elided.

    The first message (the task) and the latest exchange, from the last
    assistant turn on, are kept whole: the model is in the middle of it.
    Raises :class:`ContextWindowExceeded` when eliding every older tool result
    is not enough.
    """
    budget = window - answer_reserve(window)

    def estimate(msgs: Sequence[Any]) -> int:
        return int(request_chars(system, tools, msgs) * tokens_per_char)

    if estimate(messages) <= budget:
        return messages
    fitted = list(messages)
    latest = max((i for i, m in enumerate(fitted) if m.get("role") == "assistant"), default=len(fitted))
    for index in range(1, latest):
        message = fitted[index]
        if message.get("role") != "tool" or str(message.get("content", "")).startswith("[earlier tool output elided"):
            continue
        fitted[index] = _elided(message, window)
        if estimate(fitted) <= budget:
            return fitted
    final = estimate(fitted)
    if final <= budget:
        return fitted
    raise ContextWindowExceeded(model=model, window=window, estimate=final)


def calibrated_ratio(prompt_tokens: int, chars: int, previous: float) -> float:
    """Tokens per character from the server's own count of a request, or ``previous`` without one.

    Kept at or above the default's half so one odd count cannot make the
    estimate wildly optimistic.
    """
    if prompt_tokens <= 0 or chars <= 0:
        return previous
    return max(prompt_tokens / chars, DEFAULT_TOKENS_PER_CHAR / 2)


def window_from_models(listing: Any, model: str) -> int | None:
    """The context window ``GET /models`` reports for ``model``, or None.

    vLLM says ``max_model_len``; other OpenAI-compatible servers say
    ``context_length`` or ``context_window``.
    """
    data = listing.get("data") if isinstance(listing, dict) else None
    if not isinstance(data, list):
        return None
    for entry in data:
        if not isinstance(entry, dict) or entry.get("id") != model:
            continue
        for key in ("max_model_len", "context_length", "context_window"):
            value = entry.get(key)
            if isinstance(value, int) and value > 0:
                return value
    return None
