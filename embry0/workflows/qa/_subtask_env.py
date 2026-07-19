"""Shared sandbox-env builder for QA pipelines.

Both single-app (init_qa_node) and multi-app (acquire_sandbox_node) sandbox
allocation uses the same env shape:

  - User-supplied env vars (filtered for safety via _filter_user_env_for_sandbox)
  - EMBRY0_GIT_PROXY_URL (if proxy is configured)
  - QA_JOB_ID, QA_ATTEMPT_N, QA_NETWORK_NAME (infrastructure vars consumed by
    in-sandbox code and post-run log capture)
"""

from __future__ import annotations

from typing import Any

# Where the storageState JSON lives inside the sandbox (EMB-40). Under
# /workspace/.qa so shared-volume fan-outs keep it per-subtask (tmpfs overlay)
# and the QA agent's Edit whitelist covers it. Exported to the sandbox as
# QA_STORAGE_STATE_PATH (login-command contract) and PLAYWRIGHT_MCP_STORAGE_STATE
# (read natively by the playwright-mcp stdio server at browser-context creation).
QA_STORAGE_STATE_PATH = "/workspace/.qa/storage-state.json"


def build_qa_sandbox_env(
    *,
    user_env_vars: Any,
    git_proxy_url: str | None,
    qa_job_id: str,
    attempt_n: int,
    qa_network_name: str,
    turbo_remote_config: Any = None,
    storage_state: bool = False,
) -> dict[str, str]:
    """Build the env dict passed to SandboxManager.create() for QA sandboxes.

    `user_env_vars` may be a list (API shape) or dict (legacy shape) or None;
    delegates filtering to _filter_user_env_for_sandbox which handles both.

    `git_proxy_url` empty/None → no `EMBRY0_GIT_PROXY_URL` key (clones fall
    back to plain HTTPS for public repos; private clones will fail).

    `qa_network_name` is "" for process-mode sandboxes (the env var is set
    anyway since callers may rely on its presence; downstream code branches
    on `mode`, not on this var being non-empty).

    `turbo_remote_config` (TurboRemoteConfig | None) — if provided, adds
    TURBO_API/TURBO_TEAM/TURBO_TOKEN env vars for Turbo remote cache.

    `storage_state` — True when the resolved app config declares an `auth:`
    block (EMB-40); adds QA_STORAGE_STATE_PATH + PLAYWRIGHT_MCP_STORAGE_STATE
    so the login step knows where to write and playwright-mcp seeds the
    browser context from that file.
    """
    from embry0.cache.turbo_remote import turbo_env_vars
    from embry0.workflows.issue_to_pr.nodes import _filter_user_env_for_sandbox

    env = _filter_user_env_for_sandbox(
        user_env_vars or [],
        qa_active=True,
    )
    if git_proxy_url:
        env["EMBRY0_GIT_PROXY_URL"] = git_proxy_url
    env.update(
        {
            "QA_JOB_ID": qa_job_id,
            "QA_ATTEMPT_N": str(attempt_n),
            "QA_NETWORK_NAME": qa_network_name,
        }
    )
    env.update(turbo_env_vars(turbo_remote_config))
    if storage_state:
        env["QA_STORAGE_STATE_PATH"] = QA_STORAGE_STATE_PATH
        env["PLAYWRIGHT_MCP_STORAGE_STATE"] = QA_STORAGE_STATE_PATH
        # playwright-mcp only applies storageState to ISOLATED browser
        # contexts (the default persistent-profile mode uses
        # launchPersistentContext, which cannot accept storageState — the
        # env var is silently ignored, verified against @playwright/mcp
        # 0.0.78 on job-ca502fac2ed4). Isolated mode keeps the profile
        # in memory, which is equivalent for QA: the persistent profile
        # was per-launch-temporary anyway.
        env["PLAYWRIGHT_MCP_ISOLATED"] = "1"
    return env
