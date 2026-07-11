"""Image-name qualification for the configurable container registry.

embry0 builds two host-side images (`embry0-proxy:latest`, `embry0-sandbox:latest`)
and references them when launching containers inside DinD. In the bootstrap flow they
are pushed to a sidecar registry; DinD pulls from there. Code paths that pass image
names to the DinD daemon must call `qualify_image()` so the configured registry prefix
is applied at runtime — keeping the flow identical for a future K8s migration where
the prefix becomes the production registry URL.
"""

from __future__ import annotations


def qualify_image(image: str, registry: str) -> str:
    """Return ``image`` prefixed with ``registry`` unless already qualified.

    "Already qualified" means the first path segment looks like a registry host
    (contains a `.` or `:` or is `localhost`), matching Docker's own resolution
    rules. Empty `registry` is a no-op.
    """
    if not registry or not image:
        return image
    if "/" in image:
        first_segment = image.split("/", 1)[0]
        if "." in first_segment or ":" in first_segment or first_segment == "localhost":
            return image
    return f"{registry.rstrip('/')}/{image}"
