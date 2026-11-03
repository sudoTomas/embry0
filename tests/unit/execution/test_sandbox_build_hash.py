"""The self-built sandbox image's build hash covers every file the image is built from (#320).

``SandboxImageManager`` keeps the image it built while the hash of what it was built from is unchanged. The hash
read a hand-kept list of files that left out modules the Dockerfile copies (``embry0/agents/``, most of the baked
``embry0/execution/`` files), so a change to one kept the old image. It now reads the list from the Dockerfile.

The paths the real Dockerfile names are found here without the parser the hash uses: every word of its
instructions that names a path in this repository. Each test changes one file at a time in a copy of the tree and
reads the hash again; nothing here builds an image.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from embry0.execution.image_manager import SandboxImageManager

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = Path("infra") / "Dockerfile.sandbox"
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


def _named_paths(dockerfile: Path) -> list[str]:
    """Every word of the Dockerfile's instructions (comments dropped) that names a path in the repository."""
    lines = [ln for ln in dockerfile.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
    words = set(re.split(r"[\s,=\\]+", "\n".join(lines)))
    return sorted(w for w in words if w and not w.startswith("/") and w not in (".", "..") and (ROOT / w).exists())


def _copy_tree(paths: list[str], into: Path) -> list[Path]:
    """``paths`` and the Dockerfile copied from the repository into ``into``; every file copied."""
    for name in [str(DOCKERFILE), *paths]:
        src, dst = ROOT / name, into / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, ignore=_IGNORE, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    return sorted(f for f in into.rglob("*") if f.is_file())


def _unhashed(root: Path, files: list[Path]) -> list[str]:
    """The files of ``files`` that can change without changing the build hash."""
    mgr = SandboxImageManager(None, app_root=str(root))  # type: ignore[arg-type]
    before = mgr._compute_build_hash()
    missed = []
    for f in files:
        original = f.read_bytes()
        f.write_bytes(original + b"\n# changed\n")
        if mgr._compute_build_hash() == before:
            missed.append(f.relative_to(root).as_posix())
        f.write_bytes(original)
    assert mgr._compute_build_hash() == before, "every file is back as it was"
    return missed


def test_the_word_scan_finds_every_source_the_dockerfile_copies() -> None:
    """The scan the tests below rely on finds each COPY of the build context, so a pass is not a scan that found
    nothing."""
    text = (ROOT / DOCKERFILE).read_text(encoding="utf-8")
    copies = re.findall(r"^COPY\s+(?!--from)(\S+)\s", text, re.MULTILINE)
    named = _named_paths(ROOT / DOCKERFILE)
    assert copies and set(copies) <= set(named), f"the scan finds every COPY source {copies}: {named}"
    assert "uv.lock" in named, f"the scan finds the lock the Dockerfile mounts: {named}"


def test_changing_any_file_the_sandbox_dockerfile_reads_changes_the_build_hash(tmp_path: Path) -> None:
    files = _copy_tree(_named_paths(ROOT / DOCKERFILE), tmp_path)
    assert len(files) > 10, f"the copy holds the image's files: {files}"
    missed = _unhashed(tmp_path, files)
    assert not missed, (
        f"{len(missed)} of the {len(files)} files infra/Dockerfile.sandbox copies or mounts can change "
        f"without the build hash changing, so a self-built image keeps their old version: {missed}"
    )


def test_a_source_the_dockerfile_gains_is_hashed_without_a_change_to_the_hash(tmp_path: Path) -> None:
    """A new COPY source and a new bind-mounted file are hashed because the Dockerfile names them, with no list of
    files to keep beside it."""
    _copy_tree(_named_paths(ROOT / DOCKERFILE), tmp_path)
    dockerfile = tmp_path / DOCKERFILE
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8")
        + "COPY embry0/newly_baked/ /app/embry0/newly_baked/\n"
        + "COPY --chown=agent embry0/execution/newly_baked.py /app/embry0/execution/newly_baked.py\n"
        + "RUN --mount=type=bind,source=infra/scripts/newly-mounted.txt,target=/opt/n.txt cat /opt/n.txt\n",
        encoding="utf-8",
    )
    added = [
        tmp_path / "embry0" / "newly_baked" / "module.py",
        tmp_path / "embry0" / "newly_baked" / "data.json",
        tmp_path / "embry0" / "execution" / "newly_baked.py",
        tmp_path / "infra" / "scripts" / "newly-mounted.txt",
    ]
    for f in added:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x = 1\n", encoding="utf-8")
    missed = _unhashed(tmp_path, added)
    assert not missed, f"a source the Dockerfile gains is part of the build hash: {missed} are not"


def test_moving_a_file_within_a_copied_directory_changes_the_build_hash(tmp_path: Path) -> None:
    """The image copies a file to its own path: the same contents under another name is another image."""
    _copy_tree(_named_paths(ROOT / DOCKERFILE), tmp_path)
    mgr = SandboxImageManager(None, app_root=str(tmp_path))  # type: ignore[arg-type]
    before = mgr._compute_build_hash()
    source = tmp_path / "embry0" / "sandbox" / "runner.py"
    source.rename(source.with_name("runner0.py"))  # sorts where runner.py did
    assert mgr._compute_build_hash() != before, "a file moved within a copied directory changes the build hash"


def test_bytecode_beside_the_code_does_not_change_the_build_hash(tmp_path: Path) -> None:
    """Importing the code writes ``__pycache__``; a hash that read it would rebuild the image at every start."""
    _copy_tree(_named_paths(ROOT / DOCKERFILE), tmp_path)
    mgr = SandboxImageManager(None, app_root=str(tmp_path))  # type: ignore[arg-type]
    before = mgr._compute_build_hash()
    cache = tmp_path / "embry0" / "sandbox" / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "runner.cpython-312.pyc").write_bytes(b"\x00compiled")
    assert mgr._compute_build_hash() == before, "compiled bytecode is not part of the build hash"


@pytest.mark.parametrize("name", ["Dockerfile.sandbox", "Dockerfile.sandbox.qa"])
def test_the_hash_follows_the_same_paths_the_word_scan_finds(name: str) -> None:
    """The hash's inputs are exactly the files under the paths the Dockerfile names, read by another route."""
    from embry0.execution.dockerfile_sources import context_sources

    text = (ROOT / "infra" / name).read_text(encoding="utf-8")
    assert sorted(context_sources(text)) == _named_paths(ROOT / "infra" / name), (
        f"the parser and the word scan agree on the build-context paths infra/{name} reads"
    )
