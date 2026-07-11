from pathlib import Path

import yaml


def test_compose_cache_yaml_parses():
    p = Path(__file__).resolve().parents[3] / "infra" / "docker-compose.cache.yml"
    parsed = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert "services" in parsed
    assert "turbo-cache" in parsed["services"]
    assert parsed["services"]["turbo-cache"]["image"].startswith("ducktors/")


def test_compose_cache_yaml_joins_backend_network():
    """The overlay attaches turbo-cache to the main stack's `backend` network
    (defined in infra/docker-compose.yml; the overlay is merged-mode only)."""
    p = Path(__file__).resolve().parents[3] / "infra" / "docker-compose.cache.yml"
    parsed = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert parsed["services"]["turbo-cache"]["networks"] == ["backend"]
