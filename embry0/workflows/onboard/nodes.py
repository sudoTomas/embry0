"""Onboard workflow nodes (EMB-50).

Graph: init_onboard → analyze → validate → smoke → write_config → END,
with validate/smoke failures routing back to analyze (error feedback in
state) up to MAX_ROUNDS attempts.

The sandbox is created once in init and reused across rounds — the repo
clone never changes; only /workspace/.onboard/qa.yaml is rewritten by the
agent. Smoke runs boot + ready checks only (embry0.workflows.qa.boot.
run_boot_phase — the exact machinery a real QA run uses) for each managed
app, in a fresh throwaway sandbox per app so an aborted boot can't poison
the analysis sandbox. The generated config only reaches the external store
(= becomes active) after schema validation AND smoke pass.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

logger = structlog.get_logger(__name__)

MAX_ROUNDS = 3
ANALYZE_TIMEOUT_SECONDS = 1800
ANALYZE_MAX_TURNS = 120


def _writer() -> Callable[[dict[str, Any]], Any]:
    try:
        return get_stream_writer()
    except RuntimeError:  # outside a LangGraph runnable (unit tests)
        return lambda _event: None


def _onboard_state(state: dict[str, Any]) -> dict[str, Any]:
    return dict(state.get("onboard") or {})


def _fail(state: dict[str, Any], summary: str) -> dict[str, Any]:
    ob = _onboard_state(state)
    ob["final_status"] = "failed"
    ob["failure_summary"] = summary
    logger.error("onboard_failed", job_id=state.get("job_id"), summary=summary)
    _writer()({"type": "error", "message": summary})
    return {"onboard": ob}


async def init_onboard_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Create the analysis sandbox and clone the target repo into it."""
    from embry0.workflows.qa._subtask_prep import prep_qa_sandbox_clone

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_mgr = configurable.get("sandbox_manager")
    profiles_repo = configurable.get("profiles_repo")
    proxy_mgr = configurable.get("proxy_manager")

    job_id = state.get("job_id")
    repo = state.get("repo")
    branch = state.get("branch_name") or "main"

    if not all([docker, sandbox_mgr, profiles_repo, job_id, repo]):
        return _fail(state, "init_onboard missing required deps or state")
    assert docker is not None and sandbox_mgr is not None and profiles_repo is not None
    assert isinstance(job_id, str) and isinstance(repo, str)

    writer = _writer()
    writer({"type": "node_started", "node": "init_onboard"})

    profile = await profiles_repo.get("slim")
    if profile is None:
        return _fail(state, "onboard bootstrap profile 'slim' not found")

    git_proxy_url = getattr(proxy_mgr, "git_proxy_url", "") if proxy_mgr else ""
    env: dict[str, str] = {}
    if git_proxy_url:
        env["EMBRY0_GIT_PROXY_URL"] = git_proxy_url

    try:
        container_id, sandbox_token = await sandbox_mgr.create(job_id, profile=profile, env=env, repo=repo)
        base: list[str] = docker._build_base_cmd() if hasattr(docker, "_build_base_cmd") else []
        await prep_qa_sandbox_clone(
            docker=docker,
            proxy_mgr=proxy_mgr,
            container_id=container_id,
            sandbox_token=sandbox_token,
            job_id=job_id,
            repo=repo,
            branch=branch,
            is_dind=False,
            qa_net="",
            base=base,
            prefs_repo=configurable.get("repo_preferences_repo"),
        )
        await docker.run_cmd(
            docker.build_exec_cmd(container_id, ["mkdir", "-p", "/workspace/.onboard"]),
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(state, f"onboard sandbox init failed: {exc}")

    ob = _onboard_state(state)
    ob.update({"sandbox_id": container_id, "round": 0, "final_status": "pending"})
    writer({"type": "node_completed", "node": "init_onboard"})
    return {"onboard": ob, "sandbox_container_id": container_id}


async def analyze_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Run the onboarding agent in the analysis sandbox (one round)."""
    from embry0.orchestration.nodes.agent import run_agent_node
    from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository

    ob = _onboard_state(state)
    if ob.get("final_status") == "failed":
        return {}

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    agent_runner = configurable.get("agent_runner")
    db = configurable.get("db")
    credentials = configurable.get("credentials") or {}
    if agent_runner is None or db is None:
        return _fail(state, "analyze requires agent_runner + db in config['configurable']")

    defs_repo = AgentDefinitionsRepository(db)
    agent_definition = await defs_repo.get("onboarding")
    if agent_definition is None:
        return _fail(state, "onboarding agent_definition missing — restart orchestrator to re-seed")

    round_n = int(ob.get("round") or 0) + 1
    repo = state.get("repo", "")
    branch = state.get("branch_name") or "main"

    prompt = (
        f"Analyze the repository {repo} (branch {branch}) cloned at /workspace "
        "and draft its qa.yaml v2 per your system prompt. Write "
        "/workspace/.onboard/qa.yaml and /workspace/.onboard/notes.md."
    )

    # This deployment's actual profiles — the agent cannot know them, and the
    # QA agent needs a browser, so any profile not built on embry0-sandbox-qa
    # fails the pipeline's capability check (orchestrator.py, EMB-34 fix #3).
    profiles_repo = configurable.get("profiles_repo")
    if profiles_repo is not None:
        try:
            rows = await profiles_repo.list_all()
        except Exception:  # noqa: BLE001
            rows = []
        if rows:
            lines = []
            for row in rows:
                base = str(row.get("base_image") or "")
                qa_ok = "QA-capable (has browser)" if "embry0-sandbox-qa" in base else "boot-only, NO browser"
                lines.append(f"- {row.get('name')}: {qa_ok}")
            prompt += (
                "\n\nSandbox profiles available on this deployment:\n"
                + "\n".join(lines)
                + "\nEvery profile the config resolves for an app MUST be QA-capable "
                "(built on embry0-sandbox-qa) — the QA agent drives a browser, and the "
                "pipeline rejects browserless profiles before spending any boot time."
            )

    feedback = ob.get("last_error")
    if feedback:
        prompt += (
            f"\n\nATTEMPT {round_n} — your previous draft was rejected. "
            "Fix the config; the errors below are ground truth:\n\n" + feedback
        )

    writer = _writer()
    writer({"type": "node_started", "node": "analyze", "round": round_n})

    try:
        out = await run_agent_node(
            state=state,
            agent_runner=agent_runner,
            agent_type="onboarding",
            prompt=prompt,
            agent_definition=agent_definition,
            max_turns=ANALYZE_MAX_TURNS,
            timeout_seconds=ANALYZE_TIMEOUT_SECONDS,
            on_event=writer,
            credentials=credentials,
            config=config,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(state, f"onboarding agent crashed: {exc}")

    ob["round"] = round_n
    writer({"type": "node_completed", "node": "analyze", "round": round_n})
    return {"onboard": ob, "agent_outputs": out.get("agent_outputs", []) or []}


async def validate_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Read the draft back from the sandbox and schema-validate it."""
    from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

    ob = _onboard_state(state)
    if ob.get("final_status") == "failed":
        return {}

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_id = ob.get("sandbox_id")
    if docker is None or not sandbox_id:
        return _fail(state, "validate missing docker or sandbox_id")

    writer = _writer()
    writer({"type": "node_started", "node": "validate"})

    try:
        yaml_text = await docker.run_cmd(
            docker.build_exec_cmd(sandbox_id, ["cat", "/workspace/.onboard/qa.yaml"]),
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        ob["last_error"] = f"/workspace/.onboard/qa.yaml was not written or is unreadable: {exc}"
        ob["validated"] = False
        writer({"type": "node_completed", "node": "validate", "ok": False})
        return {"onboard": ob}

    try:
        cfg = parse_qa_yaml_v2(yaml_text)
    except Exception as exc:  # noqa: BLE001
        ob["last_error"] = f"schema validation failed:\n{exc}"
        ob["validated"] = False
        writer({"type": "node_completed", "node": "validate", "ok": False})
        return {"onboard": ob}

    # Read notes.md best-effort — it rides into the job record for humans.
    notes = ""
    try:
        notes = await docker.run_cmd(
            docker.build_exec_cmd(sandbox_id, ["cat", "/workspace/.onboard/notes.md"]),
            timeout=10,
        )
    except Exception:  # noqa: BLE001
        pass

    ob.update(
        {
            "validated": True,
            "last_error": None,
            "qa_yaml_text": yaml_text,
            "qa_yaml_parsed": cfg.model_dump(mode="json"),
            "notes_md": notes,
        }
    )
    writer({"type": "node_completed", "node": "validate", "ok": True})
    return {"onboard": ob}


async def smoke_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Boot + ready-checks smoke for each managed app in the draft config.

    Reuses run_boot_phase — the exact boot machinery of a real QA run —
    in a fresh throwaway sandbox per app (the app's own sandbox_profile,
    fresh clone). Deployed apps get a ready-check-only liveness poll from
    the analysis sandbox. No QA agent runs — this is "ready checks only".
    """
    from embry0.workflows.qa._subtask_prep import prep_qa_sandbox_clone
    from embry0.workflows.qa.boot import run_boot_phase
    from embry0.workflows.qa.qa_yaml_resolve import resolve_app_config
    from embry0.workflows.qa.qa_yaml_v2 import QAYamlConfigV2
    from embry0.workflows.qa.subtask_state import _synth_v1_qa_yaml

    ob = _onboard_state(state)
    if ob.get("final_status") == "failed" or not ob.get("validated"):
        return {}
    if state.get("skip_smoke"):
        ob["smoke"] = {"skipped": True}
        return {"onboard": ob}

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_mgr = configurable.get("sandbox_manager")
    profiles_repo = configurable.get("profiles_repo")
    proxy_mgr = configurable.get("proxy_manager")
    job_id = state.get("job_id", "")
    repo = state.get("repo", "")
    branch = state.get("branch_name") or "main"

    if not all([docker, sandbox_mgr, profiles_repo]):
        return _fail(state, "smoke missing docker/sandbox_manager/profiles_repo")
    assert docker is not None and sandbox_mgr is not None and profiles_repo is not None

    cfg = QAYamlConfigV2.model_validate(ob["qa_yaml_parsed"])
    writer = _writer()
    writer({"type": "node_started", "node": "smoke"})

    failures: list[str] = []
    results: dict[str, str] = {}

    # Same capability rule the QA orchestrator enforces (EMB-34 fix #3): the
    # QA agent drives a browser, so a config resolving to a profile not built
    # on embry0-sandbox-qa would pass boot smoke here and then fail every real
    # QA run. Reject it now, with the pipeline's own wording, so the agent's
    # retry round fixes the profile.
    for app_name in cfg.apps:
        try:
            resolved = resolve_app_config(app_name, cfg, None)
        except (ValueError, KeyError):
            continue  # the boot loop below reports resolve errors
        profile = await profiles_repo.get(resolved.sandbox_profile)
        if profile is None:
            continue  # boot loop reports missing profiles
        base_image = str(profile.get("base_image") or "")
        if "embry0-sandbox-qa" not in base_image:
            results[app_name] = "profile_browserless"
            failures.append(
                f"app {app_name!r}: profile {resolved.sandbox_profile!r} "
                f"(base_image {base_image!r}) has no browser, but the QA agent "
                "requires Playwright — use a profile built on embry0-sandbox-qa"
            )
    if failures:
        ob["smoke"] = {"results": results, "failures": failures}
        ob["last_error"] = "smoke run rejected the config before boot:\n- " + "\n- ".join(failures)
        ob["validated"] = False
        writer({"type": "node_completed", "node": "smoke", "ok": False})
        return {"onboard": ob}

    for app_name in cfg.apps:
        try:
            resolved = resolve_app_config(app_name, cfg, None)
        except (ValueError, KeyError) as exc:
            # e.g. a managed app whose merged ready_checks is empty by omission
            # — resolve-time errors are config errors, feed them back.
            results[app_name] = "resolve_error"
            failures.append(f"app {app_name!r}: config resolve failed — {exc}")
            continue
        smoke_job_id = f"{job_id}__smoke_{app_name}"
        v1_yaml = _synth_v1_qa_yaml(resolved)

        if resolved.target == "deployed":
            # Liveness-only: poll ready checks from the analysis sandbox
            # (empty startup.command makes run_boot_phase skip the launch).
            v1_yaml["startup"]["boot_timeout_seconds"] = min(resolved.boot_timeout_seconds, 120)
            boot = await run_boot_phase(
                qa_yaml=v1_yaml,
                container_id=ob["sandbox_id"],
                docker=docker,
            )
            results[app_name] = boot.outcome
            if boot.outcome != "passed":
                failures.append(
                    f"app {app_name!r} (deployed): ready checks failed — "
                    f"{boot.error_message or boot.outcome}; failed_checks={boot.failed_checks}"
                )
            continue

        profile = await profiles_repo.get(resolved.sandbox_profile)
        if profile is None:
            failures.append(
                f"app {app_name!r}: sandbox_profile {resolved.sandbox_profile!r} does not exist on this deployment"
            )
            results[app_name] = "profile_missing"
            continue

        container_id = None
        try:
            from embry0.workflows.qa._subtask_env import build_qa_sandbox_env

            # Same env construction as a real QA sub-task sandbox — the smoke
            # boot must see the repo's env vars (scope-filtered) or apps that
            # need credentials to start would fail smoke spuriously.
            env = build_qa_sandbox_env(
                user_env_vars=state.get("user_env_vars"),
                git_proxy_url=getattr(proxy_mgr, "git_proxy_url", "") if proxy_mgr else "",
                qa_job_id=smoke_job_id,
                attempt_n=1,
                qa_network_name="",
            )
            container_id, sandbox_token = await sandbox_mgr.create(smoke_job_id, profile=profile, env=env, repo=repo)
            base: list[str] = docker._build_base_cmd() if hasattr(docker, "_build_base_cmd") else []
            await prep_qa_sandbox_clone(
                docker=docker,
                proxy_mgr=proxy_mgr,
                container_id=container_id,
                sandbox_token=sandbox_token,
                job_id=smoke_job_id,
                repo=repo,
                branch=branch,
                is_dind=False,
                qa_net="",
                base=base,
                prefs_repo=configurable.get("repo_preferences_repo"),
            )
            boot = await run_boot_phase(
                qa_yaml=v1_yaml,
                container_id=container_id,
                docker=docker,
            )
            results[app_name] = boot.outcome
            if boot.outcome != "passed":
                failures.append(
                    f"app {app_name!r}: boot/ready smoke failed ({boot.outcome}) — "
                    f"{boot.error_message or 'no detail'}; failed_checks={boot.failed_checks}; "
                    f"boot_command={resolved.boot_command!r}, "
                    f"ready_checks={[rc.http for rc in resolved.ready_checks]}"
                )
        except Exception as exc:  # noqa: BLE001
            results[app_name] = "infra_error"
            failures.append(f"app {app_name!r}: smoke infra error — {exc}")
        finally:
            if container_id:
                try:
                    await sandbox_mgr.destroy(container_id)
                except Exception:  # noqa: BLE001
                    logger.warning("onboard_smoke_sandbox_cleanup_failed", container_id=container_id)

    ob["smoke"] = {"results": results, "failures": failures}
    if failures:
        ob["last_error"] = "smoke run (boot + ready checks) failed:\n- " + "\n- ".join(failures)
        ob["validated"] = False
    writer({"type": "node_completed", "node": "smoke", "ok": not failures})
    return {"onboard": ob}


async def write_config_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Persist the validated + smoked config into the external store."""
    from embry0.workflows.qa.qa_config_store import save_external_qa_yaml

    ob = _onboard_state(state)
    if ob.get("final_status") == "failed":
        return {}
    if not ob.get("validated"):
        return _fail(state, ob.get("last_error") or "config never passed validation")

    repo = state.get("repo", "")
    try:
        path = save_external_qa_yaml(repo, ob["qa_yaml_text"])
    except Exception as exc:  # noqa: BLE001
        return _fail(state, f"config store write failed: {exc}")

    ob.update({"final_status": "completed", "store_path": str(path)})
    _writer()({"type": "progress", "message": f"qa.yaml written to config store: {path}"})
    logger.info("onboard_config_written", job_id=state.get("job_id"), repo=repo, path=str(path))
    return {"onboard": ob}


async def cleanup_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Destroy the analysis sandbox. Always runs (terminal node)."""
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    sandbox_mgr = configurable.get("sandbox_manager")
    ob = _onboard_state(state)
    sandbox_id = ob.get("sandbox_id")
    if sandbox_mgr and sandbox_id:
        try:
            await sandbox_mgr.destroy(sandbox_id)
        except Exception:  # noqa: BLE001
            logger.warning("onboard_sandbox_cleanup_failed", sandbox_id=sandbox_id)
    if ob.get("final_status") == "pending":
        # Rounds exhausted without a valid config.
        ob["final_status"] = "failed"
        ob["failure_summary"] = (
            f"no valid config after {MAX_ROUNDS} analysis rounds; last error:\n{ob.get('last_error')}"
        )
    ob["finished_at"] = time.time()
    return {"onboard": ob}


def route_after_validate(state: dict[str, Any]) -> str:
    """validate → smoke (ok) | analyze (retry) | cleanup (failed/exhausted)."""
    ob = _onboard_state(state)
    if ob.get("final_status") == "failed":
        return "cleanup"
    if ob.get("validated"):
        return "smoke"
    if int(ob.get("round") or 0) >= MAX_ROUNDS:
        return "cleanup"
    return "analyze"


def route_after_smoke(state: dict[str, Any]) -> str:
    """smoke → write_config (ok) | analyze (retry) | cleanup (failed/exhausted)."""
    ob = _onboard_state(state)
    if ob.get("final_status") == "failed":
        return "cleanup"
    if ob.get("validated"):
        return "write_config"
    if int(ob.get("round") or 0) >= MAX_ROUNDS:
        return "cleanup"
    return "analyze"
