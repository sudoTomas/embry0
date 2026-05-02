"""Label-based cleanup for QA jobs.

Two label families to filter on:
  com.docker.compose.project=qa_<job_id>   -- spawned by `qa-compose` wrapper
  athanor.qa_job_id=<job_id>               -- spawned directly via `docker run`

Plus the per-job network qa-net-<job_id>.

Called from:
  - report_node, every attempt (whether the attempt succeeded or failed)
  - ContainerReaper, periodically, to sweep orphans from crashed jobs
"""

from __future__ import annotations

import structlog

from athanor.execution.docker_client import DockerClient

logger = structlog.get_logger(__name__)


async def cleanup_qa_resources(docker: DockerClient, *, job_id: str) -> None:
    """Best-effort teardown of every container/network/volume for one QA job.

    Failures are logged and swallowed -- partial cleanup is better than
    aborting and leaving more orphans, and the next ContainerReaper sweep
    will catch anything missed.
    """
    base = docker._build_base_cmd()  # noqa: SLF001 -- intentional accessor

    async def _run(cmd: list[str]) -> str:
        try:
            return await docker.run_cmd(cmd, timeout=15)
        except RuntimeError as exc:
            logger.warning("qa_cleanup_step_failed", cmd=cmd, error=str(exc))
            return ""

    project_filter = f"label=com.docker.compose.project=qa_{job_id}"
    job_filter = f"label=athanor.qa_job_id={job_id}"

    # 1. Containers -- both label families.
    for f in (project_filter, job_filter):
        ids = (await _run(base + ["ps", "-aq", "--filter", f])).strip().split()
        if ids:
            await _run(base + ["rm", "-f", *ids])

    # 2. Volumes -- both label families.
    for f in (project_filter, job_filter):
        vols = (await _run(base + ["volume", "ls", "-q", "--filter", f])).strip().split()
        if vols:
            await _run(base + ["volume", "rm", *vols])

    # 3. The per-job network.
    await _run(base + ["network", "rm", f"qa-net-{job_id}"])

    logger.info("qa_cleanup_done", job_id=job_id)
