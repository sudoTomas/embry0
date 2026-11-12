"""An agent's MCP server definitions as a reader sees them: the values that may be credentials masked.

An operator gives an agent's MCP servers what they need to reach their
service, and that is often a credential: a stdio server's ``env``
(``API_TOKEN``), a remote server's ``headers`` (``Authorization``), a key of
its own named like one, a URL with a password or a token in it, a command line
flag such as ``--api-key`` or ``--header "Authorization: …"``. Every caller
that reads the agents reads the definitions, so :func:`masked_servers` gives
them with those values masked:

- every value under ``env`` and ``headers``, whatever case they are written
  in, numbers and booleans included;
- every value under a key whose name looks like a credential
  (:data:`SECRET_NAME`: ``auth``, ``bearer``, ``cookie``, ``session``,
  ``pass``, ``pwd``, ``private``, ``secret``, ``token``, ``key``,
  ``credential``, ``dsn``, in any case), the server's own keys and those
  nested in it;
- in every other string, a URL's userinfo and each query parameter named like
  a credential, and in a string that holds a JSON object or array
  (``--config '{"token": "…"}'``), the values these rules mask in it, the
  string written out again;
- in ``args``, the value of a header flag (``--header``, ``-H``) and of a flag
  named like a credential or ``-p``, written ``--flag=VALUE`` or as the
  argument after the flag.

What cannot be told from its place is not masked: a token passed as a bare
positional argument, or one in a URL's path. Keep such a credential in
``env`` or ``headers``.

The shape is kept: every key and every list item is there, a masked value
reads :data:`~embry0.config_redaction.MASK` (or its URL with the credentials
masked), and an empty string stays empty. So an edit form shows what a server
has and can send it back: :func:`restore_unchanged` reads a value that is
exactly what the reader was shown for the stored one, at the same key, as
unchanged, and puts the stored value back. A value edited from a masked one
that still holds the mask cannot be stored: the masked part stood for
something the writer cannot know (:class:`MaskedValueWithoutStored`).

Pure: ``embry0.api.v1.agents`` decides who reads the masked view. It is not in
``embry0/agents/``, which the sandbox images copy: nothing that runs in a
sandbox reads it, and a job's agent is given the servers as stored.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from embry0.config_redaction import MASK, mask_url_credentials

#: The keys of a server whose every value is masked, whatever it is called (compared in lower case).
MASKED_SECTIONS = frozenset({"env", "headers"})
#: The fragments of a name that make its value a credential: substrings, in any case.
_SECRET_PARTS = (
    "auth",
    "bearer",
    "cookie",
    "session",
    "pass",
    "pwd",
    "private",
    "secret",
    "token",
    "key",
    "credential",
    "dsn",
)
SECRET_NAME = re.compile("|".join(_SECRET_PARTS), re.IGNORECASE)
#: Flags whose value is masked whatever their name says: a header, and the ``-p`` many tools read a password from.
_VALUE_FLAGS = frozenset({"--header", "-h", "-p"})
#: A command line flag, with its value when written ``--flag=VALUE``.
_FLAG = re.compile(r"(-{1,2}[^=\s]+)(=.*)?", re.DOTALL)
#: A URL's query parameter named like a credential: ``?auth=…``, ``&session_id=…``.
_SECRET_QUERY = re.compile(r"([?&](?:[^=&#\s]*(?:" + SECRET_NAME.pattern + r")[^=&#\s]*)=)[^&#\s]+", re.IGNORECASE)


class MaskedValueWithoutStored(ValueError):
    """A masked value was sent where no stored value stands behind it, or a masked value edited and still holding
    the mask: what the mask stands for cannot be known, so there is nothing to keep."""

    def __init__(self, path: str, *, edited: bool = False) -> None:
        super().__init__(path)
        self.path = path
        #: True when the value was edited from a masked one and still holds the mask.
        self.edited = edited


def _mask_all(value: Any) -> Any:
    """``value`` with every scalar in it masked, but an empty string and a null, its shape kept."""
    if isinstance(value, Mapping):
        return {key: _mask_all(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_all(item) for item in value]
    if value is None or value == "":
        return value
    return MASK


def _secret_flag(flag: str) -> bool:
    return flag.lower() in _VALUE_FLAGS or bool(SECRET_NAME.search(flag))


def _json_container(text: str) -> Any:
    """The JSON object or array ``text`` holds, or None."""
    if not text.lstrip().startswith(("{", "[")):
        return None
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict | list) else None


def _mask_text(text: str) -> str:
    """``text`` with a URL's userinfo and its query parameters named like a credential masked; when it holds a JSON
    object or array, the values masked in it as in a server's own (:func:`_masked`), written out again."""
    parsed = _json_container(text)
    if parsed is not None:
        masked = _masked(parsed)
        return text if masked == parsed else json.dumps(masked, ensure_ascii=False)
    return _SECRET_QUERY.sub(rf"\g<1>{MASK}", mask_url_credentials(text))


def _mask_args(args: list[Any]) -> list[Any]:
    """``args`` with the value of each header flag and each flag named like a credential masked, and URL credentials
    in the others."""
    out: list[Any] = []
    after_flag = False
    for arg in args:
        if not isinstance(arg, str):
            out.append(MASK if after_flag and arg is not None else _masked(arg))
            after_flag = False
            continue
        if after_flag and arg and not arg.startswith("-"):
            out.append(MASK)
            after_flag = False
            continue
        flag = _FLAG.fullmatch(arg) if arg.startswith("-") else None
        if flag is not None and _secret_flag(flag.group(1)):
            if flag.group(2):
                out.append(f"{flag.group(1)}={MASK}" if len(flag.group(2)) > 1 else arg)
                after_flag = False
            else:
                out.append(arg)
                after_flag = True
            continue
        if flag is not None and flag.group(2):
            # ``--config={"token": …}``: the value read as any other string.
            out.append(f"{flag.group(1)}={_mask_text(flag.group(2)[1:])}")
            after_flag = False
            continue
        out.append(_mask_text(arg))
        after_flag = False
    return out


def _masked(value: Any, key: str | None = None) -> Any:
    """``value``, found under ``key``, as a reader is shown it."""
    if key is not None and (key.lower() in MASKED_SECTIONS or SECRET_NAME.search(key)):
        return _mask_all(value)
    if key is not None and key.lower() == "args" and isinstance(value, list):
        return _mask_args(value)
    if isinstance(value, str):
        return _mask_text(value)
    if isinstance(value, Mapping):
        return {name: _masked(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_masked(item) for item in value]
    return value


def masked_servers(servers: Any) -> Any:
    """An agent's ``mcp_servers`` with every value that may be a credential masked, the shape kept."""
    if not isinstance(servers, Mapping):
        return servers
    return {name: _masked(server) for name, server in servers.items()}


def restore_unchanged(sent: Any, stored: Any) -> Any:
    """``sent``, an agent's ``mcp_servers`` as a writer sends them, with each value that is exactly what
    :func:`masked_servers` shows of ``stored`` at the same key read as unchanged: the stored value put back.

    A list's items are restored only while the list is the stored one as shown, item for item but the masked ones,
    so a masked value never moves to a place where it reads in clear. Any other value that is exactly
    :data:`MASK`, and a value edited from one shown masked that still holds the mask, is
    :class:`MaskedValueWithoutStored` naming where: what the mask stood for cannot be known. Any other value that
    holds the mask among other characters is stored as sent.
    """
    return _restore(sent, stored, masked_servers(stored), "mcp_servers")


def _restore(sent: Any, stored: Any, shown: Any, path: str) -> Any:
    if isinstance(sent, Mapping):
        stored_map = stored if isinstance(stored, Mapping) else {}
        shown_map = shown if isinstance(shown, Mapping) else {}
        return {
            key: _restore(item, stored_map.get(key), shown_map.get(key), f"{path}.{key}") for key, item in sent.items()
        }
    if isinstance(sent, list):
        # The list as the reader was shown it, but for its masked items: only then does a masked item stand where it
        # stood, behind the same flag.
        same = (
            isinstance(shown, list)
            and isinstance(stored, list)
            and len(sent) == len(shown) == len(stored)
            and all(item == shown[n] for n, item in enumerate(sent) if not _holds_mask(shown[n]))
        )
        return [
            _restore(item, stored[n] if same else None, shown[n] if same else None, f"{path}[{n}]")
            for n, item in enumerate(sent)
        ]
    masked_here = stored is not None and shown != stored and _holds_mask(shown)
    if masked_here and sent == shown:
        return stored
    if sent == MASK:
        raise MaskedValueWithoutStored(path)
    if masked_here and _holds_mask(sent):
        raise MaskedValueWithoutStored(path, edited=True)
    return sent


def _holds_mask(value: Any) -> bool:
    return isinstance(value, str) and MASK in value
