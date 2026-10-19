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


def test_republishing_the_current_sha_leaves_the_live_tree_alone(tmp_path: Path) -> None:
    """Running sandboxes bind-mount the tree; an unchanged re-sync must not replace it."""
    store = SkillStore(tmp_path / "skills")
    first = store.publish("mine", "a" * 40, _stage(tmp_path, "one"))
    marker = first / "marker"
    marker.write_text("live", encoding="utf-8")
    inode = first.stat().st_ino
    (store.source_dir("mine") / "current").write_text("stale\n", encoding="utf-8")
    again = store.publish("mine", "a" * 40, _stage(tmp_path, "two"))
    assert again == first and marker.read_text(encoding="utf-8") == "live", "the live tree was replaced"
    assert first.stat().st_ino == inode
    assert store.current_sha("mine") == "a" * 40, "a stale current pointer is rewritten"
    assert not (tmp_path / "staging" / "two").exists(), "the redundant staged tree is removed"


def test_mounts_enabled_sources_whose_tree_exists_whatever_the_status(tmp_path: Path) -> None:
    """A failed re-sync keeps the previous tree, so the source stays mounted."""
    from structlog.testing import capture_logs

    store = SkillStore(tmp_path / "skills")
    store.publish("mine", "a" * 40, _stage(tmp_path, "one"))
    store.publish("failed", "b" * 40, _stage(tmp_path, "two"))
    rows = [
        {"id": "mine", "plugin_name": "mine", "status": "ready", "enabled": True, "resolved_sha": "a" * 40},
        {"id": "off", "plugin_name": "off", "status": "ready", "enabled": False, "resolved_sha": "a" * 40},
        {"id": "failed", "plugin_name": "failed", "status": "error", "enabled": True, "resolved_sha": "b" * 40},
        {"id": "wiped", "plugin_name": "wiped", "status": "ready", "enabled": True, "resolved_sha": "c" * 40},
    ]
    with capture_logs() as logs:
        mounts = store.mounts(rows)
    assert [m.source_id for m in mounts] == ["mine", "failed"]
    assert mounts[0].host_path == str(tmp_path / "skills" / "mine" / ("a" * 40))
    assert mounts[0].container_path == "/opt/embry0/skills/mine"
    missing = [e for e in logs if e["event"] == "skill_source_mount_missing"]
    assert [e["source_id"] for e in missing] == ["wiped"]


def test_mounts_name_the_tree_where_the_sandbox_daemon_sees_the_store(tmp_path: Path) -> None:
    """Rootless: the daemon resolves bind sources on the host, not at the orchestrator's /skills."""
    store = SkillStore(tmp_path / "skills", daemon_root=Path("/var/lib/embry0/skills"))
    store.publish("mine", "a" * 40, _stage(tmp_path, "one"))
    assert store.tree_path("mine", "a" * 40) == tmp_path / "skills" / "mine" / ("a" * 40)
    [mount] = store.mounts([{"id": "mine", "plugin_name": "mine", "enabled": True}])
    assert mount.host_path == "/var/lib/embry0/skills/mine/" + "a" * 40, "the mount names the daemon's path"
    assert mount.container_path == "/opt/embry0/skills/mine"
