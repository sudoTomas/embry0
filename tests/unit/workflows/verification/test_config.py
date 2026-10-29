"""Stored verification config (#298, #299): strict on write, harmless on read."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from embry0.workflows.verification.config import COMMAND_MAX_CHARS, VerificationConfig, load_verification_config


def test_defaults() -> None:
    config = VerificationConfig.model_validate({"test_targets": [{"name": "py", "command": "pytest -q"}]})
    target = config.test_targets[0]
    assert (target.paths, target.timeout_seconds) == ([], 1200)
    assert (config.base_run, config.head_reruns, config.total_timeout_seconds) == ("always", 1, 3600)
    assert config.tiers.frontend_paths is None


def test_an_over_long_command_is_refused_not_truncated() -> None:
    with pytest.raises(ValidationError):
        VerificationConfig.model_validate({"test_targets": [{"name": "py", "command": "x" * (COMMAND_MAX_CHARS + 1)}]})


@pytest.mark.parametrize("raw", [None, {}])
def test_nothing_stored_is_not_an_error(raw: object) -> None:
    assert load_verification_config(raw) == (None, None)


@pytest.mark.parametrize(
    "raw",
    [[], "x", {"test_targets": "nope"}, {"test_targets": [{"name": "a b", "command": "t"}]}, {"surprise": 1}],
)
def test_an_unusable_stored_value_never_raises(raw: object) -> None:
    config, problem = load_verification_config(raw)
    assert config is None
    assert problem and "verification config" in problem


def test_a_target_name_ending_in_a_newline_is_refused() -> None:
    """The name becomes a log file name, and Python's ``$`` also matches before a final newline."""
    try:
        VerificationConfig.model_validate({"test_targets": [{"name": "py\n", "command": "pytest -q"}]})
    except ValidationError as exc:
        assert "name must match" in str(exc), f"the refusal must say what a name is: {exc}"
    else:
        raise AssertionError("a target name ending in a newline must be refused")
