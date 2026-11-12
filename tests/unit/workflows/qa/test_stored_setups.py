"""A stored test setup and a stored verification config holding a glob the release before read keep reading
(#458's second review).

The release before read a class ending in a backslash (``[\\]``) as one class
with whatever followed it in its regex. This release refuses such a glob when
it is written (``test_stored_globs.py``); one stored before must read after the
upgrade, and match what it matched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from embry0.workflows.qa.conditional_criteria import evaluate_conditional_criteria
from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2
from embry0.workflows.verification.config import load_verification_config
from embry0.workflows.verification.runner import select_targets

#: main read ``docs/[\]*`` as ``docs/`` then any run of ``]``, ``[``, ``^`` and ``/``: its class took in ``[^/]``.
SETUP = (Path(__file__).parents[3] / "fixtures" / "qa-yaml-corpus" / "v2" / "conditional-criteria.yaml").read_text()
SETUP += """  - name: docs
    when:
      changed_paths: ['docs/[\\]*']
    criteria:
      - "Open the docs site"
"""


def test_a_stored_setup_holding_one_still_reads_and_its_criteria_fire_as_before() -> None:
    try:
        cfg = parse_qa_yaml_v2(SETUP)
    except ValueError as exc:
        pytest.fail(f"a stored setup with a glob main read no longer reads: {exc}")
    fired = [
        evaluate_conditional_criteria(cfg, changed_files=[path], apps_to_qa=["quoting"], affected_apps_from_diff=())
        for path in ("docs/]^", "docs/x")
    ]
    assert ["docs" in f.matched_groups for f in fired] == [True, False], "the group fires where main's did"


VERIFICATION = {"test_targets": [{"name": "docs", "paths": ["docs/[\\]*"], "command": "true"}]}


def test_a_stored_verification_config_holding_one_still_reads_and_selects_as_before() -> None:
    config, why = load_verification_config(VERIFICATION)
    assert config is not None, f"a stored verification config with a glob main read no longer reads: {why}"
    assert [t.name for t in select_targets(config.test_targets, ["docs/]"])] == ["docs"]
    assert select_targets(config.test_targets, ["docs/x"]) == []
