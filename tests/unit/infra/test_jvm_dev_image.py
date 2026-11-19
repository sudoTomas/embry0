"""The dev images run a target repository's Poetry checks, the JVM one alongside its Maven checks.

Both install the same Poetry, at or past 2.3.3: Poetry 1.8.4 and the releases it held its dependencies to
(dulwich < 0.22, virtualenv < 21 and, through virtualenv 20, filelock < 4) carried fixable HIGH advisories that no
1.x could take (#331).
"""

import re
from pathlib import Path

import embry0

_DOCKERFILE = Path(embry0.__file__).resolve().parents[1] / "infra" / "Dockerfile.sandbox"
_DEV_STAGES = ("dev-python", "dev-jvm-python")


def _stage(name: str) -> str:
    text = _DOCKERFILE.read_text(encoding="utf-8")
    return text.split(f"FROM base AS {name}", 1)[1].split("\nFROM ", 1)[0]


def _poetry_pins() -> dict[str, list[str]]:
    return {name: re.findall(r'"poetry==([0-9][0-9.]*)"', _stage(name)) for name in _DEV_STAGES}


def test_jvm_dev_image_installs_poetry() -> None:
    assert _poetry_pins()["dev-jvm-python"], "the jvm dev image must be able to run Poetry checks"


def test_both_dev_images_install_the_same_poetry() -> None:
    pins = _poetry_pins()
    assert all(len(found) == 1 for found in pins.values()), f"each dev stage pins Poetry once: {pins}"
    assert len({found[0] for found in pins.values()}) == 1, f"a monorepo's checks would meet two Poetrys: {pins}"


def test_the_dev_images_run_a_poetry_past_its_advisories() -> None:
    for name, (version,) in _poetry_pins().items():
        assert tuple(int(part) for part in version.split(".")) >= (2, 3, 3), f"{name} installs Poetry {version}"
