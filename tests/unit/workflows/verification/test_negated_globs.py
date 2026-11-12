"""A verification config glob that starts with '!' is refused on write; a stored one keeps reading, warned of.

The grammar has no negation: ``!src/**`` only ever matched a path whose first
directory starts with ``!``, so a test target with it never ran and a tier
rule with it classified nothing. A request is refused (as a test setup is,
#486); a config stored with one reads as it did, and its warning is logged
once per repository and config, not at each run.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from embry0.workflows.verification.config import load_verification_config

STORED = {
    "test_targets": [{"name": "app", "command": "make test", "paths": ["!src/**", "web/**"]}],
    "tiers": {"backend_paths": ["!api/**"]},
}


def _warnings(logs: list[dict]) -> list[tuple[str | None, list[str]]]:
    return [(e.get("repo"), e.get("globs")) for e in logs if e["event"] == "verification_config_negated_globs"]


@pytest.fixture(autouse=True)
def _nothing_warned_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with no warning logged, whatever another test in this process read."""
    from collections import OrderedDict

    from embry0.workflows.qa import _glob_match

    monkeypatch.setattr(_glob_match, "_WARNED", OrderedDict())


def test_a_stored_config_with_one_reads_as_it_did_and_is_warned_of_once_per_repository_and_config() -> None:
    with capture_logs() as logs:
        config, problem = load_verification_config(STORED, repo="acme/web")
        load_verification_config(STORED, repo="acme/web")
        load_verification_config(STORED, repo="acme/api")
        changed = {**STORED, "head_reruns": 2}
        load_verification_config(changed, repo="acme/web")
    assert problem is None and config is not None, f"a stored config with a negated glob no longer reads: {problem}"
    assert config.test_targets[0].paths == ["!src/**", "web/**"]
    assert _warnings(logs) == [
        ("acme/web", ["!src/**", "!api/**"]),
        ("acme/api", ["!src/**", "!api/**"]),
        ("acme/web", ["!src/**", "!api/**"]),
    ], f"not once per repository and config: {_warnings(logs)}"


def test_the_warning_names_the_globs() -> None:
    from embry0.workflows.verification.config import negation_warning

    config, _ = load_verification_config(STORED, repo="acme/warn")
    warning = negation_warning(config)
    assert warning is not None and "'!src/**'" in warning and "'!api/**'" in warning
    plain, _ = load_verification_config({"test_targets": [{"name": "a", "command": "t", "paths": ["web/**"]}]})
    assert negation_warning(plain) is None
