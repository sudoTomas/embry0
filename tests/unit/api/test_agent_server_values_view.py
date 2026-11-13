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
            # Named like a credential (``auth``), so all of it.
            "oauth": {"client_id": MASK, "private_key": MASK},
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


#: The other ways a server's definition commonly holds a credential, each in a value that must be masked.
COMMON: dict[str, Any] = {
    "cli": {
        "command": "tool-mcp",
        "args": [
            "--header",
            "Authorization: Bearer value-000000000021",
            "--header=X-Api-Key: value-000000000022",
            "-H",
            "Cookie: sid=value-000000000023",
            "--auth",
            "ops:value-000000000024",
            "--bearer=value-000000000025",
            "-p",
            "value-000000000026",
            "--pwd",
            "value-000000000027",
            "https://tool.example.test/api?auth=value-000000000028&page=2",
        ],
        "ENV": {"PORT_SECRET_NUMBER": 1234, "ENABLED": True, "BLANK": ""},
        "Headers": {"X-Trace": "value-000000000029"},
    },
    "web": {
        "type": "http",
        "url": "https://tool.example.test/mcp?session_id=value-000000000030",
        "Authorization": "Bearer value-000000000031",
        "cookie": "sid=value-000000000032",
    },
}


def test_the_common_ways_of_writing_a_credential_are_masked() -> None:
    shown = masked_servers(COMMON)
    named = [s for s in _strings(shown) if "value-0000" in s]
    assert named == [], f"credentials a reader is shown: {named}"
    assert shown["cli"]["ENV"] == {"PORT_SECRET_NUMBER": MASK, "ENABLED": MASK, "BLANK": ""}, shown["cli"]
    assert shown["cli"]["args"][:4] == ["--header", MASK, f"--header={MASK}", "-H"], shown["cli"]["args"]
    assert shown["cli"]["args"][-1] == f"https://tool.example.test/api?auth={MASK}&page=2", shown["cli"]["args"]
    assert restore_unchanged(shown, COMMON) == COMMON, "the masked view sent back changes nothing, numbers included"


def test_a_masked_item_moved_in_a_list_is_refused() -> None:
    """A list is restored only while its unmasked items stand where they stood, so a masked value never moves to a
    place where it would read in clear."""
    sent = masked_servers(COMMON)
    sent["cli"]["args"] = ["--verbose", *sent["cli"]["args"][1:]]
    with pytest.raises(MaskedValueWithoutStored) as raised:
        restore_unchanged(sent, COMMON)
    assert raised.value.path == "mcp_servers.cli.args[1]"


def test_a_value_that_holds_the_mask_among_other_characters_is_stored_as_sent() -> None:
    sent = {"tracker": {"command": "npx", "env": {"NOTE": f"stars {MASK} in it"}}}
    assert restore_unchanged(sent, STORED) == sent


#: Credentials inside a JSON object or array passed as a string: an argument, a flag's value, a key of its own.
IN_JSON: dict[str, Any] = {
    "cfg": {
        "command": "cfg-mcp",
        "args": [
            "--config",
            json.dumps({"token": "value-000000000041", "region": "eu", "nested": {"api_key": "value-000000000042"}}),
            "--settings=" + json.dumps({"password": "value-000000000043", "retries": 3}),
            json.dumps([{"url": "https://ops:value-000000000044@cfg.example.test/"}, "plain"]),
            '{"region":"eu",  "retries":3}',
            "{not json",
        ],
        "options": json.dumps({"headers": {"X-Api-Key": "value-000000000045"}, "timeout": 5}),
    },
}


def test_a_credential_inside_a_json_string_is_masked() -> None:
    shown = masked_servers(IN_JSON)
    named = [s for s in _strings(shown) if "value-0000" in s]
    assert named == [], f"credentials a reader is shown inside JSON strings: {named}"
    args = shown["cfg"]["args"]
    assert json.loads(args[1]) == {"token": MASK, "region": "eu", "nested": {"api_key": MASK}}, args[1]
    assert args[2] == "--settings=" + json.dumps({"password": MASK, "retries": 3}), args[2]
    assert args[4:] == IN_JSON["cfg"]["args"][4:], "a JSON string with no credential in it, and text, read as stored"
    assert json.loads(shown["cfg"]["options"]) == {"headers": {"X-Api-Key": MASK}, "timeout": 5}
    assert restore_unchanged(shown, IN_JSON) == IN_JSON, "the masked view sent back keeps every stored string"


@pytest.mark.parametrize(
    ("edit", "where"),
    [
        (lambda s: s["remote"].update(url=s["remote"]["url"].replace("team=widgets", "team=gadgets")), "url"),
        (lambda s: s["tracker"]["args"].__setitem__(7, s["tracker"]["args"][7] + "&page=2"), "args[7]"),
    ],
)
def test_a_masked_value_edited_around_its_mask_is_refused(edit: Any, where: str) -> None:
    """What the mask stood for cannot be known, so a value edited from a masked one that still holds the mask would
    store the mask itself in place of the credential."""
    sent = copy.deepcopy(masked_servers(STORED))
    edit(sent)
    with pytest.raises(MaskedValueWithoutStored) as raised:
        restore_unchanged(sent, STORED)
    assert raised.value.edited and raised.value.path.endswith(where), raised.value.path
