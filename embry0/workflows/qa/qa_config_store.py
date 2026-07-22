"""External per-repo QA config store — Phase 1 file store (EMB-48).

Per-repo qa.yaml v2 files can live on the embry0 side instead of in the
target repo: ``<store>/<owner>__<repo>/qa.yaml``, where ``<store>`` is the
directory named by ``EMBRY0_QA_CONFIG_DIR`` (compose mounts the repo-root
``repo-configs/`` there, read-only). When a file exists for the repo it
REPLACES the in-repo ``.embry0/qa.yaml`` — no merge, exactly one source of
truth per repo. Absent, the in-repo file remains the source, so existing
integrations are unchanged.

Phase 2 moves the store into Postgres with an API/dashboard editor; this
file layout then becomes seed/backup. See docs/qa-yaml-reference.md.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

QA_CONFIG_DIR_ENV = "EMBRY0_QA_CONFIG_DIR"

# owner/name, each segment a plain GitHub-ish slug. Anything else (path
# separators, "..") must not be able to steer the filesystem lookup.
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def external_qa_config_path(repo: str) -> Path | None:
    """Path where the external config for ``repo`` would live, or None when
    the store is disabled (env unset) or ``repo`` is not a safe owner/name."""
    root = os.environ.get(QA_CONFIG_DIR_ENV, "").strip()
    if not root:
        return None
    if not _REPO_RE.fullmatch(repo):
        logger.warning("qa_config_store_bad_repo", repo=repo)
        return None
    owner, name = repo.split("/", 1)
    return Path(root) / f"{owner}__{name}" / "qa.yaml"


def load_external_qa_yaml(repo: str) -> str | None:
    """Raw qa.yaml text from the external store, or None when absent.

    Read failures on an existing file are real errors and propagate — a
    present-but-unreadable config must fail the run loudly, not silently
    fall back to a possibly-divergent in-repo file.
    """
    path = external_qa_config_path(repo)
    if path is None or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    logger.info("qa_config_external_store_hit", repo=repo, path=str(path))
    return text
