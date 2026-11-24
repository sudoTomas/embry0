"""plain_errors: a refused test setup explained in sentences, not parser output."""

from __future__ import annotations

from typing import Any

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


# ---- answered_errors: what the API answers a refused write with ------------------------------------------------


def _answering() -> Any:
    from embry0.workflows.qa import qa_yaml_errors

    answer = getattr(qa_yaml_errors, "answered_errors", None)
    assert answer is not None, "the API answers a refused setup with its own sentences, not the repair prompts'"
    return answer


def _answered(text: str) -> list[str]:
    with pytest.raises(Exception) as caught:
        parse_qa_yaml_v2(text, writing=True)
    return _answering()(caught.value, text)


def test_the_repair_sentences_still_name_what_the_setup_holds():
    """The agent that repairs a setup holds the file; its sentences name the app and the key, as before."""
    text = _HEAD + "apps:\n  web:\n    boot_comand: npm run dev\n    frontend_url: http://localhost:3000\n"
    assert _errors(text) == ["'boot_comand' is not a setting embry0 knows. Did you mean 'boot_command'? (app web)"]


def test_an_answer_says_an_app_a_package_and_an_app_local_entry_by_position():
    text = (
        _HEAD + "packages:\n  one:\n    path: a\n  zz-two:\n    pathh: b\n"
        "apps:\n  web:\n    boot_command: x\n    frontend_url: http://localhost:3000\n"
        "  zz-api:\n    boot_command: x\n    frontend_url: http://localhost:3001\n"
        "    ready_checks:\n      - http: http://localhost:3001\n      - http: zz-host\n"
        "app_local:\n  web:\n    zz-key: 1\n"
    )
    answered = _answered(text)
    assert not any("zz-" in sentence for sentence in answered), answered
    assert any(sentence.endswith("(package 2)") for sentence in answered), answered
    assert "Start the address with http:// or https:// (app 2, ready_checks 2)" in answered, answered
    assert "Not a setting embry0 knows. (app_local entry 1)" in answered, answered


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("a '/' and '..' and '**', in glob 'zz-pattern'", "a '/' and '..' and '**', in glob '…'"),
        ('frontend_url "zz-it\'s" points at', "frontend_url '…' points at"),
        ("unknown apps: ['zz-a', 'zz-b']", "unknown apps: ['…', '…']"),
        ("written \\\\ in 'zz-x'", "written \\\\ in '…'"),
    ],
)
def test_a_check_s_message_keeps_its_own_words_and_says_what_it_quotes_as_an_ellipsis(text: str, said: str):
    from embry0.workflows.qa import qa_yaml_errors

    masked = getattr(qa_yaml_errors, "_masked", None)
    assert masked is not None, "a check's message is answered with what it quotes said as an ellipsis"
    assert masked(text) == said


def test_the_schema_s_own_options_stay_named():
    """pydantic's own messages say what the schema takes ('auto', 'always' or 'never'), never the input."""
    text = _HEAD + "qa_required: zz-sometimes\n"
    assert _answered(text) == ["Input should be 'auto', 'always' or 'never' (qa_required)"]


def test_text_that_is_not_a_setup_is_answered_without_repeating_it():
    assert _answering()(ValueError("a 'zz-name' went wrong"), "") == ["a '…' went wrong"]
    assert _answered("- zz-item\n") == ["qa.yaml must be a YAML mapping at the top level"]
