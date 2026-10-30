"""Image-name qualification for the configurable container registry.

embry0 builds two host-side images (`embry0-proxy:latest`, `embry0-sandbox:latest`)
and references them when launching containers inside DinD. In the bootstrap flow they
are pushed to a sidecar registry; DinD pulls from there. Code paths that pass image
names to the DinD daemon must call `qualify_image()` so the configured registry prefix
is applied at runtime — keeping the flow identical for a future K8s migration where
the prefix becomes the production registry URL.

Digest pins (#523). A tag in a registry is only as trustworthy as everything that can
push to it. The bootstrap records the digest of each image it pushes, and the
orchestrator installs those pins at startup (`install_image_pins`). From then on
`qualify_image()` names a pinned image as ``<registry>/<name>:<tag>@<digest>``: Docker
resolves that reference by the digest alone and checks the content against it, so an
image re-pushed under the same tag is never the one that runs. An image with no pin
keeps its plain tag.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
# `docker push` prints `<tag>: digest: sha256:<hex> size: <n>` for what it pushed.
# The pin is parsed from that line, not read back afterwards: on the containerd
# image store the daemon's RepoDigests can name a multi-platform index that was
# never pushed, and asking the registry would trust the very thing a pin guards.
_PUSHED_DIGEST_RE = re.compile(r"\bdigest: (sha256:[0-9a-f]{64})\b")
_PIN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


class ImagePinError(ValueError):
    """The image pins are missing, unreadable or malformed."""


def is_registry_qualified(image: str) -> bool:
    """Does ``image`` already name a registry host?

    True when the first path segment contains a `.` or `:` or is `localhost`,
    matching Docker's own resolution rules. Such a name can be pulled; an
    unqualified one names only whatever the local daemon holds.
    """
    if "/" not in image:
        return False
    first_segment = image.split("/", 1)[0]
    return "." in first_segment or ":" in first_segment or first_segment == "localhost"


def strip_digest(image: str) -> str:
    """``reg/name:tag@sha256:…`` -> ``reg/name:tag``. A name without a digest is unchanged."""
    return image.split("@", 1)[0]


def is_valid_digest(value: str) -> bool:
    return bool(_DIGEST_RE.fullmatch(value))


def pushed_digest(push_output: str) -> str | None:
    """The digest ``docker push`` reported for what it pushed, or None.

    The last match wins: a push prints one digest line per tag it pushed.
    """
    found = _PUSHED_DIGEST_RE.findall(push_output or "")
    return found[-1] if found else None


@dataclass
class _PinStore:
    registry: str = ""
    pins: dict[str, str] = field(default_factory=dict)


# Process-wide, installed once at startup before anything resolves an image name.
# Every launch path already calls qualify_image(), so keeping the pins here rather
# than threading them through each constructor means no launch path can miss them.
_store = _PinStore()


def _normalize_registry(registry: str) -> str:
    return registry.rstrip("/")


def _with_tag(name: str) -> str:
    """``embry0-proxy`` -> ``embry0-proxy:latest``. A colon is a tag only after the last slash."""
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def parse_image_pins(text: str) -> dict[str, str]:
    """Parse the pins file the bootstrap writes: one ``<name>:<tag> <digest>`` per line.

    Blank lines and ``#`` comments are skipped. Any other line that is not a valid
    pin raises, naming the line: a pin that cannot be trusted must stop the start,
    not fall back to the tag it exists to replace.
    """
    pins: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise ImagePinError(f"line {number}: expected '<name>:<tag> <digest>', got {line!r}")
        name, digest = parts
        if not _PIN_NAME_RE.fullmatch(name) or is_registry_qualified(name):
            raise ImagePinError(f"line {number}: {name!r} is not an unqualified '<name>:<tag>'")
        if not is_valid_digest(digest):
            raise ImagePinError(f"line {number}: {digest!r} is not a sha256 digest")
        if pins.get(name, digest) != digest:
            raise ImagePinError(f"line {number}: {name!r} is pinned twice, to different digests")
        pins[name] = digest
    return pins


def load_image_pins(path: str | Path) -> dict[str, str]:
    """Read and parse the pins file at ``path``. ImagePinError if it is missing, bad or empty."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ImagePinError(f"cannot read the image pins file {path}: {exc}") from exc
    pins = parse_image_pins(text)
    if not pins:
        raise ImagePinError(f"the image pins file {path} pins no images")
    return pins


def install_image_pins(registry: str, pins: Mapping[str, str]) -> None:
    """Make ``pins`` (unqualified ``name:tag`` -> digest, recorded in ``registry``) process-wide."""
    if not registry:
        raise ImagePinError("image pins need the registry they were recorded in")
    for name, digest in pins.items():
        if "@" in name or is_registry_qualified(name) or not is_valid_digest(digest):
            raise ImagePinError(f"invalid pin {name!r} -> {digest!r}")
    _store.registry = _normalize_registry(registry)
    _store.pins = {_with_tag(name): digest for name, digest in pins.items()}


def clear_image_pins() -> None:
    """Forget every pin. A process that never installs any has none."""
    _store.registry = ""
    _store.pins = {}


def installed_image_pins() -> dict[str, str]:
    return dict(_store.pins)


def pinned_digest(image: str) -> str | None:
    """The installed pin for ``image``, or None.

    ``image`` may be unqualified (``embry0-proxy:latest``) or qualified with the
    registry the pins were recorded in (``registry:5000/embry0-proxy:latest``). A
    missing tag means ``latest``. A name that already carries a digest, or that
    names another registry, has no pin.
    """
    if not _store.pins or not image or "@" in image:
        return None
    name = image
    prefix = f"{_store.registry}/"
    if name.startswith(prefix):
        name = name[len(prefix) :]
    if is_registry_qualified(name):
        return None
    return _store.pins.get(_with_tag(name))


def qualify_image(image: str, registry: str) -> str:
    """Return ``image`` prefixed with ``registry`` unless already qualified.

    Empty `registry` is a no-op. When pins are installed for ``registry`` and one
    matches ``image``, the result also carries its digest (``…:<tag>@<digest>``).
    """
    if not registry or not image:
        return image
    qualified = image if is_registry_qualified(image) else f"{registry.rstrip('/')}/{image}"
    if _normalize_registry(registry) != _store.registry:
        return qualified
    digest = pinned_digest(qualified)
    return f"{qualified}@{digest}" if digest else qualified
