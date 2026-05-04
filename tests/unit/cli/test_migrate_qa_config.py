from pathlib import Path

import pytest
import yaml

from athanor.cli.migrate_qa_config import (
    MigrationError,
    migrate_v1_text_to_v2_text,
    migrate_v1_to_v2,
)
from athanor.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2


CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "qa-yaml-corpus"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "v1_name,v2_name",
    [
        ("single-app.yaml", "single-app.yaml"),
        ("single-app-with-seed.yaml", "single-app-with-seed.yaml"),
        ("single-app-with-e2e.yaml", "single-app-with-e2e.yaml"),
    ],
)
def test_v1_to_v2_matches_golden(v1_name: str, v2_name: str):
    v1 = _read(CORPUS / "v1" / v1_name)
    expected = _read(CORPUS / "v2" / v2_name)

    actual = migrate_v1_text_to_v2_text(v1, app_name="app")

    # Normalize both via parse → emit so cosmetic YAML differences are ignored
    parsed_actual = yaml.safe_load(actual)
    parsed_expected = yaml.safe_load(expected)
    assert parsed_actual == parsed_expected


def test_migrated_output_parses_via_v2_parser():
    v1 = _read(CORPUS / "v1" / "single-app.yaml")
    out = migrate_v1_text_to_v2_text(v1, app_name="hub")
    cfg = parse_qa_yaml_v2(out)
    assert cfg.version == 2
    assert "hub" in cfg.apps


def test_migrate_refuses_v2_input():
    v2_input = _read(CORPUS / "v2" / "single-app.yaml")
    with pytest.raises(MigrationError) as exc:
        migrate_v1_text_to_v2_text(v2_input, app_name="app")
    assert "version" in str(exc.value).lower()


def test_migrate_refuses_invalid_v1():
    with pytest.raises(MigrationError):
        migrate_v1_text_to_v2_text("not yaml: [", app_name="app")


def test_migrate_v1_to_v2_writes_file_and_backup(tmp_path: Path):
    qa_path = tmp_path / "qa.yaml"
    backup_path = tmp_path / "qa.v1.yaml.bak"
    qa_path.write_text(_read(CORPUS / "v1" / "single-app.yaml"), encoding="utf-8")

    migrate_v1_to_v2(qa_path, app_name="hub", write=True)

    assert backup_path.exists()
    parsed = parse_qa_yaml_v2(qa_path.read_text(encoding="utf-8"))
    assert parsed.version == 2
    assert "hub" in parsed.apps


def test_migrate_v1_to_v2_dry_run_does_not_write(tmp_path: Path):
    qa_path = tmp_path / "qa.yaml"
    qa_path.write_text(_read(CORPUS / "v1" / "single-app.yaml"), encoding="utf-8")
    original = qa_path.read_text(encoding="utf-8")

    output = migrate_v1_to_v2(qa_path, app_name="hub", write=False)

    assert qa_path.read_text(encoding="utf-8") == original
    assert "version: 2" in output
    assert not (tmp_path / "qa.v1.yaml.bak").exists()


def test_migrate_default_app_name_uses_parent_dir_when_unspecified(tmp_path: Path):
    repo = tmp_path / "my-cool-repo"
    athanor_dir = repo / ".athanor"
    athanor_dir.mkdir(parents=True)
    qa_path = athanor_dir / "qa.yaml"
    qa_path.write_text(_read(CORPUS / "v1" / "single-app.yaml"), encoding="utf-8")

    migrate_v1_to_v2(qa_path, app_name=None, write=True)

    parsed = parse_qa_yaml_v2(qa_path.read_text(encoding="utf-8"))
    assert "my-cool-repo" in parsed.apps
