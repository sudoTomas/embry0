"""plain_errors: a refused test setup explained in sentences, not parser output."""

from __future__ import annotations

import pytest

from embry0.workflows.qa.qa_yaml_errors import plain_errors
from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

_HEAD = "version: 2\nworkspace_provider:\n  type: npm-workspaces-turbo\n"


def _errors(text: str) -> list[str]:
    with pytest.raises(Exception) as caught:
        parse_qa_yaml_v2(text)
    return plain_errors(caught.value)


def test_a_misspelt_setting_names_the_closest_real_one():
    """The measured typo from the dashboard editor."""
    text = _HEAD + "apps:\n  web:\n    boot_comand: npm run dev\n    frontend_url: http://localhost:3000\n"
    assert "'boot_comand' is not a setting embry0 knows. Did you mean 'boot_command'? (app web)" in _errors(text)


def test_an_unknown_setting_with_no_close_match_drops_the_suggestion():
    text = _HEAD + "defaults:\n  zzzzzz: 1\n"
    assert _errors(text) == ["'zzzzzz' is not a setting embry0 knows. (defaults)"]


def test_an_unknown_top_level_setting_has_no_location():
    assert _errors(_HEAD + "qa_requird: never\n") == [
        "'qa_requird' is not a setting embry0 knows. Did you mean 'qa_required'?"
    ]


def test_an_address_without_a_scheme_asks_for_one():
    text = (
        _HEAD + "apps:\n  web:\n    boot_command: npm run dev\n    frontend_url: localhost:3000\n"
        "    ready_checks:\n      - http: localhost:3000\n"
    )
    assert _errors(text) == [
        "Start the address with http:// or https:// (app web)",
        "Start the address with http:// or https:// (app web, ready_checks 1)",
    ]


def test_a_missing_setting_is_named():
    text = _HEAD + "apps:\n  web:\n    boot_command: npm run dev\n"
    assert _errors(text) == ["'frontend_url' is missing (app web)"]


def test_a_tab_indent_names_its_line():
    text = "version: 2\napps:\n\tweb:\n    boot_command: x\n"
    assert _errors(text) == ["Line 3 starts with a tab. Indent with spaces."]


def test_a_tab_inside_a_line_is_not_called_an_indent():
    assert _errors("version: 2\nqa_required:\tnever\n") == ["The text could not be read near line 2."]


def test_other_unreadable_text_names_its_line():
    assert _errors("version: 2\napps: [\n") == ["The text could not be read near line 3."]


def test_any_other_refusal_keeps_the_message_and_says_where():
    text = _HEAD + "qa_required: sometimes\napps:\n  web:\n    frontend_url: http://localhost:3000\n"
    assert _errors(text) == [
        "Input should be 'auto', 'always' or 'never' (qa_required)",
        "boot_command is required for target: managed apps (app web)",
    ]


def test_an_error_that_is_not_from_the_schema_keeps_its_message():
    assert plain_errors(ValueError("qa.yaml must be a YAML mapping at the top level")) == [
        "qa.yaml must be a YAML mapping at the top level"
    ]
