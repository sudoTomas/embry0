"""An agent's MCP server definitions as a reader sees them: the values that may be credentials masked.

An operator gives an agent's MCP servers what they need to reach their
service, and that is often a credential: a stdio server's ``env``
(``API_TOKEN``), a remote server's ``headers`` (``Authorization``), a key of
its own named like one, a URL with a password or a token in it, a command
line flag such as ``--api-key``. Every caller that reads the agents reads the
definitions, so :func:`masked_servers` gives them with those values masked:

- every value under ``env`` and ``headers``;
- every value under a key whose name looks secret
  (:data:`~embry0.config_redaction.SECRET_NAME_PATTERN`), the server's own
  keys and those nested in it;
- in every other string, a URL's userinfo and its secret-looking query
  parameters (:func:`~embry0.config_redaction.mask_url_credentials`);
- in ``args``, the value of a flag named like a secret, written
  ``--api-key=VALUE`` or as the argument after ``--api-key``.

The shape is kept: every key and every list item is there, a masked string
reads :data:`~embry0.config_redaction.MASK` (or its URL with the credentials
masked), an empty string stays empty, and any other value stays as it is. So
an edit form shows what a server has and can send it back:
:func:`restore_unchanged` reads a value that is exactly what the reader was
shown for the stored one as unchanged, and puts the stored value back.

Pure: ``embry0.api.v1.agents`` decides who reads the masked view. It is not in ``embry0/agents/``, which the
sandbox images copy: nothing that runs in a sandbox reads it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from embry0.config_redaction import MASK, SECRET_NAME_PATTERN, mask_url_credentials

#: The keys of a server whose every value is masked, whatever it is called.
MASKED_SECTIONS = frozenset({"env", "headers"})

#: A command line flag named like a secret: ``--api-key``, ``-token``, ``--password=…``.
_SECRET_FLAG = re.compile(
    r"(-{1,2}[^=\s]*(?:" + SECRET_NAME_PATTERN.pattern + r")[^=\s]*)(=.*)?", re.IGNORECASE | re.DOTALL
)


class MaskedValueWithoutStored(ValueError):
    """A masked value was sent for a value nothing is stored for: there is nothing to keep."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


def _mask_all(value: Any) -> Any:
    """``value`` with every non-empty string in it masked, its shape kept."""
    if isinstance(value, str):
        return MASK if value else value
    if isinstance(value, Mapping):
        return {key: _mask_all(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_all(item) for item in value]
    return value


def _mask_args(args: list[Any]) -> list[Any]:
    """``args`` with the value of each flag named like a secret masked, and URL credentials in the others."""
    out: list[Any] = []
    after_flag = False
    for arg in args:
        if not isinstance(arg, str):
            out.append(_masked(arg))
            after_flag = False
            continue
        if after_flag and arg and not arg.startswith("-"):
            out.append(MASK)
            after_flag = False
            continue
        flag = _SECRET_FLAG.fullmatch(arg)
        if flag is not None and flag.group(2):
            out.append(f"{flag.group(1)}={MASK}" if len(flag.group(2)) > 1 else arg)
            after_flag = False
        else:
            out.append(mask_url_credentials(arg))
            after_flag = flag is not None
    return out


def _masked(value: Any, key: str | None = None) -> Any:
    """``value``, found under ``key``, as a reader is shown it."""
    if key is not None and (key in MASKED_SECTIONS or SECRET_NAME_PATTERN.search(key)):
        return _mask_all(value)
    if key == "args" and isinstance(value, list):
        return _mask_args(value)
    if isinstance(value, str):
        return mask_url_credentials(value)
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
    :func:`masked_servers` shows of ``stored`` there read as unchanged: the stored value put back. Any other value
    that holds a mask (:data:`MASK`), where nothing is stored or what is stored differs, is
    :class:`MaskedValueWithoutStored` naming where: the value it stood for cannot be known.
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
        stored_list = stored if isinstance(stored, list) else []
        shown_list = shown if isinstance(shown, list) else []
        return [
            _restore(
                item,
                stored_list[n] if n < len(stored_list) else None,
                shown_list[n] if n < len(shown_list) else None,
                f"{path}[{n}]",
            )
            for n, item in enumerate(sent)
        ]
    if isinstance(sent, str) and isinstance(stored, str) and sent == shown and shown != stored:
        return stored
    if isinstance(sent, str) and MASK in sent and sent != stored:
        raise MaskedValueWithoutStored(path)
    return sent
