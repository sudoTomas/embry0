"""SkillStore: the /skills layout and its atomic publish (spec 3.2)."""

from __future__ import annotations

from pathlib import Path

from embry0.skills.store import SkillStore


def _stage(tmp_path: Path, name: str) -> Path:
    staged = tmp_path / "staging" / name
    (staged / "skills" / "plan").mkdir(parents=True)
    (staged / "skills" / "plan" / "SKILL.md").write_text("---\nname: plan\n---\n", encoding="utf-8")
    return staged


def test_publish_moves_the_tree_writes_current_and_drops_older_trees(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    first = store.publish("mine", "a" * 40, _stage(tmp_path, "one"))
    assert first == store.tree_path("mine", "a" * 40) and first.is_dir()
    assert store.current_sha("mine") == "a" * 40
    second = store.publish("mine", "b" * 40, _stage(tmp_path, "two"))
    assert second.is_dir() and not first.exists(), "an older tree is removed after the new one is current"
    assert store.current_sha("mine") == "b" * 40
    assert not (tmp_path / "staging" / "two").exists(), "publish renames, it does not copy"


def test_current_sha_is_none_for_an_unknown_source(tmp_path: Path) -> None:
    assert SkillStore(tmp_path).current_sha("nothing") is None


def test_mounts_only_ready_enabled_sources_with_a_tree(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    store.publish("mine", "a" * 40, _stage(tmp_path, "one"))
    rows = [
        {"id": "mine", "plugin_name": "mine", "status": "ready", "enabled": True, "resolved_sha": "a" * 40},
        {"id": "off", "plugin_name": "off", "status": "ready", "enabled": False, "resolved_sha": "a" * 40},
        {"id": "broken", "plugin_name": "broken", "status": "error", "enabled": True, "resolved_sha": ""},
        {"id": "wiped", "plugin_name": "wiped", "status": "ready", "enabled": True, "resolved_sha": "c" * 40},
    ]
    mounts = store.mounts(rows)
    assert [m.source_id for m in mounts] == ["mine"]
    assert mounts[0].host_path == str(tmp_path / "skills" / "mine" / ("a" * 40))
    assert mounts[0].container_path == "/opt/embry0/skills/mine"
