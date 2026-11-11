"""An agent's MCP server values as a reader sees them, and as a writer sends them back (embry0.api.agent_server_values)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from embry0.api.agent_server_values import MaskedValueWithoutStored, masked_servers, restore_unchanged

MASK = "****"

#: Servers holding a credential in each place one is found, and values that are none.
STORED: dict[str, Any] = {
    "tracker": {
        "type": "stdio",
        "command": "npx",
        "args": [
            "-y",
            "tracker-mcp",
            "--api-key",
            "value-000000000001",
            "--token=value-000000000002",
            "--port",
            "8080",
            "https://ops:value-000000000003@tracker.example.test/api?access_token=value-000000000004&page=1",
        ],
        "env": {"TRACKER_TOKEN": "value-000000000005", "LOG_LEVEL": "debug", "EMPTY": ""},
    },
    "remote": {
        "type": "http",
        "url": "https://mcp.example.test/v1?api_key=value-000000000006&team=widgets",
        "headers": {"Authorization": "Bearer value-000000000007"},
        "client_secret": "value-000000000008",
        "timeout": 30,
        "oauth": {"client_id": "widgets-app", "private_key": "value-000000000009"},
    },
}


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def test_every_credential_is_masked_and_the_shape_is_kept() -> None:
    shown = masked_servers(STORED)
    named = [s for s in _strings(shown) if "value-0000" in s]
    assert named == [], f"credentials a reader is shown: {named}"
    assert shown == {
        "tracker": {
            "type": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "tracker-mcp",
                "--api-key",
                MASK,
                f"--token={MASK}",
                "--port",
                "8080",
                f"https://{MASK}@tracker.example.test/api?access_token={MASK}&page=1",
            ],
            "env": {"TRACKER_TOKEN": MASK, "LOG_LEVEL": MASK, "EMPTY": ""},
        },
        "remote": {
            "type": "http",
            "url": f"https://mcp.example.test/v1?api_key={MASK}&team=widgets",
            "headers": {"Authorization": MASK},
            "client_secret": MASK,
            "timeout": 30,
            "oauth": {"client_id": "widgets-app", "private_key": MASK},
        },
    }, json.dumps(shown, indent=1)


def test_what_a_writer_sends_back_as_shown_keeps_the_stored_values() -> None:
    sent = masked_servers(STORED)
    assert restore_unchanged(sent, STORED) == STORED, "the masked view sent back as it is changes nothing"
    edited = copy.deepcopy(sent)
    edited["remote"]["headers"]["Authorization"] = "Bearer value-000000000010"
    edited["tracker"]["env"]["LOG_LEVEL"] = "info"
    kept = restore_unchanged(edited, STORED)
    assert kept["remote"]["headers"] == {"Authorization": "Bearer value-000000000010"}, kept["remote"]
    assert kept["tracker"]["env"] == {"TRACKER_TOKEN": "value-000000000005", "LOG_LEVEL": "info", "EMPTY": ""}
    assert kept["tracker"]["args"] == STORED["tracker"]["args"], "the masked flags and URL keep their values"


@pytest.mark.parametrize(
    ("sent", "where"),
    [
        ({"fresh": {"env": {"TOKEN": MASK}}}, "mcp_servers.fresh.env.TOKEN"),
        ({"remote": {"url": f"https://{MASK}@other.example.test/"}}, "mcp_servers.remote.url"),
        ({"tracker": {"args": ["--api-key", MASK]}}, "mcp_servers.tracker.args[1]"),
    ],
)
def test_a_mask_with_no_stored_value_behind_it_is_refused(sent: dict[str, Any], where: str) -> None:
    with pytest.raises(MaskedValueWithoutStored) as raised:
        restore_unchanged(sent, STORED)
    assert raised.value.path == where


def test_values_that_are_not_masked_pass_as_they_are() -> None:
    plain = {"files": {"type": "stdio", "command": "files-mcp", "args": ["--root", "/srv"], "timeout": 5}}
    assert masked_servers(plain) == plain, "nothing in it may be a credential"
    assert restore_unchanged(plain, {}) == plain
    assert masked_servers(None) is None and masked_servers([]) == []
