"""Spec 3.2: the skills-store volume is shared by the orchestrator and dind at
the same path, because sandbox bind mounts resolve in dind's namespace."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def _compose() -> dict:
    return yaml.safe_load((ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8"))


def test_orchestrator_mounts_the_skills_store_read_write():
    assert "skills-store:/skills" in _compose()["services"]["orchestrator"]["volumes"]


def test_dind_mounts_the_skills_store_read_only_at_the_same_path():
    assert "skills-store:/skills:ro" in _compose()["services"]["dind"]["volumes"]


def test_skills_store_volume_is_declared():
    assert "skills-store" in _compose()["volumes"]


def test_orchestrator_image_hands_skills_to_the_non_root_user():
    dockerfile = (ROOT / "infra" / "Dockerfile.orchestrator").read_text(encoding="utf-8")
    assert "mkdir -p /data /skills" in dockerfile
    assert "chown orchestrator:orchestrator /data /skills" in dockerfile
