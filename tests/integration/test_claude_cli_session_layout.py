"""Live Claude CLI session-layout verification (drift detector).

Runs the REAL bundled Claude CLI inside a QA sandbox, captures the
resulting session_id, then asserts that ``find_session_file`` discovers
it. This is the canary that catches CLI on-disk-format drift between
upgrades — when Anthropic moves the session file, this test fails BEFORE
the broken state ships.

When this test fails:

1. ``docker exec <sandbox> find /home/agent/.claude -name '*.jsonl'`` to
   see where the CLI now writes.
2. Update ``athanor/agents/claude_cli_session.py``'s discovery hierarchy
   to cover the new location (keep old branches for back-compat).
3. Update ``docs/contracts/claude-cli-session-file-layout.md`` to the
   new pinned CLI version.
4. Re-run this test.

Currently a scaffold — gated behind ``requires_dind`` and skipped until
the sandbox plumbing has a small enough surface to invoke from a
unit-style test (today, booting a sandbox requires the QA pipeline to be
wired through, which lands with the qa_boot plan).
"""

from __future__ import annotations

import pytest


@pytest.mark.requires_dind
@pytest.mark.asyncio
async def test_real_cli_writes_to_a_path_we_can_discover() -> None:
    """The bundled Claude CLI's session file must be discoverable by
    ``find_session_file`` immediately after a one-shot prompt.

    Implementation outline (when un-skipping):
      1. Boot a sandbox via ``app.state.sandbox_manager`` with cwd=/workspace.
      2. ``docker exec`` the bundled CLI with a trivial prompt + capture
         the printed session_id (``--print-session-id`` or stdout parse).
      3. ``docker exec find /home/agent/.claude -name '<id>.jsonl'`` —
         confirm a file exists.
      4. Call ``find_session_file(home_dir=/home/agent, session_id=<id>,
         project_cwd='/workspace')`` from inside the sandbox (e.g. via a
         small ``docker exec python -c`` shim) and assert it returns the
         same path the ``find`` shell command found.
      5. Tear down the sandbox.

    The contract under test is "the CLI's on-disk layout matches our
    discovery hierarchy." Failure means either Anthropic moved the file
    or the discovery hierarchy regressed.
    """
    pytest.skip(
        "scaffold — implement when boot_qa_node lands so we have a "
        "lightweight sandbox-boot fixture available to integration tests"
    )
