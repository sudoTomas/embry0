"""Shared sandbox-env builder for QA pipelines.

Both single-app (init_qa_node) and multi-app (acquire_sandbox_node) sandbox
allocation uses the same env shape:

  - User-supplied env vars (filtered for safety via _filter_user_env_for_sandbox)
  - ATHANOR_GIT_PROXY_URL (if proxy is configured)
  - QA_JOB_ID, QA_ATTEMPT_N, QA_NETWORK_NAME (infrastructure vars consumed by
    in-sandbox code and post-run log capture)
"""

from __future__ import annotations

from typing import Any


def build_qa_sandbox_env(
    *,
    user_env_vars: Any,
    git_proxy_url: str | None,
    qa_job_id: str,
    attempt_n: int,
    qa_network_name: str,
) -> dict[str, str]:
    """Build the env dict passed to SandboxManager.create() for QA sandboxes.

    `user_env_vars` may be a list (API shape) or dict (legacy shape) or None;
    delegates filtering to _filter_user_env_for_sandbox which handles both.

    `git_proxy_url` empty/None → no `ATHANOR_GIT_PROXY_URL` key (clones fall
    back to plain HTTPS for public repos; private clones will fail).

    `qa_network_name` is "" for process-mode sandboxes (the env var is set
    anyway since callers may rely on its presence; downstream code branches
    on `mode`, not on this var being non-empty).
    """
    from athanor.workflows.issue_to_pr.nodes import _filter_user_env_for_sandbox

    env = _filter_user_env_for_sandbox(
        user_env_vars or [],
        qa_active=True,
    )
    if git_proxy_url:
        env["ATHANOR_GIT_PROXY_URL"] = git_proxy_url
    env.update(
        {
            "QA_JOB_ID": qa_job_id,
            "QA_ATTEMPT_N": str(attempt_n),
            "QA_NETWORK_NAME": qa_network_name,
        }
    )
    return env
