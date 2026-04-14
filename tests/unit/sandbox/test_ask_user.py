"""Tests for the sandbox ask_user helper."""

from __future__ import annotations

import json


def test_ask_user_emits_event(capsys):
    from legion.sandbox.ask_user import ask_user

    ask_user("Which database?", category="design", options=["postgres", "mysql"])

    captured = capsys.readouterr()
    assert "agent_ask_user" in captured.out
    assert "Which database?" in captured.out
    assert "postgres" in captured.out

    # Sanity check that it parses as JSON with expected keys
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert payload["type"] == "agent_ask_user"
    assert payload["question"] == "Which database?"
    assert payload["category"] == "design"
    assert payload["options"] == ["postgres", "mysql"]
