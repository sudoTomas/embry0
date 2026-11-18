"""The proxy image's rebuild recipe names every module the image copies from outside the proxy package (#57).

``infra/deploy.sh`` does not rebuild the proxy image, so a change to a module it copies reaches the proxies
only through the recipe in ``docs/ops/deploy.md``. The context proxy redacts with ``embry0/safety/redaction.py``;
a change to it that the recipe did not name would leave the proxies on the old redactor.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[3]


def test_the_proxy_rebuild_recipe_names_every_module_the_image_copies() -> None:
    dockerfile = (ROOT / "infra" / "Dockerfile.proxy").read_text()
    copied = set(re.findall(r"^COPY\s+(embry0/\S+\.py)\s", dockerfile, re.MULTILINE))
    outside = {p for p in copied if not p.startswith("embry0/execution/proxy/") and not p.endswith("/__init__.py")}
    assert "embry0/safety/redaction.py" in outside, f"the Dockerfile is not read as expected: {sorted(outside)}"
    doc = (ROOT / "docs" / "ops" / "deploy.md").read_text()
    recipe = next(
        line for line in doc.splitlines() if line.startswith("- **After code changes to `embry0/execution/proxy/`")
    )
    missing = sorted(p for p in outside if f"`{p}`" not in recipe)
    assert not missing, f"docs/ops/deploy.md's proxy recipe does not name modules the proxy image copies: {missing}"
