"""A qa.yaml glob that starts with '!' is refused when a setup is written; a stored one keeps reading, warned of.

The grammar has no negation: ``!docs/**`` only ever matched a path whose
first directory starts with ``!``, never "everything but docs". Refusing it
when it is read would stop every stored setup that holds one.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

SETUP = """version: 2
qa_required: never
workspace_provider:
  type: npm-workspaces-turbo
conditional_acceptance_criteria:
  - name: docs
    when:
      changed_paths: ['!docs/**', 'web/**']
    criteria:
      - The docs open
"""


@pytest.fixture(autouse=True)
def _nothing_warned_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with no warning logged, whatever another test in this process read."""
    from collections import OrderedDict

    from embry0.workflows.qa import _glob_match

    monkeypatch.setattr(_glob_match, "_WARNED", OrderedDict())


def test_a_setup_with_a_negated_glob_is_refused_when_written() -> None:
    try:
        parse_qa_yaml_v2(SETUP, writing=True)
    except ValueError as exc:
        assert "negation is not supported" in str(exc), str(exc)
    else:
        pytest.fail("a setup with a glob that starts with '!' is accepted when written")


def test_a_stored_setup_with_one_reads_and_is_warned_of_once_per_repository_and_text() -> None:
    """#486's review: a stored setup is read at every run; its warning is a line when it is first read, or changed."""
    with capture_logs() as logs:
        cfg = parse_qa_yaml_v2(SETUP, repo="acme/web")
        parse_qa_yaml_v2(SETUP, repo="acme/web")
        parse_qa_yaml_v2(SETUP, repo="acme/api")
        parse_qa_yaml_v2(SETUP + "# changed\n", repo="acme/web")
    warned = [(e.get("repo"), e.get("globs")) for e in logs if e["event"] == "qa_config_negated_globs"]
    assert warned == [("acme/web", ["!docs/**"]), ("acme/api", ["!docs/**"]), ("acme/web", ["!docs/**"])], (
        f"not once per repository and text: {warned}"
    )
    assert cfg.conditional_acceptance_criteria[0].when.changed_paths == ["!docs/**", "web/**"]


def test_a_setup_without_one_logs_nothing_and_says_nothing() -> None:
    from embry0.workflows.qa.qa_yaml_v2 import negation_warning

    plain = SETUP.replace("'!docs/**', ", "")
    with capture_logs() as logs:
        cfg = parse_qa_yaml_v2(plain)
    assert not [entry for entry in logs if entry["event"] == "qa_config_negated_globs"]
    assert negation_warning(cfg) is None


def test_the_warning_names_the_globs() -> None:
    from embry0.workflows.qa.qa_yaml_v2 import negation_warning

    warning = negation_warning(parse_qa_yaml_v2(SETUP))
    assert warning is not None and "'!docs/**'" in warning and "a new save refuses them" in warning
