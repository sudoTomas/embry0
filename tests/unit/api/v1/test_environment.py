"""Unit tests for environment parse helper."""

from legion.api.v1.environment import _parse_env_file


def test_parse_env_file_basic():
    parsed = _parse_env_file("# API key for X\nAPI_KEY=abc123\nMODE=prod\n")
    assert parsed == [
        {
            "key": "API_KEY",
            "default_value": "abc123",
            "description": "API key for X",
            "suggested_type": "secret",
        },
        {
            "key": "MODE",
            "default_value": "prod",
            "description": "",
            "suggested_type": "config",
        },
    ]


def test_parse_env_file_classifies_secrets():
    parsed = _parse_env_file("GITHUB_TOKEN=\nDATABASE_PASSWORD=\nPORT=8080\n")
    types = {v["key"]: v["suggested_type"] for v in parsed}
    assert types == {
        "GITHUB_TOKEN": "secret",
        "DATABASE_PASSWORD": "secret",
        "PORT": "config",
    }


def test_parse_env_file_ignores_blank_separated_comments():
    content = "# orphan comment\n\nMODE=value\n"
    parsed = _parse_env_file(content)
    assert parsed == [{"key": "MODE", "default_value": "value", "description": "", "suggested_type": "config"}]


def test_parse_env_file_strips_quotes():
    parsed = _parse_env_file("NAME=\"hello world\"\nOTHER='quoted'\n")
    values = {v["key"]: v["default_value"] for v in parsed}
    assert values == {"NAME": "hello world", "OTHER": "quoted"}


def test_parse_env_file_empty_value_is_none():
    parsed = _parse_env_file("EMPTY=\n")
    assert parsed[0]["default_value"] is None
