"""Regression test for the 2026-05-06 QA-pipeline silent-fail.

The sandbox image's `athanor/execution/` directory is a hand-picked subset
(see infra/Dockerfile.sandbox). When `agent_runner.py` imported
`SandboxManager` at module load, the import chain transitively required
`athanor.execution.image_registry` — a module that the Dockerfile does
NOT copy into the sandbox. The runner crashed with
`ModuleNotFoundError: No module named 'athanor.execution.image_registry'`
before any agent code could run, killing all 14 sub-tasks of every QA
job with no usable diagnostic until orchestrator-side stderr capture
landed earlier in the same session.

Fix: agent_runner.py now guards its SandboxManager import behind
TYPE_CHECKING. This test verifies that constraint stays in place: the
sandbox runner must be importable without any module from
`athanor/execution/` other than the four files the Dockerfile actually
copies (events, agent_runner, auth_provider, docker_client).

The check runs in a subprocess so it can wipe sys.modules without
breaking the test runner's own import cache (notably for fixtures that
hold references to e.g. SandboxInitError).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap


# The exhaustive list of athanor.execution.* modules that the sandbox image
# is allowed to load. Mirrors infra/Dockerfile.sandbox COPY directives.
_SANDBOX_ALLOWED_EXECUTION_MODULES = {
    "athanor.execution",
    "athanor.execution.events",
    "athanor.execution.agent_runner",
    "athanor.execution.auth_provider",
    "athanor.execution.docker_client",
}


def test_sandbox_runner_import_surface_does_not_pull_orchestrator_only_modules():
    """Loading the sandbox runner's transitive deps must not touch
    athanor.execution.* modules outside the Dockerfile's COPY list.

    A new file in athanor/execution/ that gets imported eagerly from any
    sandbox-side module will fail this test, forcing the author to either
    add it to the Dockerfile or guard the import (TYPE_CHECKING /
    function-local import).
    """
    probe = textwrap.dedent(
        """
        import importlib, json, sys
        importlib.import_module("athanor.sandbox.runner")
        importlib.import_module("athanor.execution.agent_runner")
        importlib.import_module("athanor.agents.executor_factory")
        loaded = sorted(
            n for n in sys.modules if n.startswith("athanor.execution.")
        )
        sys.stdout.write(json.dumps(loaded))
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
    )
    loaded = set(json.loads(result.stdout))
    leaked = sorted(loaded - _SANDBOX_ALLOWED_EXECUTION_MODULES)
    assert not leaked, (
        f"Sandbox runner pulled in {leaked!r}, but those modules are not "
        f"copied into the sandbox image (see infra/Dockerfile.sandbox). "
        f"Either add the file to the Dockerfile's COPY list or guard the "
        f"import behind TYPE_CHECKING / a function-local import."
    )
