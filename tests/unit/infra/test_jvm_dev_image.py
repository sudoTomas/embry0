"""The dev images run a target repository's Poetry checks, the JVM one alongside its Maven checks.

Both install the same Poetry: uv.lock's `poetry` group (#645), at or past 2.3.3, the release with the fix for
Poetry 1.8.4's advisories (#331). How each stage installs it, hash-checked and checked against the lock, is pinned
in test_sandbox_image_lockfile.py.
"""

import shlex
from pathlib import Path

import embry0

_DOCKERFILE = Path(embry0.__file__).resolve().parents[1] / "infra" / "Dockerfile.sandbox"
_DEV_STAGES = ("dev-python", "dev-jvm-python")


def _stage(name: str) -> str:
    text = _DOCKERFILE.read_text(encoding="utf-8")
    return text.split(f"FROM base AS {name}", 1)[1].split("\nFROM ", 1)[0]


def _installs_the_poetry_group(name: str) -> bool:
    argv = shlex.split(_stage(name).replace("\\\n", " "), comments=True)
    return any(argv[i : i + 2] == ["--only-group", "poetry"] for i in range(len(argv) - 1))


def test_jvm_dev_image_installs_poetry() -> None:
    assert _installs_the_poetry_group("dev-jvm-python"), "the jvm dev image must be able to run Poetry checks"


def test_both_dev_images_install_the_locked_poetry() -> None:
    missing = [name for name in _DEV_STAGES if not _installs_the_poetry_group(name)]
    assert not missing, f"these dev stages do not install uv.lock's `poetry` group: {missing}"
    for name in _DEV_STAGES:
        assert '"poetry==' not in _stage(name), f"{name} pins Poetry by hand; it comes from uv.lock"
