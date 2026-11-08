"""The test gate reports its test names and summary lines scrubbed.

A run's failed test names and its summary line are read from the same log as
its output tail, and go wherever the gate's report goes: the
job_verifications row, the job's checkpoints, the summary that ends a job
with new failures, and the pull request comment. They pass through the
scrubber the tail does (``scrub.py``), after head and base have been compared
by the names as read, one entry per test as read. The gate scrubs with the
job's own secret values too: its secret-typed and secret-named env vars, as
its sandbox had them, and this process's secret-named ones.
"""

from __future__ import annotations

import inspect
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from structlog.testing import capture_logs

from embry0.workflows.issue_to_pr.nodes._verify import verify_node
from embry0.workflows.verification.render import render_verification_comment
from embry0.workflows.verification.runner import run_gate
from embry0.workflows.verification.scrub import REDACTED
from tests.unit.workflows.issue_to_pr.test_verify_node import JAVA, VERIFICATION, _run, _state
from tests.unit.workflows.issue_to_pr.test_verify_node import _config as _node_config
from tests.unit.workflows.verification.test_runner import (  # noqa: F401 - clock is an autouse fixture
    BASE_DIR,
    BRANCH,
    FORGE_URL,
    HEAD_DIR,
    JVM,
    FakeDocker,
    _config,
    _gate,
    clock,
)

# A secret known by value, as the credential registry's are, and one known by
# the name it is assigned to. Neither has a credential's shape.
KNOWN_VALUE = "s3cr3t-nonstandard-value"
NAMED_VALUE = "tok-000000000001"

PLAIN = "tests/test_plain.py::test_plain"
HEAD_LOG = "\n".join(
    [
        f"FAILED tests/test_login.py::test_login[api_token={NAMED_VALUE}] - AssertionError",
        f"FAILED tests/test_clone.py::test_clone[{KNOWN_VALUE}] - RuntimeError",
        f"FAILED {PLAIN} - AssertionError",
        f"======= 3 failed, 1 passed in 0.50s ({KNOWN_VALUE}) =======",
    ]
)


@pytest.fixture
def registered_secret():
    from embry0.safety.redaction import known_secret_values, register_known_secrets

    before = known_secret_values()
    register_known_secrets([KNOWN_VALUE])
    yield KNOWN_VALUE
    register_known_secrets(before, reset=True)


async def _red_head_report():
    docker = FakeDocker({(HEAD_DIR, JVM): [(1, HEAD_LOG)], (BASE_DIR, JVM): [(0, "ok")]})
    return await _gate(docker, _config(head_reruns=0), [JAVA])


def _leaks(text: str) -> list[str]:
    return [value for value in (KNOWN_VALUE, NAMED_VALUE) if value in text]


@pytest.mark.asyncio
async def test_the_stored_names_and_summary_are_scrubbed(registered_secret: str) -> None:
    report = await _red_head_report()
    head = report.runs_for("head")[0]

    assert head.failed_tests is not None and len(head.failed_tests) == 3, head.failed_tests
    leaked_names = [name for name in head.failed_tests if _leaks(name)]
    assert leaked_names == [], f"failed test names reach the report unscrubbed: {leaked_names}"
    assert head.summary is not None and not _leaks(head.summary), f"the summary line is unscrubbed: {head.summary}"
    assert REDACTED in head.summary and head.summary.startswith("3 failed, 1 passed")

    verdict = report.targets[0]
    assert verdict.status == "new_failures" and len(verdict.new_failures) == 3, verdict
    assert [name for name in verdict.new_failures if _leaks(name)] == [], (
        f"the verdict lists test names unscrubbed: {verdict.new_failures}"
    )
    assert PLAIN in verdict.new_failures and PLAIN in head.failed_tests, "a name with nothing to scrub changed"

    stored = json.dumps(report.to_dict())
    assert _leaks(stored) == [], "the report as stored still carries a secret from a test name or summary"


SECRET_TEST = f"tests/test_clone.py::test_clone[{KNOWN_VALUE}]"
SECRET_FAILS = (1, f"FAILED {SECRET_TEST} - RuntimeError")
GREEN = (0, "ok")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("head", "base", "listed_in"),
    [
        ([SECRET_FAILS], [SECRET_FAILS], "preexisting"),
        ([SECRET_FAILS, GREEN], [GREEN], "flaky"),
        ([GREEN], [SECRET_FAILS], "fixed"),
    ],
    ids=["preexisting", "flaky", "fixed"],
)
async def test_every_list_of_names_in_a_verdict_is_scrubbed(
    registered_secret: str, head: list[tuple[int, str]], base: list[tuple[int, str]], listed_in: str
) -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): head, (BASE_DIR, JVM): base})

    report = await _gate(docker, _config(head_reruns=1, base_run="always"), [JAVA])

    names = getattr(report.targets[0], listed_in)
    assert names == [f"tests/test_clone.py::test_clone[{REDACTED}]"], f"{listed_in} lists {names}"
    assert _leaks(json.dumps(report.to_dict())) == [], f"the stored report carries the secret ({listed_in})"


@pytest.mark.asyncio
async def test_head_and_base_are_compared_by_the_names_as_read() -> None:
    """Two tests whose names differ only in a part the scrubber redacts stay two tests."""
    head_log = "FAILED tests/test_auth.py::test_auth[token=second] - AssertionError"
    base_log = "FAILED tests/test_auth.py::test_auth[token=first] - AssertionError"
    docker = FakeDocker({(HEAD_DIR, JVM): [(1, head_log)], (BASE_DIR, JVM): [(1, base_log)]})

    report = await _gate(docker, _config(head_reruns=0), [JAVA])

    verdict = report.targets[0]
    assert (verdict.status, verdict.preexisting) == ("new_failures", []), (
        f"a test that fails only on head was matched with a different test failing on base: {verdict}"
    )
    assert verdict.new_failures == [f"tests/test_auth.py::test_auth[token={REDACTED}]"], (
        f"the name, scrubbed, keeps the runner's closing bracket: {verdict.new_failures}"
    )
    assert report.verdict == "new_failures"


@pytest.mark.asyncio
async def test_nothing_the_verify_node_stores_shows_or_sends_carries_them(registered_secret: str) -> None:
    config, stored = _node_config()

    cmd, _ = await _run(_state([JAVA]), config, await _red_head_report())

    row = stored.upsert.await_args.kwargs["result"]
    checkpoint = cmd.update["verification"]
    summary = cmd.update["result_summary"]
    comment = render_verification_comment(row)
    for where, text in [
        ("the job_verifications row", json.dumps(row)),
        ("the checkpoint", json.dumps(checkpoint)),
        ("the job's result summary", summary),
        ("the pull request comment", comment),
    ]:
        assert _leaks(text) == [], f"{where} carries a secret from a test name or summary"
    assert PLAIN in summary and PLAIN in comment
    assert f"test_login[api_token={REDACTED}]" in comment


# --- one entry per test -------------------------------------------------------

TWO_TOKENS = "\n".join(
    [
        "FAILED tests/test_auth.py::test_auth[token=aaaa] - AssertionError",
        "FAILED tests/test_auth.py::test_auth[token=bbbb] - AssertionError",
        "======= 2 failed in 0.20s =======",
    ]
)
SCRUBBED_AUTH = f"tests/test_auth.py::test_auth[token={REDACTED}]"


@pytest.mark.asyncio
async def test_two_tests_that_scrub_to_one_name_stay_two_entries() -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): [(1, TWO_TOKENS)], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(head_reruns=0), [JAVA])
    config, stored = _node_config()

    cmd, _ = await _run(_state([JAVA]), config, report)

    assert report.targets[0].new_failures == [SCRUBBED_AUTH, SCRUBBED_AUTH], report.targets[0]
    assert report.runs_for("head")[0].failed_tests == [SCRUBBED_AUTH, SCRUBBED_AUTH]
    comment = render_verification_comment(stored.upsert.await_args.kwargs["result"])
    assert "Tests that fail only with this change (2)" in comment, f"the comment counts two tests as one: {comment}"
    assert comment.count(SCRUBBED_AUTH) == 2, f"the comment lists the two tests as one: {comment}"
    assert cmd.update["result_summary"].count(SCRUBBED_AUTH) == 2, (
        f"the job's summary lists the two tests as one: {cmd.update['result_summary']}"
    )


def test_a_name_left_whole_is_listed_once_however_many_groups_name_it() -> None:
    gate = {
        "verdict": "new_failures",
        "targets": [
            {"target": "jvm", "status": "new_failures", "new_failures": [PLAIN, SCRUBBED_AUTH]},
            {"target": "web", "status": "new_failures", "new_failures": [PLAIN, SCRUBBED_AUTH]},
        ],
    }

    comment = render_verification_comment({"gate": gate})

    assert comment.count(f"`{PLAIN}`") == 1, f"equal text is one test: {comment}"
    assert comment.count(SCRUBBED_AUTH) == 2, f"a scrubbed name is listed each time it appears: {comment}"
    assert "Tests that fail only with this change (3)" in comment, comment


# --- the job's own secret values --------------------------------------------

# Low-entropy fakes: a secret-typed variable under a plain name, a config variable under a secret-looking name,
# and a secret-looking variable of the orchestrator's own environment.
JOB_SECRET = "cfg-000000000042"
JOB_NAMED = "tok-000000000043"
PROCESS_NAMED = "key-000000000044"
JOB_ENV = [
    {"key": "DEPLOY_SETTING", "value": JOB_SECRET, "scope": "app", "var_type": "secret"},
    {"key": "SERVICE_TOKEN", "value": JOB_NAMED, "scope": "app", "var_type": "config"},
    {"key": "PLAIN_SETTING", "value": "not-a-secret-000", "scope": "app", "var_type": "config"},
]
JOB_LOG = "\n".join(
    [
        f"FAILED tests/test_deploy.py::test_deploy[{JOB_SECRET}] - AssertionError",
        f"FAILED tests/test_service.py::test_service[{JOB_NAMED}] - AssertionError",
        f"FAILED tests/test_process.py::test_process[{PROCESS_NAMED}] - AssertionError",
        f"connecting with {JOB_SECRET} {JOB_NAMED} {PROCESS_NAMED} not-a-secret-000",
        f"======= 3 failed in 0.20s ({JOB_SECRET}) =======",
    ]
)


def _job_leaks(text: str) -> list[str]:
    return [value for value in (JOB_SECRET, JOB_NAMED, PROCESS_NAMED) if value in text]


@pytest.mark.asyncio
async def test_the_gate_scrubs_names_summary_tail_and_excerpts_with_the_values_it_is_given() -> None:
    assert "scrub_values" in inspect.signature(run_gate).parameters, "run_gate takes no values to scrub with"
    printed = {(HEAD_DIR, "cd frontend && npm test"): ("", f"cannot start: {JOB_SECRET}\n")}
    docker = FakeDocker(
        {
            (HEAD_DIR, JVM): [(1, JOB_LOG)],
            (BASE_DIR, JVM): [(0, "ok")],
            (HEAD_DIR, "cd frontend && npm test"): [(0, "")],
        },
        printed=printed,
    )

    with capture_logs() as logs:
        report = await run_gate(
            docker=docker,
            container_id="c1",
            config=_config(head_reruns=0),
            changed_files=[JAVA, "frontend/src/App.tsx"],
            base_ref="main",
            branch=BRANCH,
            forge_url=FORGE_URL,
            scrub_values=[JOB_SECRET, JOB_NAMED, PROCESS_NAMED],
        )

    assert any(e["event"] == "verify_run_exec_printed" for e in logs), f"no excerpt was logged: {logs}"
    assert _job_leaks(repr(logs)) == [], f"a log line's excerpt carries a value the gate was given: {logs}"
    stored = json.dumps(report.to_dict())
    assert _job_leaks(stored) == [], "the report carries a value the gate was given"
    assert "not-a-secret-000" in stored, "a value nobody named secret was scrubbed"


@pytest.mark.asyncio
async def test_the_verify_node_scrubs_with_the_job_s_own_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBRY0_TEST_SERVICE_API_KEY", PROCESS_NAMED)
    config, stored = _node_config()
    command = VERIFICATION["test_targets"][0]["command"]
    config["configurable"]["docker"] = FakeDocker(
        {(HEAD_DIR, command): [(1, JOB_LOG)], (BASE_DIR, command): [(0, "ok")]}
    )
    state = _state([JAVA], user_env_vars=JOB_ENV)

    with (
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None),
        patch("embry0.workflows.issue_to_pr.nodes._verify.sweep_sandbox", new=_clean_sweep()),
        patch("embry0.execution.workspace_diff.resolve_base_branch", new=AsyncMock(return_value="main")),
        patch("embry0.execution.workspace_diff.compute_changed_files_via_diff", new=AsyncMock(return_value=[JAVA])),
    ):
        cmd = await verify_node(state, config)  # type: ignore[arg-type]

    row = stored.upsert.await_args.kwargs["result"]
    assert row["gate"]["verdict"] == "new_failures", row["gate"]
    for where, text in [
        ("the job_verifications row", json.dumps(row)),
        ("the checkpoint", json.dumps(cmd.update["verification"])),
        ("the job's result summary", cmd.update["result_summary"]),
        ("the pull request comment", render_verification_comment(row)),
    ]:
        assert _job_leaks(text) == [], f"{where} carries the job's secret values: {_job_leaks(text)}"
    assert "not-a-secret-000" in json.dumps(row), "a value nobody named secret was scrubbed"


def _clean_sweep() -> Any:
    from embry0.workflows.verification.sweep import SweepResult

    return AsyncMock(return_value=SweepResult(clean=True))


# --- the sweep's excerpts ---------------------------------------------------

UNREADABLE_SWEEP = (0, f"not a report {JOB_SECRET}\n", f"noise {JOB_NAMED}\n")


@pytest.mark.asyncio
async def test_the_gate_s_sweeps_scrub_their_excerpts_with_the_values_it_is_given() -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): [(0, "ok")], (BASE_DIR, JVM): [(0, "ok")]}, sweeps=[UNREADABLE_SWEEP])

    with capture_logs() as logs:
        await run_gate(
            docker=docker,
            container_id="c1",
            config=_config(head_reruns=0),
            changed_files=[JAVA],
            base_ref="main",
            branch=BRANCH,
            forge_url=FORGE_URL,
            scrub_values=[JOB_SECRET, JOB_NAMED],
        )

    unreadable = [e for e in logs if e["event"] == "sandbox_sweep_unreadable"]
    assert unreadable, f"the unreadable sweep logged nothing: {logs}"
    assert _job_leaks(repr(unreadable)) == [], f"a sweep's excerpt carries a value the gate was given: {unreadable}"
    assert "not a report" in unreadable[0]["stdout"], "the excerpt itself is kept"


@pytest.mark.asyncio
async def test_the_verify_node_s_own_sweep_scrubs_with_the_job_s_secret_values() -> None:
    config, _ = _node_config()
    command = VERIFICATION["test_targets"][0]["command"]
    config["configurable"]["docker"] = FakeDocker(
        {(HEAD_DIR, command): [(0, "ok")], (BASE_DIR, command): [(0, "ok")]}, sweeps=[UNREADABLE_SWEEP]
    )

    with (
        capture_logs() as logs,
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None),
        patch("embry0.execution.workspace_diff.resolve_base_branch", new=AsyncMock(return_value="main")),
        patch("embry0.execution.workspace_diff.compute_changed_files_via_diff", new=AsyncMock(return_value=[JAVA])),
    ):
        await verify_node(_state([JAVA], user_env_vars=JOB_ENV), config)  # type: ignore[arg-type]

    unreadable = [e for e in logs if e["event"] == "sandbox_sweep_unreadable"]
    assert unreadable, f"the node's sweep logged nothing: {logs}"
    assert _job_leaks(repr(unreadable)) == [], f"the node's sweep excerpt carries the job's secret values: {unreadable}"
