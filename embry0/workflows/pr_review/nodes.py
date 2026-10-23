"""pr-review workflow nodes (#30).

init → review → publish → cleanup, every edge unconditional. A node that
finds ``pr_review.final_status == "failed"`` does nothing, so a failure
anywhere still reaches cleanup, which destroys the sandbox. The executor's
runner destroys it again from the last known state if the graph raised.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from embry0.execution import events
from embry0.safety.error_codes import ErrorCode
from embry0.workflows.pipeline_graphs import pr_review_pipeline_graph
from embry0.workflows.pr_review import publish as _publish
from embry0.workflows.pr_review.agent_seed import AGENT_TYPE, PR_REVIEWER_AGENT_SEED

logger = structlog.get_logger(__name__)

DEFAULT_PROFILE = "slim"  # carries sandbox-internet, so the checks can install dependencies
CHECKOUT_TIMEOUT_SECONDS = 600
REVIEW_MAX_TURNS = 80


def _writer() -> Callable[[dict[str, Any]], Any]:
    try:
        return get_stream_writer()
    except RuntimeError:  # outside a LangGraph runnable (unit tests)
        return lambda _event: None


def _configurable(config: RunnableConfig | None) -> dict[str, Any]:
    raw = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    return raw if isinstance(raw, dict) else {}


def _review(state: dict[str, Any]) -> dict[str, Any]:
    return dict(state.get("pr_review") or {})


def _runs_nothing(rv: dict[str, Any]) -> bool:
    """A fork is reviewed read-only whatever the depth: its code is untrusted."""
    return rv.get("depth") == "read_only" or bool(rv.get("from_fork"))


def _failed(rv: dict[str, Any], code: str, summary: str) -> dict[str, Any]:
    rv.update({"final_status": "failed", "error_code": code, "failure_summary": summary[:4000]})
    logger.warning("pr_review_failed", review_id=rv.get("review_id"), error_code=code, summary=summary[:500])
    _writer()(events.error(message=summary[:1000]))
    return {"pr_review": rv}


async def _repo_preferences(configurable: dict[str, Any], repo: str) -> dict[str, Any]:
    prefs_repo = configurable.get("repo_preferences_repo")
    if prefs_repo is None:
        return {}
    try:
        row = await prefs_repo.get(repo)
    except Exception:
        logger.warning("pr_review_preferences_unavailable", repo=repo, exc_info=True)
        return {}
    return dict(row) if isinstance(row, dict) else {}


async def _previous_review(configurable: dict[str, Any], rv: dict[str, Any], job_id: str) -> dict[str, Any] | None:
    """The review this one continues: the newest posted review of the same
    pull request that left a session (copied to this job), else the newest
    posted review, for its findings as text. Never blocks the review."""
    reviews = configurable.get("pr_reviews_repo")
    sessions = configurable.get("agent_sessions_repo")
    if reviews is None:
        return None
    try:
        rows = await reviews.previous_reviews(
            str(rv.get("repo") or ""), int(rv.get("number") or 0), exclude_job_id=job_id
        )
        if not rows:
            return None
        chosen, copied = rows[0], False
        if sessions is not None:
            for row in rows:
                donor = row.get("job_id")
                session = await sessions.get(job_id=str(donor), agent_type=AGENT_TYPE) if donor else None
                if session is None:
                    continue
                await sessions.upsert(
                    job_id=job_id,
                    agent_type=AGENT_TYPE,
                    mode=session["mode"],
                    session_id=session.get("session_id"),
                    messages=session.get("messages"),
                    session_blob=session.get("session_blob"),
                )
                chosen, copied = row, True
                logger.info("pr_review_session_adopted", job_id=job_id, donor_job_id=donor)
                break
        return {
            "job_id": chosen.get("job_id"),
            "head_sha": str(chosen.get("head_sha") or ""),
            "verdict": chosen.get("verdict"),
            "session_copied": copied,
        }
    except Exception:
        logger.warning("pr_review_previous_lookup_failed", job_id=job_id, exc_info=True)
        return None


async def init_pr_review_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Create the review sandbox, clone the base through the git-proxy and check out the exact head."""
    from embry0.services.forge_resolver import resolve_forge
    from embry0.storage.repositories.repos import UnregisteredRepoError
    from embry0.workflows.issue_to_pr.nodes._common import _filter_user_env_for_sandbox
    from embry0.workflows.pr_review.checkout import (
        FILES_NUL,
        build_checkout_script,
        head_fetch_ref,
        parse_changed_paths,
        parse_checkout_output,
    )
    from embry0.workflows.qa._subtask_prep import prep_qa_sandbox_clone

    rv = _review(state)
    configurable = _configurable(config)
    docker = configurable.get("docker")
    sandbox_mgr = configurable.get("sandbox_manager")
    profiles_repo = configurable.get("profiles_repo")
    proxy_mgr = configurable.get("proxy_manager")
    job_id = state.get("job_id")
    repo = state.get("repo")
    if docker is None or sandbox_mgr is None or profiles_repo is None or not isinstance(job_id, str) or not job_id:
        return _failed(rv, ErrorCode.PR_REVIEW_INIT_FAILED.value, "init_pr_review is missing its dependencies or state")
    if not isinstance(repo, str) or not repo:
        return _failed(rv, ErrorCode.PR_REVIEW_INIT_FAILED.value, "init_pr_review has no repository")

    writer = _writer()
    writer(events.node_started(node="init_pr_review"))
    writer(events.pipeline_graph(graph=pr_review_pipeline_graph(job_id), node="init_pr_review"))

    prefs = await _repo_preferences(configurable, repo)
    profile_name = str(prefs.get("sandbox_profile") or DEFAULT_PROFILE)
    profile = await profiles_repo.get(profile_name)
    if profile is None:
        return _failed(rv, ErrorCode.PR_REVIEW_INIT_FAILED.value, f"sandbox profile {profile_name!r} does not exist")
    try:
        forge = await resolve_forge(configurable.get("repos_repo"), repo, job_id=job_id)
    except UnregisteredRepoError as exc:
        return _failed(rv, ErrorCode.REPO_UNREGISTERED.value, f"{ErrorCode.REPO_UNREGISTERED}: {exc}")

    git_proxy_url = getattr(proxy_mgr, "git_proxy_url", "") if proxy_mgr else ""
    env: dict[str, str] = {"EMBRY0_GIT_PROXY_URL": git_proxy_url} if git_proxy_url else {}
    # Code a job runs can read its sandbox's environment (ADR 0008): a fork,
    # and a depth that runs nothing, get none of the repo's variables.
    if not rv.get("from_fork") and rv.get("depth") != "read_only" and state.get("user_env_vars"):
        env.update(_filter_user_env_for_sandbox(state["user_env_vars"], qa_active=False))
    try:
        container_id, sandbox_token = await sandbox_mgr.create(
            job_id,
            profile=profile,
            env=env,
            repo=repo,
            forge=forge,
            llm_providers=tuple(configurable.get("llm_providers") or ()),
        )
    except Exception as exc:  # noqa: BLE001
        return _failed(rv, ErrorCode.PR_REVIEW_INIT_FAILED.value, f"the review sandbox could not be created: {exc}")
    rv["sandbox_id"] = container_id

    previous = await _previous_review(configurable, rv, job_id) if rv.get("trigger") == "command" else None
    try:
        await prep_qa_sandbox_clone(
            docker=docker,
            proxy_mgr=proxy_mgr,
            container_id=container_id,
            sandbox_token=sandbox_token,
            job_id=job_id,
            repo=repo,
            branch=str(rv.get("base_ref") or ""),
            is_dind=False,
            qa_net="",
            prefs_repo=configurable.get("repo_preferences_repo"),
            forge=forge,
            gitlab_url=getattr(configurable.get("embry0_config"), "gitlab_url", "") or "",
            forge_credential=(
                sandbox_mgr.last_forge_credential(container_id)
                if hasattr(sandbox_mgr, "last_forge_credential")
                else None
            ),
        )
        script = build_checkout_script(
            fetch_ref=head_fetch_ref(
                forge=str(rv.get("forge") or forge),
                number=int(rv.get("number") or 0),
                head_ref=str(rv.get("head_ref") or ""),
                from_fork=bool(rv.get("from_fork")),
            ),
            head_sha=str(rv.get("head_sha") or ""),
            base_ref=str(rv.get("base_ref") or ""),
            previous_sha=str((previous or {}).get("head_sha") or ""),
            runs_nothing=_runs_nothing(rv),
        )
        out = await docker.run_cmd(
            docker.build_exec_cmd(container_id, ["bash", "-c", script]), timeout=CHECKOUT_TIMEOUT_SECONDS
        )
        listing = await docker.run_cmd(docker.build_exec_cmd(container_id, ["cat", FILES_NUL]), timeout=30)
    except Exception as exc:  # noqa: BLE001
        failed = _failed(rv, ErrorCode.PR_REVIEW_INIT_FAILED.value, f"checking out the pull request failed: {exc}")
        return {**failed, "sandbox_container_id": container_id}
    checkout = parse_checkout_output(str(out or ""))

    rv.update(
        {
            "final_status": "pending",
            "changed_files": parse_changed_paths(str(listing or "")),
            "delta": checkout.delta if checkout else "none",
            "base_sha": checkout.base_sha if checkout else "",
            "previous": previous,
            "dev_checks": list(prefs.get("dev_checks") or []),
        }
    )
    writer(events.node_completed(node="init_pr_review"))
    return {"pr_review": rv, "sandbox_container_id": container_id, "current_stage": "init_complete"}


async def _load_session(sessions: Any, job_id: str) -> Any:
    from embry0.agents.session import AgentSession

    if sessions is None:
        return None
    try:
        row = await sessions.get(job_id=job_id, agent_type=AGENT_TYPE)
    except Exception:
        logger.warning("pr_review_session_restore_failed", job_id=job_id, exc_info=True)
        return None
    if row is None:
        return None
    return AgentSession(
        job_id=row["job_id"],
        agent_type=row["agent_type"],
        mode=row["mode"],
        messages=row.get("messages"),
        session_id=row.get("session_id"),
        session_blob=row.get("session_blob"),
    )


async def _persist_session(sessions: Any, job_id: str, resume: Any, last: Any, credentials: dict[str, str]) -> None:
    from embry0.agents.session import merged_resume_messages

    if sessions is None or not last or last.get("is_error"):
        return
    messages = merged_resume_messages(resume, last.get("messages"))
    session_id = last.get("session_id")
    if not (messages or session_id):
        return
    mode = last.get("mode") or ("claude_max" if credentials.get("oauth_token") else "anthropic_api")
    try:
        await sessions.upsert(
            job_id=job_id,
            agent_type=AGENT_TYPE,
            mode=mode,
            messages=messages,
            session_id=session_id,
            session_blob=last.get("session_blob"),
        )
    except Exception:
        logger.warning("pr_review_session_persist_failed", job_id=job_id, exc_info=True)


def _without_bash_override(state: dict[str, Any]) -> dict[str, Any]:
    """``state`` with Bash taken out of any ``agent_tools`` override for the reviewer.

    ``resolve_agent_invocation`` lets ``pipeline_config.agent_tools`` replace
    the definition's tools, so removing Bash from the definition alone would
    let an override bring it back.
    """
    pipeline_config = state.get("pipeline_config")
    if not isinstance(pipeline_config, dict):
        return state
    overrides = pipeline_config.get("agent_tools")
    if not isinstance(overrides, dict) or AGENT_TYPE not in overrides:
        return state
    tools = overrides[AGENT_TYPE]
    kept = {k: v for k, v in overrides.items() if k != AGENT_TYPE}
    if isinstance(tools, list):
        kept[AGENT_TYPE] = [t for t in tools if t != "Bash"]
    return {**state, "pipeline_config": {**pipeline_config, "agent_tools": kept}}


async def _prepare_review(
    state: dict[str, Any], rv: dict[str, Any], config: RunnableConfig, sessions: Any, job_id: str
) -> tuple[dict[str, Any], str, Any, dict[str, Any]]:
    """The reviewer's definition, prompt, resume session and the state it runs with."""
    from embry0.agents.session import session_resumable
    from embry0.orchestration.nodes.agent import load_agent_definition
    from embry0.workflows.pr_review.prompts import (
        PromptContext,
        build_delta_prompt,
        build_full_prompt,
        build_rewritten_prompt,
        format_previous_findings,
        select_dev_checks,
    )

    depth = str(rv.get("depth") or "full")
    runs_nothing = _runs_nothing(rv)
    definition = await load_agent_definition(config, AGENT_TYPE, dict(PR_REVIEWER_AGENT_SEED))
    if runs_nothing:
        definition = {**definition, "tools": [t for t in definition.get("tools") or [] if t != "Bash"]}
    changed = tuple(str(f) for f in rv.get("changed_files") or [])
    dev_checks = rv.get("dev_checks") or []
    ctx = PromptContext(
        repo=str(state.get("repo") or ""),
        number=int(rv.get("number") or 0),
        url=str(rv.get("url") or ""),
        title=str(rv.get("title") or ""),
        body=str(rv.get("body") or ""),
        author=str(rv.get("author") or ""),
        base_ref=str(rv.get("base_ref") or ""),
        base_sha=str(rv.get("base_sha") or ""),
        head_ref=str(rv.get("head_ref") or ""),
        head_sha=str(rv.get("head_sha") or ""),
        depth=depth,
        from_fork=bool(rv.get("from_fork")),
        changed_files=changed,
        checks=tuple(select_dev_checks(dev_checks, changed, "read_only" if runs_nothing else depth)),
    )
    previous = rv.get("previous") or {}
    resume = await _load_session(sessions, job_id) if previous.get("session_copied") else None
    if session_resumable(resume):
        previous_sha = str(previous.get("head_sha") or "")
        if rv.get("delta") == "yes":
            prompt = build_delta_prompt(ctx, previous_sha=previous_sha)
        else:
            prompt = build_rewritten_prompt(ctx, previous_sha=previous_sha)
    else:
        resume = None
        findings = (
            format_previous_findings(previous.get("verdict"), str(previous.get("head_sha") or "")) if previous else ""
        )
        prompt = build_full_prompt(ctx, previous_findings=findings)

    return definition, prompt, resume, _without_bash_override(state) if runs_nothing else state


async def review_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Run the pr-reviewer agent and read its verdict."""
    from embry0.orchestration.nodes.agent import agent_attempt, run_agent_node
    from embry0.safety.redaction import redact_bounded
    from embry0.workflows.pr_review.prompts import REVIEW_TIMEOUT_SECONDS
    from embry0.workflows.pr_review.verdict import parse_verdict

    rv = _review(state)
    if rv.get("final_status") == "failed":
        return {}
    configurable = _configurable(config)
    agent_runner = configurable.get("agent_runner")
    if agent_runner is None:
        return _failed(rv, ErrorCode.AGENT_RUNNER_UNAVAILABLE.value, "no agent runner is configured")
    credentials = configurable.get("credentials") or {}
    sessions = configurable.get("agent_sessions_repo")
    job_id = str(state.get("job_id") or "")
    writer = _writer()
    attempt = agent_attempt(state, AGENT_TYPE)
    writer(events.node_started(node="review", agent=AGENT_TYPE, attempt=attempt))

    try:
        definition, prompt, resume, run_state = await _prepare_review(state, rv, config, sessions, job_id)
        result = await run_agent_node(
            state=run_state,
            agent_runner=agent_runner,
            agent_type=AGENT_TYPE,
            node="review",
            prompt=prompt,
            agent_definition=definition,
            max_turns=REVIEW_MAX_TURNS,
            timeout_seconds=REVIEW_TIMEOUT_SECONDS,
            on_event=writer,
            credentials=credentials,
            config=config,
            resume_session=resume,
        )
    except Exception as exc:  # noqa: BLE001 — a crashed run must still reach cleanup
        crash = redact_bounded(f"{type(exc).__name__}: {exc}", 2000)
        return _failed(rv, ErrorCode.PR_REVIEW_NO_VERDICT.value, f"the pull request reviewer crashed: {crash}")
    outputs = result.get("agent_outputs") or []
    last = outputs[-1] if outputs else None
    await _persist_session(sessions, job_id, resume, last, credentials)
    writer(events.node_completed(node="review", agent=AGENT_TYPE, attempt=attempt))

    if not last or last.get("is_error"):
        code = str(result.get("error_code") or (last or {}).get("error_code") or ErrorCode.PR_REVIEW_NO_VERDICT.value)
        error = str((last or {}).get("error_message") or "no output")
        return {**result, **_failed(rv, code, f"the pull request reviewer failed: {error}")}
    output = str(last.get("output") or "")
    verdict = parse_verdict(output)
    if verdict is None:
        rv["raw_output_tail"] = redact_bounded(output[-4000:], 2000)
        failed = _failed(
            rv,
            ErrorCode.PR_REVIEW_NO_VERDICT.value,
            "the reviewer's final message held no readable verdict; nothing was posted",
        )
        return {**result, **failed}
    rv["verdict"] = verdict.to_dict()
    return {**result, "pr_review": rv}


def _github_client(configurable: dict[str, Any]) -> Any:
    from embry0.execution.github_client import GitHubClient

    return GitHubClient(
        default_token=str(configurable.get("github_token") or ""),
        credential_resolver=configurable.get("credential_resolver"),
    )


def _gitlab_client(configurable: dict[str, Any]) -> Any:
    from embry0.execution.gitlab_client import make_gitlab_client

    return make_gitlab_client(
        configurable.get("embry0_config"), credential_resolver=configurable.get("credential_resolver")
    )


def _publish_failure(exc: Exception, forge: str) -> str:
    """What went wrong, in the forge's words where it gave any."""
    import httpx

    from embry0.execution.forge_client import forge_write
    from embry0.safety.redaction import redact_bounded

    name = "GitLab" if forge == "gitlab" else "GitHub"
    if isinstance(exc, _publish.PublishError):
        text = str(exc)
    elif isinstance(exc, httpx.HTTPStatusError):
        answer = forge_write(exc.response)
        text = f"{name} answered {answer.status_code} for {exc.request.method} {exc.request.url.path}: {answer.detail}"
    elif isinstance(exc, httpx.HTTPError):
        text = f"{name} could not be reached ({type(exc).__name__}): {exc}"
    else:
        text = f"{type(exc).__name__}: {exc}"
    return redact_bounded(text, 2000)


def _job_url(configurable: dict[str, Any], job_id: str) -> str:
    """The job's dashboard page, or "" when the dashboard URL is unset or loopback.

    ``dashboard_public_url`` defaults to ``http://localhost:8200``, and a pull
    request's page may be public: a local address there is a dead link that
    tells its readers about the deployment.
    """
    from embry0.services.linear_sync import public_dashboard_url

    base = public_dashboard_url(str(getattr(configurable.get("embry0_config"), "dashboard_public_url", "") or ""))
    return f"{base}/jobs/{job_id}" if base else ""


async def _record_outcome(configurable: dict[str, Any], rv: dict[str, Any], *, posted: bool) -> None:
    reviews = configurable.get("pr_reviews_repo")
    review_id = rv.get("review_id")
    if reviews is None or not review_id:
        return
    try:
        await reviews.record_outcome(str(review_id), verdict=rv.get("verdict"), posted=posted)
    except Exception:
        logger.warning("pr_review_outcome_record_failed", review_id=review_id, exc_info=True)


async def publish_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Post the review from the orchestrator, with the forge token."""
    from embry0.workflows.pr_review.summary import SummaryContext
    from embry0.workflows.pr_review.verdict import Verdict

    rv = _review(state)
    if rv.get("final_status") == "failed":
        return {}
    configurable = _configurable(config)
    verdict = Verdict.from_dict(rv.get("verdict"))
    if verdict is None:
        return _failed(rv, ErrorCode.PR_REVIEW_NO_VERDICT.value, "there is no verdict to publish")
    job_id = str(state.get("job_id") or "")
    head_sha = str(rv.get("head_sha") or "")
    ctx = SummaryContext(
        job_id=job_id,
        head_sha=head_sha,
        job_url=_job_url(configurable, job_id),
        from_fork=bool(rv.get("from_fork")),
        depth=str(rv.get("depth") or "full"),
    )
    writer = _writer()
    writer(events.node_started(node="publish"))
    repo = str(state.get("repo") or "")
    number = int(rv.get("number") or 0)
    forge = str(rv.get("forge") or "github")
    try:
        if forge == "gitlab":
            result = await _publish.publish_gitlab(
                _gitlab_client(configurable), repo=repo, number=number, head_sha=head_sha, verdict=verdict, ctx=ctx
            )
        else:
            result = await _publish.publish_github(
                _github_client(configurable), repo=repo, number=number, head_sha=head_sha, verdict=verdict, ctx=ctx
            )
    except Exception as exc:  # noqa: BLE001 — PublishError, an httpx error from a forge call, or a client setup error
        # On GitLab each inline comment is its own request, so some may have
        # landed before the summary was refused: those are on the pull request.
        landed = exc.inline_posted if isinstance(exc, _publish.PublishError) else 0
        summary = f"publishing the review failed: {_publish_failure(exc, forge)}"
        if landed:
            rv["inline_count"] = landed
            noun = "inline comment was" if landed == 1 else "inline comments were"
            summary += f" ({landed} {noun} posted before it failed)"
            logger.warning("pr_review_partially_posted", review_id=rv.get("review_id"), inline_posted=landed)
        await _record_outcome(configurable, rv, posted=landed > 0)
        return _failed(rv, ErrorCode.PR_REVIEW_PUBLISH_FAILED.value, summary)
    await _record_outcome(configurable, rv, posted=True)
    rv.update({"final_status": "completed", "inline_count": result.inline_count, "listed_count": result.listed_count})
    writer(
        events.progress(
            message=f"review posted: {result.inline_count} inline, {result.listed_count} in the summary", node="publish"
        )
    )
    writer(events.node_completed(node="publish", ok=True))
    return {"pr_review": rv, "current_stage": "published"}


async def cleanup_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Destroy the review sandbox. Always runs."""
    rv = _review(state)
    writer = _writer()
    writer(events.node_started(node="cleanup"))
    sandbox_mgr = _configurable(config).get("sandbox_manager")
    sandbox_id = rv.get("sandbox_id") or state.get("sandbox_container_id")
    if sandbox_mgr is not None and sandbox_id:
        try:
            await sandbox_mgr.destroy(sandbox_id)
        except Exception:  # noqa: BLE001
            logger.warning("pr_review_sandbox_cleanup_failed", sandbox_id=sandbox_id, exc_info=True)
    if rv.get("final_status") not in ("completed", "failed"):
        rv.update(
            {
                "final_status": "failed",
                "error_code": ErrorCode.PR_REVIEW_NO_VERDICT.value,
                "failure_summary": "the review ended before its verdict was published",
            }
        )
    writer(events.node_completed(node="cleanup"))
    return {"pr_review": rv, "sandbox_container_id": None}
