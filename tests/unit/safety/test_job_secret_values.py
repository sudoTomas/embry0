"""``job_secret_values``: the values a job's own output is scrubbed with.

Its env vars as the sandbox had them, merged rows or the legacy plain dict:
every secret-typed value, and the value of every variable whose name looks
secret, besides this process's and the registry's. A value nobody marked
secret, under a plain name, is not among them.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.safety.redaction import job_secret_values

# Low-entropy fakes.
SECRET_TYPED = "cfg-000000000051"
SECRET_NAMED = "tok-000000000052"
PLAIN = "plain-value-000053"


def test_merged_rows_give_secret_typed_and_secret_named_values() -> None:
    values = job_secret_values(
        [
            {"key": "DEPLOY_SETTING", "value": SECRET_TYPED, "scope": "app", "var_type": "secret"},
            {"key": "SERVICE_TOKEN", "value": SECRET_NAMED, "scope": "app", "var_type": "config"},
            {"key": "PLAIN_SETTING", "value": PLAIN, "scope": "app", "var_type": "config"},
        ]
    )

    assert SECRET_TYPED in values, "a secret-typed value under a plain name is not scrubbed"
    assert SECRET_NAMED in values, "the value of a variable whose name looks secret is not scrubbed"
    assert PLAIN not in values, "a value nobody marked secret is scrubbed"


def test_the_legacy_dict_shape_gives_secret_named_values() -> None:
    values = job_secret_values({"SERVICE_TOKEN": SECRET_NAMED, "PLAIN_SETTING": PLAIN})

    assert SECRET_NAMED in values, "the legacy {key: value} shape is not read: its secret-named value is not scrubbed"
    assert PLAIN not in values, "a value nobody marked secret is scrubbed"


@pytest.mark.parametrize("env_vars", [None, [], {}])
def test_no_env_vars_give_this_process_s_values(env_vars: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBRY0_TEST_SERVICE_TOKEN", SECRET_NAMED)

    assert SECRET_NAMED in job_secret_values(env_vars), "this process's secret-named values are not scrubbed"
