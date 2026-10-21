"""docs/ops/deploy.md says what a restart does to each kind of job, as the code does."""

from __future__ import annotations

import re
from pathlib import Path

from embry0.orchestration.restart import NOT_RESUMABLE_PIPELINES

DEPLOY = Path(__file__).resolve().parents[3] / "docs" / "ops" / "deploy.md"


def test_what_still_fails_names_every_pipeline_a_restart_cannot_resume() -> None:
    item = re.search(r"^3\. \*\*What still fails\.\*\*(.*?)(?=^\d\. |\Z)", DEPLOY.read_text(), re.S | re.M)
    assert item, "deploy.md has no '3. **What still fails.**' step"
    text = item.group(1).lower()
    missing = sorted(p for p in NOT_RESUMABLE_PIPELINES if p.lower() not in text)
    assert not missing, f"deploy.md 'What still fails' leaves out pipelines a restart fails: {missing}"
