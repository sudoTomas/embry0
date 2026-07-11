import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from embry0.cli.migrate_qa_config import (
    MigrationError,
    migrate_v1_text_to_v2_text,
    migrate_v1_to_v2,
)
from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

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
    embry0_dir = repo / ".embry0"
    embry0_dir.mkdir(parents=True)
    qa_path = embry0_dir / "qa.yaml"
    qa_path.write_text(_read(CORPUS / "v1" / "single-app.yaml"), encoding="utf-8")

    migrate_v1_to_v2(qa_path, app_name=None, write=True)

    parsed = parse_qa_yaml_v2(qa_path.read_text(encoding="utf-8"))
    assert "my-cool-repo" in parsed.apps


CLI_BIN = [sys.executable, "-m", "embry0.cli"]


def _toy_v1_in(tmp_path: Path) -> Path:
    qa_dir = tmp_path / "demo-repo" / ".embry0"
    qa_dir.mkdir(parents=True)
    qa = qa_dir / "qa.yaml"
    qa.write_text((CORPUS / "v1" / "single-app.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return qa


def test_cli_dry_run_prints_v2_and_does_not_write(tmp_path: Path):
    qa = _toy_v1_in(tmp_path)
    original = qa.read_text(encoding="utf-8")
    result = subprocess.run(
        CLI_BIN + ["migrate-qa-config", "--dry-run", "--qa-path", str(qa)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "version: 2" in result.stdout
    assert qa.read_text(encoding="utf-8") == original
    assert not qa.with_name("qa.v1.yaml.bak").exists()


def test_cli_write_replaces_file_and_creates_backup(tmp_path: Path):
    qa = _toy_v1_in(tmp_path)
    result = subprocess.run(
        CLI_BIN + ["migrate-qa-config", "--write", "--qa-path", str(qa), "--app-name", "hub"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert qa.with_name("qa.v1.yaml.bak").exists()
    new_text = qa.read_text(encoding="utf-8")
    assert "version: 2" in new_text
    assert "hub:" in new_text


def test_cli_requires_one_of_dry_run_or_write(tmp_path: Path):
    qa = _toy_v1_in(tmp_path)
    result = subprocess.run(
        CLI_BIN + ["migrate-qa-config", "--qa-path", str(qa)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "--dry-run" in result.stderr or "--write" in result.stderr


def test_cli_missing_file_exits_nonzero_with_message(tmp_path: Path):
    result = subprocess.run(
        CLI_BIN + ["migrate-qa-config", "--dry-run", "--qa-path", str(tmp_path / "nope.yaml")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "not found" in result.stderr.lower()
