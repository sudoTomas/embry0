"""Onboard workflow node tests (EMB-50) — validate/route/write logic.

The analyze node is a thin run_agent_node dispatcher and the smoke node's
boot machinery (run_boot_phase) has its own suite; these tests cover the
orchestration seams: draft read-back + schema gate, retry routing, the
store write gate, and cleanup semantics.
"""

from __future__ import annotations

import pytest

from embry0.workflows.onboard.nodes import (
    MAX_ROUNDS,
    cleanup_node,
    route_after_smoke,
    route_after_validate,
    validate_node,
    write_config_node,
)

_VALID_QA_YAML = """\
version: 2
workspace_provider:
  type: static-apps
  config:
    apps:
      web: {path: "."}
defaults:
  acceptance_criteria_template:
    - "Home page renders without console errors"
apps:
  web:
    boot_command: "npm ci && npm run dev"
    frontend_url: "http://localhost:5173"
    ready_checks:
      - http: "http://localhost:5173"
        expect_status: 200
"""


class _FakeDocker:
    def __init__(self, files: dict[str, str]):
        self.files = files

    def build_exec_cmd(self, cid, cmd):
        return ["docker", "exec", cid, *cmd]

    async def run_cmd(self, cmd, timeout=None):
        target = cmd[-1]
        if target in self.files:
            return self.files[target]
        raise RuntimeError(f"cat: {target}: No such file")


def _config(docker):
    return {"configurable": {"docker": docker}}


async def test_validate_accepts_good_draft():
    docker = _FakeDocker(
        {"/workspace/.onboard/qa.yaml": _VALID_QA_YAML, "/workspace/.onboard/notes.md": "detected vite"}
    )
    state = {"onboard": {"sandbox_id": "sb-1", "round": 1}}
    out = await validate_node(state, _config(docker))
    ob = out["onboard"]
    assert ob["validated"] is True
    assert ob["qa_yaml_text"] == _VALID_QA_YAML
    assert ob["notes_md"] == "detected vite"
    assert ob["last_error"] is None


async def test_validate_missing_file_sets_feedback():
    docker = _FakeDocker({})
    state = {"onboard": {"sandbox_id": "sb-1", "round": 1}}
    out = await validate_node(state, _config(docker))
    ob = out["onboard"]
    assert ob["validated"] is False
    assert "was not written" in ob["last_error"]


async def test_validate_schema_error_sets_feedback():
    bad = _VALID_QA_YAML.replace("version: 2", "version: 3")
    docker = _FakeDocker({"/workspace/.onboard/qa.yaml": bad})
    state = {"onboard": {"sandbox_id": "sb-1", "round": 1}}
    out = await validate_node(state, _config(docker))
    ob = out["onboard"]
    assert ob["validated"] is False
    assert "schema validation failed" in ob["last_error"]


def test_route_after_validate_retries_until_rounds_exhausted():
    assert route_after_validate({"onboard": {"validated": True, "round": 1}}) == "smoke"
    assert route_after_validate({"onboard": {"validated": False, "round": 1}}) == "analyze"
    assert route_after_validate({"onboard": {"validated": False, "round": MAX_ROUNDS}}) == "cleanup"
    assert route_after_validate({"onboard": {"final_status": "failed"}}) == "cleanup"


def test_route_after_smoke_mirrors_validate_routing():
    assert route_after_smoke({"onboard": {"validated": True, "round": 1}}) == "write_config"
    assert route_after_smoke({"onboard": {"validated": False, "round": 2}}) == "analyze"
    assert route_after_smoke({"onboard": {"validated": False, "round": MAX_ROUNDS}}) == "cleanup"


class _FakeConfigsRepo:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    async def upsert(self, repo, yaml_text, updated_by=""):
        self.rows[repo] = {"repo": repo, "yaml_text": yaml_text, "updated_by": updated_by}
        return self.rows[repo]


async def test_write_config_persists_to_db():
    """RAV-1251: the onboard pipeline writes the DB, not a file store."""
    configs = _FakeConfigsRepo()
    state = {
        "repo": "acme/widgets",
        "onboard": {"validated": True, "qa_yaml_text": _VALID_QA_YAML, "round": 1},
    }
    out = await write_config_node(state, {"configurable": {"qa_configs_repo": configs}})
    ob = out["onboard"]
    assert ob["final_status"] == "completed"
    row = configs.rows["acme/widgets"]
    assert row["yaml_text"] == _VALID_QA_YAML
    assert row["updated_by"] == "onboard-pipeline"
    assert ob["store_path"] == "db:repo_qa_configs/acme/widgets"


async def test_write_config_fails_without_configs_repo():
    state = {
        "repo": "acme/widgets",
        "onboard": {"validated": True, "qa_yaml_text": _VALID_QA_YAML, "round": 1},
    }
    out = await write_config_node(state, {"configurable": {}})
    assert out["onboard"]["final_status"] == "failed"
    assert "qa_configs_repo" in out["onboard"]["failure_summary"]


async def test_write_config_refuses_unvalidated_draft():
    configs = _FakeConfigsRepo()
    state = {"repo": "acme/widgets", "onboard": {"validated": False, "last_error": "smoke failed"}}
    out = await write_config_node(state, {"configurable": {"qa_configs_repo": configs}})
    assert out["onboard"]["final_status"] == "failed"
    assert configs.rows == {}


async def test_smoke_skip_flag():
    from embry0.workflows.onboard.nodes import smoke_node

    state = {
        "skip_smoke": True,
        "onboard": {"validated": True, "qa_yaml_parsed": {}, "round": 1},
    }
    out = await smoke_node(state, {"configurable": {}})
    assert out["onboard"]["smoke"] == {"skipped": True}
    assert out["onboard"]["validated"] is True


async def test_cleanup_destroys_sandbox_and_finalizes_pending_as_failed():
    destroyed = []

    class _Sb:
        async def destroy(self, cid):
            destroyed.append(cid)

    state = {"onboard": {"sandbox_id": "sb-9", "final_status": "pending", "last_error": "boom", "round": MAX_ROUNDS}}
    out = await cleanup_node(state, {"configurable": {"sandbox_manager": _Sb()}})
    assert destroyed == ["sb-9"]
    ob = out["onboard"]
    assert ob["final_status"] == "failed"
    assert "boom" in ob["failure_summary"]


async def test_cleanup_preserves_completed_status():
    class _Sb:
        async def destroy(self, cid):
            return None

    state = {"onboard": {"sandbox_id": "sb-9", "final_status": "completed"}}
    out = await cleanup_node(state, {"configurable": {"sandbox_manager": _Sb()}})
    assert out["onboard"]["final_status"] == "completed"


def test_workflow_compiles():
    from embry0.workflows.onboard.graph import OnboardWorkflow

    graph = OnboardWorkflow().compile()
    node_names = set(graph.get_graph().nodes)
    assert {"init_onboard", "analyze", "validate", "smoke", "write_config", "cleanup"} <= node_names


@pytest.mark.parametrize("bad_repo", ["../etc", "../evil", "acme", "acme/../evil"])
async def test_write_config_rejects_unsafe_repo(bad_repo):
    configs = _FakeConfigsRepo()
    state = {"repo": bad_repo, "onboard": {"validated": True, "qa_yaml_text": _VALID_QA_YAML}}
    out = await write_config_node(state, {"configurable": {"qa_configs_repo": configs}})
    assert out["onboard"]["final_status"] == "failed"
    assert "unsafe repo name" in out["onboard"]["failure_summary"]
    assert configs.rows == {}


async def test_smoke_rejects_browserless_profile():
    """A config resolving to a non-QA-image profile must fail smoke BEFORE
    any boot spend, with the pipeline's own capability wording."""
    from embry0.workflows.onboard.nodes import smoke_node
    from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

    cfg = parse_qa_yaml_v2(_VALID_QA_YAML)

    class _Profiles:
        async def get(self, name):
            return {"name": name, "base_image": "embry0-sandbox:latest"}

    class _NoBoot:
        async def create(self, *a, **kw):
            raise AssertionError("no sandbox may be created for a browserless config")

    state = {
        "job_id": "job-x",
        "repo": "acme/widgets",
        "onboard": {"validated": True, "qa_yaml_parsed": cfg.model_dump(mode="json"), "round": 1, "sandbox_id": "sb"},
    }
    out = await smoke_node(
        state,
        {"configurable": {"docker": object(), "sandbox_manager": _NoBoot(), "profiles_repo": _Profiles()}},
    )
    ob = out["onboard"]
    assert ob["validated"] is False
    assert "has no browser" in ob["last_error"]


# ── spec 3.6: onboard sandboxes follow the repo's forge ─────────────────────


class _ForgeRepos:
    def __init__(self, forge="gitlab", raises=None):
        self._forge = forge
        self._raises = raises

    async def forge_for(self, repo):
        if self._raises is not None:
            raise self._raises
        return self._forge


async def test_init_onboard_clones_the_repo_forge(monkeypatch):
    from types import SimpleNamespace

    from embry0.workflows.onboard.nodes import init_onboard_node

    created: dict = {}
    cloned: dict = {}

    class _Sb:
        async def create(self, job_id, profile=None, env=None, repo=None, forge="github", **_kw):
            created["forge"] = forge
            return "cid-1", "tok-" + "A" * 40

    class _Profiles:
        async def get(self, name):
            return {"name": "slim"}

    class _Docker:
        def _build_base_cmd(self):
            return ["docker"]

        def build_exec_cmd(self, cid, cmd):
            return ["docker", "exec", cid, *cmd]

        async def run_cmd(self, cmd, timeout=None):
            return ""

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        cloned.update(kw)
        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.workflows.qa._subtask_prep.prep_qa_sandbox_clone", fake_clone)

    state = {"job_id": "job-1", "repo": "acme/group/backend", "branch_name": "dev"}
    out = await init_onboard_node(
        state,
        {
            "configurable": {
                "docker": _Docker(),
                "sandbox_manager": _Sb(),
                "profiles_repo": _Profiles(),
                "repos_repo": _ForgeRepos("gitlab"),
                "embry0_config": SimpleNamespace(gitlab_url="https://gitlab.example.com"),
            }
        },
    )
    assert out["onboard"]["final_status"] == "pending"
    assert created["forge"] == "gitlab"
    assert cloned["forge"] == "gitlab"
    assert cloned["gitlab_url"] == "https://gitlab.example.com"


async def test_init_onboard_fails_loudly_on_an_unregistered_repo():
    from embry0.safety.error_codes import ErrorCode
    from embry0.storage.repositories.repos import UnregisteredRepoError
    from embry0.workflows.onboard.nodes import init_onboard_node

    class _Sb:
        async def create(self, *a, **kw):
            raise AssertionError("no sandbox may be created for an unregistered repo")

    class _Profiles:
        async def get(self, name):
            return {"name": "slim"}

    out = await init_onboard_node(
        {"job_id": "job-1", "repo": "org/ghost"},
        {
            "configurable": {
                "docker": object(),
                "sandbox_manager": _Sb(),
                "profiles_repo": _Profiles(),
                "repos_repo": _ForgeRepos(raises=UnregisteredRepoError("org/ghost is not registered")),
            }
        },
    )
    assert out["onboard"]["final_status"] == "failed"
    assert out["onboard"]["failure_summary"].startswith(ErrorCode.REPO_UNREGISTERED)


async def test_smoke_clones_the_repo_forge(monkeypatch):
    from types import SimpleNamespace

    from embry0.workflows.onboard.nodes import smoke_node
    from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

    cfg = parse_qa_yaml_v2(_VALID_QA_YAML)
    created: dict = {}
    cloned: dict = {}

    class _Sb:
        async def create(self, job_id, profile=None, env=None, repo=None, forge="github", **_kw):
            created["forge"] = forge
            return "cid-smoke", "tok-" + "A" * 40

        async def destroy(self, cid):
            return None

    class _Profiles:
        async def get(self, name):
            return {"name": name, "base_image": "embry0-sandbox-qa:latest"}

    class _Docker:
        def _build_base_cmd(self):
            return ["docker"]

        def build_exec_cmd(self, cid, cmd):
            return ["docker", "exec", cid, *cmd]

        async def run_cmd(self, cmd, timeout=None):
            return ""

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        cloned.update(kw)
        return ClonedSandbox(head_sha="abc123")

    async def fake_boot(**kw):
        return SimpleNamespace(outcome="passed", error_message="", failed_checks=[])

    monkeypatch.setattr("embry0.workflows.qa._subtask_prep.prep_qa_sandbox_clone", fake_clone)
    monkeypatch.setattr("embry0.workflows.qa.boot.run_boot_phase", fake_boot)

    state = {
        "job_id": "job-1",
        "repo": "acme/group/backend",
        "branch_name": "dev",
        "onboard": {"validated": True, "qa_yaml_parsed": cfg.model_dump(mode="json"), "round": 1, "sandbox_id": "sb"},
    }
    out = await smoke_node(
        state,
        {
            "configurable": {
                "docker": _Docker(),
                "sandbox_manager": _Sb(),
                "profiles_repo": _Profiles(),
                "repos_repo": _ForgeRepos("gitlab"),
                "embry0_config": SimpleNamespace(gitlab_url="https://gitlab.example.com"),
            }
        },
    )
    assert out["onboard"]["smoke"]["failures"] == []
    assert created["forge"] == "gitlab"
    assert cloned["forge"] == "gitlab"
    assert cloned["gitlab_url"] == "https://gitlab.example.com"


async def test_smoke_fails_loudly_on_an_unregistered_repo():
    from embry0.safety.error_codes import ErrorCode
    from embry0.storage.repositories.repos import UnregisteredRepoError
    from embry0.workflows.onboard.nodes import smoke_node
    from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

    cfg = parse_qa_yaml_v2(_VALID_QA_YAML)

    class _Sb:
        async def create(self, *a, **kw):
            raise AssertionError("no sandbox may be created for an unregistered repo")

    class _Profiles:
        async def get(self, name):
            return {"name": name, "base_image": "embry0-sandbox-qa:latest"}

    out = await smoke_node(
        {
            "job_id": "job-1",
            "repo": "org/ghost",
            "onboard": {
                "validated": True,
                "qa_yaml_parsed": cfg.model_dump(mode="json"),
                "round": 1,
                "sandbox_id": "sb",
            },
        },
        {
            "configurable": {
                "docker": object(),
                "sandbox_manager": _Sb(),
                "profiles_repo": _Profiles(),
                "repos_repo": _ForgeRepos(raises=UnregisteredRepoError("org/ghost is not registered")),
            }
        },
    )
    assert out["onboard"]["final_status"] == "failed"
    assert out["onboard"]["failure_summary"].startswith(ErrorCode.REPO_UNREGISTERED)


async def test_init_onboard_defaults_to_github_without_a_registry(monkeypatch):
    """No repos_repo wired (legacy graphs): github, and no gitlab_url."""
    from embry0.workflows.onboard.nodes import init_onboard_node

    created: dict = {}
    cloned: dict = {}

    class _Sb:
        async def create(self, job_id, profile=None, env=None, repo=None, forge="github", **_kw):
            created["forge"] = forge
            return "cid-1", "tok-" + "A" * 40

    class _Profiles:
        async def get(self, name):
            return {"name": "slim"}

    class _Docker:
        def _build_base_cmd(self):
            return ["docker"]

        def build_exec_cmd(self, cid, cmd):
            return ["docker", "exec", cid, *cmd]

        async def run_cmd(self, cmd, timeout=None):
            return ""

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        cloned.update(kw)
        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.workflows.qa._subtask_prep.prep_qa_sandbox_clone", fake_clone)

    out = await init_onboard_node(
        {"job_id": "job-1", "repo": "acme/widgets"},
        {"configurable": {"docker": _Docker(), "sandbox_manager": _Sb(), "profiles_repo": _Profiles()}},
    )
    assert out["onboard"]["final_status"] == "pending"
    assert created["forge"] == "github"
    assert cloned["forge"] == "github"
    assert cloned["gitlab_url"] == ""


async def test_write_config_accepts_a_nested_repo_path():
    """Spec 3.5: a GitLab subgroup path must survive config persistence."""
    configs = _FakeConfigsRepo()
    state = {
        "repo": "acme/group/backend",
        "onboard": {"validated": True, "qa_yaml_text": _VALID_QA_YAML, "round": 1},
    }
    out = await write_config_node(state, {"configurable": {"qa_configs_repo": configs}})
    assert out["onboard"]["final_status"] == "completed"
    assert "acme/group/backend" in configs.rows


async def test_analyze_shows_the_agent_this_deployments_profiles(monkeypatch):
    """The agent cannot know a deployment's profile names; analyze must list them.

    Built on the real SandboxProfilesRepository so a call to a method it does
    not have fails here as it did in production, where a swallowed
    AttributeError left the agent guessing a profile name for three rounds.
    """
    from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository
    from embry0.workflows.onboard.nodes import analyze_node

    class _Db:
        async def fetch(self, query, *args):
            return [
                {
                    "name": "qa-external",
                    "base_image": "embry0-sandbox-qa:latest",
                    "extra_networks": ["sandbox-internet"],
                },
                {"name": "slim", "base_image": "embry0-sandbox:latest", "extra_networks": ["sandbox-internet"]},
            ]

    class _Defs:
        def __init__(self, db):
            pass

        async def get(self, agent_type):
            return {"agent_type": agent_type}

    prompts: list[str] = []

    async def fake_run_agent_node(**kwargs):
        prompts.append(kwargs["prompt"])
        return {"agent_outputs": []}

    monkeypatch.setattr("embry0.storage.repositories.agent_definitions.AgentDefinitionsRepository", _Defs)
    monkeypatch.setattr("embry0.orchestration.nodes.agent.run_agent_node", fake_run_agent_node)

    await analyze_node(
        {"job_id": "job-1", "repo": "acme/widgets", "onboard": {"round": 0}},
        {
            "configurable": {
                "agent_runner": object(),
                "db": object(),
                "profiles_repo": SandboxProfilesRepository(_Db()),
            }
        },
    )

    assert prompts, "analyze never ran the agent"
    assert "- qa-external: QA-capable (has browser)" in prompts[0], (
        f"the agent was not shown this deployment's profiles:\n{prompts[0]}"
    )
    assert "- slim: boot-only, NO browser" in prompts[0]


@pytest.mark.asyncio
async def test_init_onboard_emits_the_pipeline_graph_first(monkeypatch):
    """spec 3.8: the onboarding job page no longer shows an empty map."""
    from types import SimpleNamespace

    from embry0.workflows.onboard import nodes as onboard_nodes
    from embry0.workflows.onboard.nodes import init_onboard_node

    events_out: list[dict] = []
    monkeypatch.setattr(onboard_nodes, "_writer", lambda: events_out.append)

    class _Sb:
        async def create(self, job_id, profile=None, env=None, repo=None, forge="github", **_kw):
            return "cid-1", "tok-" + "A" * 40

    class _Profiles:
        async def get(self, name):
            return {"name": "slim"}

    class _Docker:
        def _build_base_cmd(self):
            return ["docker"]

        def build_exec_cmd(self, cid, cmd):
            return ["docker", "exec", cid, *cmd]

        async def run_cmd(self, cmd, timeout=None):
            return ""

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.workflows.qa._subtask_prep.prep_qa_sandbox_clone", fake_clone)
    await init_onboard_node(
        {"job_id": "job-1", "repo": "acme/group/backend", "branch_name": "dev"},
        {
            "configurable": {
                "docker": _Docker(),
                "sandbox_manager": _Sb(),
                "profiles_repo": _Profiles(),
                "repos_repo": _ForgeRepos("gitlab"),
                "embry0_config": SimpleNamespace(gitlab_url="https://gitlab.example.com"),
            }
        },
    )
    types = [e["type"] for e in events_out]
    assert "pipeline_graph" in types, "init_onboard must emit the graph"
    assert types.index("node_started") < types.index("pipeline_graph")
    graph = next(e for e in events_out if e["type"] == "pipeline_graph")
    assert graph["node"] == "init_onboard" and graph["graph"]["graph_id"] == "onboard-job-1"


def _node_events(events_out: list[dict], node: str) -> list[tuple[str, object]]:
    return [(e["type"], e.get("ok")) for e in events_out if e.get("node") == node and e["type"].startswith("node_")]


async def test_write_config_and_cleanup_light_their_graph_nodes(monkeypatch):
    """#40: the onboarding map's last two nodes stayed pending after the job ended."""
    from embry0.workflows.onboard import nodes as onboard_nodes

    events_out: list[dict] = []
    monkeypatch.setattr(onboard_nodes, "_writer", lambda: events_out.append)
    state = {
        "repo": "acme/widgets",
        "onboard": {"validated": True, "qa_yaml_text": _VALID_QA_YAML, "round": 1},
    }
    await write_config_node(state, {"configurable": {"qa_configs_repo": _FakeConfigsRepo()}})
    assert _node_events(events_out, "write_config") == [("node_started", None), ("node_completed", True)], (
        "write_config brackets its work with node_started and node_completed(ok=True)"
    )

    await cleanup_node({"onboard": {"final_status": "completed"}}, {"configurable": {}})
    assert [t for t, _ok in _node_events(events_out, "cleanup")] == ["node_started", "node_completed"], (
        "cleanup brackets its work with node_started and node_completed"
    )


async def test_a_failed_write_config_completes_its_node_not_ok(monkeypatch):
    from embry0.workflows.onboard import nodes as onboard_nodes

    events_out: list[dict] = []
    monkeypatch.setattr(onboard_nodes, "_writer", lambda: events_out.append)
    state = {"repo": "acme/widgets", "onboard": {"validated": True, "qa_yaml_text": _VALID_QA_YAML, "round": 1}}
    await write_config_node(state, {"configurable": {}})
    assert _node_events(events_out, "write_config") == [("node_started", None), ("node_completed", False)], (
        "a failing write_config still closes its node, with ok=False"
    )
