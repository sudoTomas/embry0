"""developer node — prompts, branch verification, session adoption (RAV-1235 split of nodes.py)."""

from __future__ import annotations

import shlex
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import Command

# Shared seams (run_agent_node, get_stream_writer, interrupt,
# _verify_branch_pushed, _branch_on_origin) are resolved through the package
# namespace at CALL time so test patches on
# `embry0.workflows.issue_to_pr.nodes.<seam>` reach this module. The self-import is safe: the package __init__ binds the seams
# before importing the node modules.
import embry0.workflows.issue_to_pr.nodes as _nodes
from embry0.execution import events
from embry0.orchestration.nodes.agent import (
    BRAINSTORMING_ASK_USER_CAP,
    DEFAULT_ASK_USER_CAP,
    agent_attempt,
)
from embry0.services.slop import SLOPMASTER
from embry0.workflows.issue_to_pr.nodes._common import (
    _extract_ask_user_events,
    _format_user_answers_block,
)

logger = structlog.get_logger(__name__)


def _developer_branch_name(state: dict[str, Any]) -> str:
    """The developer's git-ref-safe working branch for this job.

    The derivation lives in ``orchestration.branch_names`` because merge sync
    re-derives the same name from the stored job row (#279).
    """
    from embry0.orchestration.branch_names import job_branch_name

    return job_branch_name(
        issue_id=state.get("issue_id", ""),
        task=state.get("task", ""),
        job_id=state.get("job_id"),
        context=state.get("context"),
    )


async def _developer_working_branch(
    state: dict[str, Any], configurable: dict[str, Any]
) -> tuple[str, dict[str, str] | None]:
    """This job's branch, and the earlier job's branch it had to avoid (#478), if any.

    The derived name is per issue, so a job dispatched again after an abandoned
    or failed one finds that job's branch still on origin, and its developer
    spent its turns reconciling two designs. On the job's first developer run
    the name is probed once; a taken name gets the job's own suffix. Every
    later run (retry, resume, answered question) reads the name back from
    state, so the job keeps the branch it started on. A follow-up job works on
    its PR's existing branch and is never probed. Neither is a job with no
    issue: its derived name already carries its job id (#558), so a branch of
    that name on origin can only be its own.
    """
    from embry0.orchestration.branch_names import shared_branch_name, taken_branch_name

    chosen = state.get("branch_name")
    if chosen:
        return str(chosen), None
    derived = _developer_branch_name(state)
    if (state.get("context") or {}).get("ref") or state.get("pr_url"):
        return derived, None
    if derived != shared_branch_name(issue_id=state.get("issue_id"), task=state.get("task")):
        return derived, None
    taken = await _nodes._branch_on_origin(
        docker=configurable.get("docker"),
        container_id=state.get("sandbox_container_id"),
        branch=derived,
    )
    if taken is None:
        return derived, None
    own = taken_branch_name(derived, state.get("job_id"))
    logger.info(
        "developer_branch_taken",
        job_id=state.get("job_id"),
        taken=derived,
        taken_sha=taken.get("sha"),
        branch=own,
    )
    return own, {"name": derived, **taken}


async def _grant_push_branch(state: dict[str, Any], configurable: dict[str, Any], branch: str) -> str:
    """Let the sandbox push ``branch``, or fall back to the branch it may already push (#559).

    init_node enrolled the sandbox for the job's own name (``job_push_branch``).
    When the developer chose another branch (the issue's shared name, found
    free on origin), the git-proxy is told to accept that one instead. If it
    cannot be told, the job keeps its own name rather than pushing to a
    branch the proxy would refuse.
    """
    from embry0.orchestration.branch_names import job_push_branch

    enrolled = job_push_branch(
        issue_id=state.get("issue_id"),
        task=state.get("task"),
        job_id=state.get("job_id"),
        context=state.get("context"),
        chosen=state.get("branch_name"),
    )
    if branch == enrolled:
        return branch
    proxy_mgr = configurable.get("proxy_manager")
    container_id = state.get("sandbox_container_id")
    if not proxy_mgr or not getattr(proxy_mgr, "git_proxy_url", "") or not container_id:
        # No git-proxy: no enrollment to change, and nothing to push through.
        return branch
    try:
        await proxy_mgr.set_git_push_branches(str(container_id), [branch])
    except Exception as exc:  # noqa: BLE001 — the job carries on, on the branch it may push
        logger.warning(
            "developer_push_branch_not_granted",
            job_id=state.get("job_id"),
            branch=branch,
            fallback=enrolled,
            error=str(exc)[:300],
        )
        return enrolled
    return branch


def _developer_delta_sections(state: dict[str, Any]) -> list[str]:
    """The one-shot context blocks that are NEW since the developer's last
    turn: user answers, review feedback, user retry guidance, and the QA
    failure addendum. Shared by the full prompt (where they extend the
    task context) and the delta prompt (where they ARE the prompt).
    """
    parts: list[str] = []
    answers_block = _format_user_answers_block(state.get("user_answers"))
    if answers_block:
        parts.append(f"\n{answers_block}")
    feedback_context = state.get("feedback_context", "")
    if feedback_context:
        parts.append(f"\nReview Feedback (address these issues):\n{feedback_context}")
    user_retry_guidance = state.get("user_retry_guidance")
    if user_retry_guidance:
        parts.append(f"\nUser guidance:\n{user_retry_guidance}")
    qa_addendum = state.get("developer_prompt_addendum")
    if qa_addendum:
        parts.append(f"\nQA follow-up (from the QA failure analysis):\n{qa_addendum}")
    focus_files = state.get("developer_focus_files") or []
    if focus_files:
        parts.append("\nFocus files:\n" + "\n".join(f"- {f}" for f in focus_files))
    # Salvage: the earlier run stopped and embry0 committed what it left.
    from embry0.workflows.issue_to_pr.nodes._salvage import prompt_section

    unfinished = prompt_section(state.get("unfinished_work"))
    if unfinished:
        parts.append(unfinished)
    return parts


def _developer_evidence_block(state: dict[str, Any], embry0_config: Any) -> str:
    """Evidence + (when granted) log-tool section of the full prompt (SH-P4.4)."""
    from embry0.workflows.issue_to_pr.evidence import format_evidence_block
    from embry0.workflows.issue_to_pr.log_tool import developer_log_tool_block

    parts = [format_evidence_block(state.get("case_evidence"))]
    containers = state.get("case_log_containers") or []
    if containers:
        from embry0.execution.proxy.context_proxy import MAX_LIMIT, MAX_SINCE_MINUTES

        parts.append(
            developer_log_tool_block(
                containers,
                quota=int(getattr(embry0_config, "context_proxy_query_quota", 30) or 30),
                since_cap=MAX_SINCE_MINUTES,
                limit_cap=MAX_LIMIT,
            )
        )
    return "\n\n".join(part for part in parts if part)


def _build_developer_full_prompt(
    state: dict[str, Any],
    branch_name: str,
    embry0_config: Any = None,
    *,
    taken_branch: dict[str, str] | None = None,
) -> str:
    """Complete task prompt for a FRESH developer conversation.

    ``taken_branch`` is the earlier job's branch this job was named away from
    (#478). The developer is told about it up front, so it does not find it
    with ``git ls-remote`` at the end of its run and spend its last turns
    deciding whose design wins.
    """
    repo = state.get("repo", "")
    task = state.get("task", "")
    issue_number = state.get("issue_number")
    triage_decision = state.get("triage_decision", {})
    additional_context = state.get("additional_context", "")

    prompt_parts = [f"Repository: {repo}"]
    if issue_number:
        prompt_parts.append(f"GitHub Issue: #{issue_number}")
    prompt_parts.append(f"\nTask:\n{task}")

    if additional_context:
        prompt_parts.append(f"\nAdditional Context:\n{additional_context}")

    prompt_parts.extend(_developer_delta_sections(state))

    # SH-P4.4: the logged failure behind the task, and the scoped log tool
    # when init_node provisioned one for this job.
    evidence_block = _developer_evidence_block(state, embry0_config)
    if evidence_block:
        prompt_parts.append(f"\n{evidence_block}")

    reasoning = triage_decision.get("reasoning", "")
    if reasoning:
        prompt_parts.append(f"\nTriage Analysis:\n{reasoning}")

    # RAV-1079: open-PR awareness. Absent for jobs that already target an
    # existing PR (the executor only injects open_prs for fresh jobs).
    from embry0.services.github_prs import format_open_prs_block

    open_prs_block = format_open_prs_block(state.get("open_prs") or [])
    if open_prs_block:
        prompt_parts.append(f"\n{open_prs_block}")
        prompt_parts.append(
            "If one of these open PRs already implements this task, do NOT "
            "push a duplicate — ask the user how to proceed via the ask_user "
            "helper below, naming the PR number."
        )

    # RAV-1079/RAV-1092: a job that already carries a PR (pr-followup whose
    # donor-session lookup failed, so no adopted prompt) must never be told
    # to open a new one — the old unconditional "Create a PR" instruction
    # here was a live duplicate-PR bug on that path.
    existing_pr_url = state.get("pr_url") or ""
    if taken_branch:
        last = " ".join(part for part in (taken_branch.get("sha"), taken_branch.get("date")) if part)
        if last and taken_branch.get("subject"):
            last += f": {taken_branch['subject']}"
        prompt_parts.append(
            f"\nBranch {taken_branch['name']} already exists on origin"
            + (f" (last commit {last})" if last else "")
            + ". An earlier embry0 job on this task pushed it. It is not yours: never push to it, "
            f"reset it, rebase it or delete it. Your branch is {branch_name}. You may read the "
            f"earlier work for reference (`git fetch origin {taken_branch['name']}`, then "
            "`git log -p FETCH_HEAD`)."
        )
    prompt_parts.append("\nInstructions:")
    if existing_pr_url:
        prompt_parts.append(
            f"1. The branch {branch_name} already exists and is checked out — work on it (do NOT create a new branch)"
        )
    else:
        prompt_parts.append(f"1. Create branch: {branch_name}")
    prompt_parts.append("2. Implement the changes")
    prompt_parts.append("3. Write or update tests")
    prompt_parts.append("4. Commit your changes")
    prompt_parts.append(
        "   If you write a design spec or plan, commit it on this branch with the code it describes: "
        "it ships in this pull request, never in one of its own."
    )
    prompt_parts.append(f"5. Push to branch {branch_name}" if existing_pr_url else "5. Push the branch")
    prompt_parts.append(
        f"   The git remote accepts pushes to {branch_name} only. It refuses any other branch or tag, "
        "and deleting a branch, so do not spend turns on them."
    )
    if existing_pr_url:
        prompt_parts.append(
            f"6. A pull request for this branch already exists ({existing_pr_url}) — do NOT create a new PR"
        )
    else:
        prompt_parts.append("6. Push done ⇒ PR handled for you (see below)")
        # RAV-1247: agents burned ~9 turns rediscovering how to authenticate
        # PR creation (job-54b89ff5c429). RAV-1282: the recipe stated here
        # (`git credential fill` + curl) was itself BLOCKED by the sandbox
        # safety policy (patterns.py: \bgit\s+credential\b), so every agent
        # that followed it burned turns getting denied. PR creation from
        # inside the sandbox is not possible — say so and lean on the
        # orchestrator fallback, which holds the token natively.
        prompt_parts.append(
            "\nAbout the PR (step 6): you CANNOT create a pull request from "
            "this sandbox — `gh` is unauthenticated, there is no GitHub "
            "token in the env, and credential-helper commands are blocked "
            "by the safety policy. Do not spend any turns trying. Push the "
            'branch, then report "pr_url": null in your final JSON — the '
            "orchestrator opens the PR automatically from your pushed "
            "branch."
        )
    dev_checks_block = _format_dev_checks_block(state.get("dev_checks"))
    if dev_checks_block:
        prompt_parts.append(dev_checks_block)
    prompt_parts.append(
        "\nIf you need information only the user can provide, ask with:\n"
        "  python -c \"from embry0.sandbox.ask_user import ask_user; ask_user('your question')\"\n"
        "then finish your turn cleanly (commit work in progress first) — the "
        "pipeline pauses, collects the answer, and re-invokes you with it. "
        "Questions asked as plain response text never reach the user."
    )
    prompt_parts.append(
        "\nReturn a JSON object at the end with: pr_url, branch, summary, files_changed. "
        "summary is the pull request body a person will read — say what changed and why."
    )
    return "\n".join(prompt_parts)


def _format_dev_checks_block(dev_checks: Any) -> str:
    """Render repo_preferences.dev_checks as the pre-PR self-check section.

    RAV-1254: replaces the in-repo .e0/dev.yaml convention — the contract is
    stored in embry0 and injected on every dispatch path. Empty/malformed
    entries render nothing (the block is advisory, never job-blocking).
    """
    if not isinstance(dev_checks, list):
        return ""
    lines: list[str] = []
    for entry in dev_checks:
        if not isinstance(entry, dict):
            continue
        command = str(entry.get("command") or "").strip()
        if not command:
            continue
        name = str(entry.get("name") or "check").strip()
        paths = [str(p).strip().strip("/") for p in entry.get("paths") or [] if str(p).strip()]
        scope = f"if you changed files under {', '.join(p + '/' for p in paths)}" if paths else "always"
        lines.append(f"- {name} ({scope}): `{command}`")
    if not lines:
        return ""
    return (
        "\nSelf-check before opening the PR (run in /workspace; fix failures "
        "and re-run until each relevant check passes):\n" + "\n".join(lines)
    )


def _build_developer_delta_prompt(state: dict[str, Any], branch_name: str) -> str:
    """Delta-only prompt for a RESUMED developer conversation (EMB-35).

    The prior session already carries the repo, task, and all earlier
    context — re-sending them re-bills the whole prompt as input and
    invalidates the CLI's cache. Send only what is new, plus a short
    closing contract so the response shape stays parseable.
    """
    prompt_parts = ["You are resuming your previous session on this task. New information since your last turn:"]
    delta = _developer_delta_sections(state)
    if delta:
        prompt_parts.extend(delta)
    else:
        prompt_parts.append("\n(no new information — continue where you left off)")
    prompt_parts.append(
        f"\nContinue the work: address the above, commit, push to branch {branch_name}, "
        "and update the PR (create one if none exists)."
    )
    prompt_parts.append(
        "\nIf you need information only the user can provide, ask with:\n"
        "  python -c \"from embry0.sandbox.ask_user import ask_user; ask_user('your question')\"\n"
        "then finish your turn cleanly (commit work in progress first) — the "
        "pipeline pauses, collects the answer, and re-invokes you with it. "
        "Questions asked as plain response text never reach the user."
    )
    prompt_parts.append(
        "\nReturn a JSON object at the end with: pr_url, branch, summary, files_changed. "
        "summary is the pull request body a person will read — say what changed and why."
    )
    return "\n".join(prompt_parts)


def _build_developer_adopted_prompt(state: dict[str, Any], branch_name: str) -> str:
    """Prompt for a session ADOPTED from the job that created the PR (RAV-1092).

    The resumed conversation carries the original implementation context, but
    NOT this job's task — the new instructions (typically a maintainer's PR
    comment) are the payload here. The workspace is a fresh clone with the PR
    branch already checked out, so the session's memory of file layout holds.
    """
    task = state.get("task", "")
    prompt_parts = [
        "You are resuming the session in which you implemented the changes on "
        f"branch {branch_name} (a fresh workspace clone has that branch checked "
        "out — your memory of the files is accurate; re-read anything you need "
        "to confirm).",
        f"\nNew instructions for this branch:\n{task}",
    ]
    prompt_parts.extend(_developer_delta_sections(state))
    prompt_parts.append(
        f"\nAddress the instructions on branch {branch_name}: implement, update "
        f"tests where relevant, commit, and push to {branch_name}. A pull "
        "request for this branch already exists — do NOT create a new PR and "
        "do NOT create a new branch."
    )
    prompt_parts.append(
        "\nIf you need information only the user can provide, ask with:\n"
        "  python -c \"from embry0.sandbox.ask_user import ask_user; ask_user('your question')\"\n"
        "then finish your turn cleanly (commit work in progress first) — the "
        "pipeline pauses, collects the answer, and re-invokes you with it. "
        "Questions asked as plain response text never reach the user."
    )
    prompt_parts.append(
        "\nReturn a JSON object at the end with: pr_url, branch, summary, files_changed. "
        "summary is the pull request body a person will read — say what changed and why."
    )
    return "\n".join(prompt_parts)


async def _branch_on_origin(
    *,
    docker: Any,
    container_id: str | None,
    branch: str,
) -> dict[str, str] | None:
    """The tip of ``branch`` on origin, or None when origin has no such branch (#478).

    ``{"sha", "date", "subject"}``; date and subject come from a one-commit
    fetch and are left out if it fails. Fail-open: when the probe cannot run
    or errors, None, and the job keeps the derived name as it always did.
    """
    if not docker or not container_id or not branch or branch == "main":
        return None
    try:
        out = await docker.run_cmd(
            docker.build_exec_cmd(container_id, ["git", "-C", "/workspace", "ls-remote", "--heads", "origin", branch]),
            timeout=15,
        )
    except Exception as exc:
        logger.warning("developer_branch_probe_failed", branch=branch, error=str(exc))
        return None
    # ls-remote matches a pattern by its tail, so take only the exact ref.
    sha = ""
    for line in (out or "").splitlines():
        found_sha, _, ref = line.partition("\t")
        if ref.strip() == f"refs/heads/{branch}":
            sha = found_sha.strip()
    if not sha:
        return None
    tip = {"sha": sha[:12]}
    ref = shlex.quote(f"refs/heads/{branch}")
    try:
        detail = await docker.run_cmd(
            docker.build_exec_cmd(
                container_id,
                [
                    "sh",
                    "-c",
                    f"git -C /workspace fetch --quiet --depth=1 origin {ref} "
                    "&& git -C /workspace log -1 --format=%cs%x09%s FETCH_HEAD",
                ],
            ),
            timeout=30,
        )
        date, _, subject = (detail or "").strip().partition("\t")
        if date:
            tip["date"] = date
        if subject:
            tip["subject"] = subject[:120]
    except Exception as exc:
        logger.info("developer_branch_probe_detail_failed", branch=branch, error=str(exc))
    return tip


def _final_summary(result: dict[str, Any]) -> str:
    """The ``summary`` of the developer's final JSON object, or "" (#488).

    The object comes after the developer's narration, often in a ```json
    fence, so the output as a whole is not JSON.
    """
    from embry0.orchestration.agent_json import last_object_with

    outputs = result.get("agent_outputs")
    last = outputs[-1] if isinstance(outputs, list) and outputs else None
    if not isinstance(last, dict) or last.get("is_error"):
        return ""
    data = last_object_with(str(last.get("output") or ""), "summary")
    summary = (data or {}).get("summary")
    return summary.strip()[:4000] if isinstance(summary, str) else ""


async def _verify_branch_pushed(
    *,
    docker: Any,
    container_id: str | None,
    branch: str,
) -> bool:
    """Verify that the developer's branch exists on the remote.

    Returns True if the branch is on remote OR if verification cannot run
    (no docker / no container / branch is "main" / docker call raised) —
    the goal is to catch the LLM-forgot-to-push case, not to block on
    infra failures. False ONLY when we can definitively confirm the
    branch is missing from origin.
    """
    if not docker or not container_id or not branch or branch == "main":
        return True
    try:
        out = await docker.run_cmd(
            docker.build_exec_cmd(
                container_id,
                [
                    "git",
                    "-C",
                    "/workspace",
                    "ls-remote",
                    "--heads",
                    "origin",
                    branch,
                ],
            ),
            timeout=15,
        )
    except Exception as exc:
        logger.warning(
            "dev_branch_verify_failed",
            branch=branch,
            error=str(exc),
            note="benefit of doubt — proceeding without blocking",
        )
        return True
    # `git ls-remote --heads origin <branch>` returns:
    #   - empty stdout if the branch doesn't exist on remote
    #   - "<sha>\trefs/heads/<branch>\n" if it does
    return bool(out and branch in out)


async def _commit_unfinished_work(
    *,
    docker: Any,
    container_id: str | None,
    branch: str,
    job_id: str | None = None,
    message: str | None = None,
) -> bool:
    """Commit what the agent left in the worktree, so it can be pushed (#358).

    ``_rescue_push_branch`` refuses a branch with no commits of its own, on the
    reasoning that an empty branch means the agent produced nothing and pausing
    is correct. For a clean finish that holds. After a TURN-CAP death it does
    not: the run is cut off mid-stride and the work sits in the worktree,
    edited but never committed, so the rescue skips it and the sandbox takes it.

    That is how the RAV-1466 canary lost a complete two-language change — 22
    edits and 3 new files, $2.17 of opus-5 — three turns short of a commit.

    Committing is deterministic, so the orchestrator does it rather than paying
    for another agent turn that may hit the same cap. The message says plainly
    that embry0 wrote the commit and that the change is unfinished; nothing
    downstream may present it as completed work.

    Called for a run that stopped (``_salvage``): a turn cap, any error, the
    job's budget. ``message`` names why; the default is #358's. The commit goes
    on ``branch``: when the agent never created it, a branch of that name is
    started where the worktree stands; when another branch is checked out and
    ``branch`` exists too, nothing is committed rather than guess. Returns True
    when a commit was created.
    """
    if not docker or not container_id or not branch or branch == "main":
        return False
    try:
        dirty = await docker.run_cmd(
            docker.build_exec_cmd(container_id, ["git", "-C", "/workspace", "status", "--porcelain"]),
            timeout=15,
        )
        # Nothing uncommitted: either the agent committed (so _rescue_push_branch
        # can push it as it always has) or it truly produced nothing.
        if not (dirty or "").strip():
            return False
        entries = len(dirty.strip().splitlines())
        if not await _on_job_branch(docker=docker, container_id=container_id, branch=branch, job_id=job_id):
            return False
        await docker.run_cmd(
            docker.build_exec_cmd(container_id, ["git", "-C", "/workspace", "add", "-A"]),
            timeout=60,
        )
        await docker.run_cmd(
            docker.build_exec_cmd(
                container_id,
                [
                    "git",
                    "-C",
                    "/workspace",
                    "commit",
                    "-m",
                    message
                    or "wip: unfinished work preserved after the developer hit its turn cap\n\n"
                    "Committed by embry0, NOT by the agent. The developer run ended on its\n"
                    "turn cap before it could commit, so this change is incomplete and was\n"
                    "never self-reviewed. It is preserved here so the run is not lost;\n"
                    "continue from this branch rather than starting over (embry0 #358).",
                ],
            ),
            timeout=60,
        )
    except Exception as exc:
        logger.warning("dev_rescue_commit_failed", branch=branch, job_id=job_id, error=str(exc))
        return False
    logger.info("dev_rescue_commit_created", branch=branch, job_id=job_id, dirty_entries=entries)
    return True


async def _on_job_branch(*, docker: Any, container_id: str, branch: str, job_id: str | None) -> bool:
    """Put the worktree on ``branch`` before a salvage commit; False when that cannot be done safely.

    An unreadable HEAD keeps #358's behaviour (commit where it stands). The
    agent normally creates the branch itself; when it never did, the work is
    still on the base branch, and ``git switch -c`` carries it to a new branch
    of the job's name.
    """
    head = (
        await docker.run_cmd(
            docker.build_exec_cmd(container_id, ["git", "-C", "/workspace", "rev-parse", "--abbrev-ref", "HEAD"]),
            timeout=15,
        )
        or ""
    ).strip()
    if not head or head == branch:
        return True
    # A missing branch exits 1, which run_cmd logs as an error (#402): ask so it exits 0.
    exists = (
        await docker.run_cmd(
            docker.build_exec_cmd(
                container_id,
                [
                    "bash",
                    "-c",
                    f"git -C /workspace rev-parse --verify --quiet refs/heads/{shlex.quote(branch)} || true",
                ],
            ),
            timeout=15,
        )
        or ""
    ).strip()
    if exists:
        logger.warning("dev_salvage_other_branch_checked_out", branch=branch, head=head[:200], job_id=job_id)
        return False
    await docker.run_cmd(
        docker.build_exec_cmd(container_id, ["git", "-C", "/workspace", "switch", "-c", branch]),
        timeout=30,
    )
    return True


# Sentinels the unpushed-commit probe prints instead of a count, so one
# `docker exec` can report "cannot answer" without a non-zero exit. See
# _count_unpushed_commits.
_PROBE_NO_BRANCH = "__EMBRY0_NO_LOCAL_BRANCH__"
_PROBE_NO_REMOTE_REFS = "__EMBRY0_NO_ORIGIN_REFS__"
_PROBE_FAILED = "__EMBRY0_PROBE_FAILED__"


async def _count_unpushed_commits(
    *,
    docker: Any,
    container_id: str,
    branch: str,
    diagnostics: dict[str, Any] | None = None,
) -> int | None:
    """How many commits ``branch`` has that origin does not — None if unknowable.

    ``diagnostics``, when given, receives a ``probe`` key naming the outcome
    (``"counted"``, ``"no_local_branch"``, ``"failed"``) so the caller can say
    *why* in the failure it reports to a human, instead of making them
    reconstruct it from git plumbing.

    This probe used to be ``git rev-list --count origin/HEAD..<branch>``, which
    is wrong twice over (#402):

    * ``origin/HEAD`` is not guaranteed to exist in the sandbox clone. A clone
      made with ``--branch <ref> --single-branch`` never sets the symref, which
      is exactly why ``resolve_base_branch`` exists. When it is missing git
      exits 128 with "ambiguous argument … unknown revision", so the probe
      failed for a reason that has nothing to do with the branch, the rescue
      was skipped, and a finished-but-unpushed branch was stranded behind a
      human decision — the very case the rescue was written for.
    * A non-zero exit inside ``run_cmd`` is logged at ERROR as
      ``docker_cmd_failed``, so a handled probe failure looked in the logs like
      an unhandled fatal error next to ``dev_branch_not_pushed``. The watcher
      read the pair as a crash; it never was one.

    ``<branch> --not --remotes=origin`` asks the question directly — commits
    reachable from the branch but from no origin remote-tracking ref — and
    needs no base ref to be named or resolved. The command always exits 0 and
    prints either a count or a sentinel, so a "cannot answer" outcome is data
    rather than an exception, and stays out of the ERROR log.

    One state must be rejected rather than counted: a clone carrying no
    ``refs/remotes/origin/*`` at all. There the exclusion set is empty, so the
    count is the branch's entire history and every branch looks worth pushing —
    including one with no change of its own, which would publish an empty
    branch and invent a delivery. No remote-tracking refs means the question
    cannot be answered, so say so.
    """
    qbranch = shlex.quote(branch)
    script = (
        "cd /workspace 2>/dev/null || { echo " + _PROBE_FAILED + "; exit 0; }; "
        f"git rev-parse --verify --quiet refs/heads/{qbranch} >/dev/null 2>&1 "
        "|| { echo " + _PROBE_NO_BRANCH + "; exit 0; }; "
        'test -n "$(git for-each-ref --count=1 refs/remotes/origin/ 2>/dev/null)" '
        "|| { echo " + _PROBE_NO_REMOTE_REFS + "; exit 0; }; "
        f"git rev-list --count {qbranch} --not --remotes=origin 2>/dev/null "
        "|| echo " + _PROBE_FAILED
    )
    try:
        raw = await docker.run_cmd(
            docker.build_exec_cmd(container_id, ["bash", "-c", script]),
            timeout=15,
        )
    except Exception as exc:
        if diagnostics is not None:
            diagnostics["probe"] = "failed"
        logger.warning("dev_rescue_probe_failed", branch=branch, error=str(exc))
        return None
    first = (raw or "").strip().splitlines()[0].strip() if (raw or "").strip() else ""
    if first.isdigit():
        if diagnostics is not None:
            diagnostics["probe"] = "counted"
            diagnostics["unpushed_commits"] = int(first)
        return int(first)
    if first == _PROBE_NO_BRANCH:
        if diagnostics is not None:
            diagnostics["probe"] = "no_local_branch"
        logger.warning(
            "dev_rescue_probe_no_local_branch",
            branch=branch,
            note="the branch does not exist in the sandbox workspace either — the agent never created it",
        )
        return None
    if first == _PROBE_NO_REMOTE_REFS:
        if diagnostics is not None:
            diagnostics["probe"] = "failed"
            diagnostics["probe_detail"] = "no_origin_refs"
        logger.warning(
            "dev_rescue_probe_failed",
            branch=branch,
            note="the clone carries no origin remote-tracking refs, so 'commits origin does not have' "
            "would count the branch's entire history and make an empty branch look worth pushing",
        )
        return None
    if diagnostics is not None:
        diagnostics["probe"] = "failed"
    logger.warning(
        "dev_rescue_probe_failed",
        branch=branch,
        note="could not count the branch's unpushed commits",
        output=first[:200],
    )
    return None


async def _rescue_push_branch(
    *,
    docker: Any,
    container_id: str | None,
    branch: str,
    diagnostics: dict[str, Any] | None = None,
) -> bool:
    """Push the developer's branch ourselves when the agent left it local.

    The agent is instructed to push (prompt step 5) and usually does, but a
    turn-cap death or a run burnt retrying a policy-blocked tool can end the
    session with the work committed and never pushed. Pausing there strands a
    finished branch behind a human decision, and the operator's only sensible
    answer is "retry", which just re-runs an agent to perform one mechanical
    git command.

    Pushing is deterministic, so the orchestrator can simply do it. This runs
    ONLY after _verify_branch_pushed said the branch is absent, and only when
    there is something to push: it never overrides the agent, it rescues it.

    Returns True when the branch is on origin afterwards.
    """
    if not docker or not container_id or not branch or branch == "main":
        return False
    # Only rescue a branch that actually has commits of its own; an empty
    # branch means the agent produced nothing and pausing is correct. An
    # unknowable count (None) is treated the same way rather than pushed
    # blind: pushing a branch with no commits of its own would publish an
    # empty branch and invent a delivery.
    ahead = await _count_unpushed_commits(
        docker=docker,
        container_id=container_id,
        branch=branch,
        diagnostics=diagnostics,
    )
    if ahead is None:
        logger.info(
            "dev_rescue_push_skipped",
            branch=branch,
            note="could not determine whether there is anything to push",
        )
        return False
    if ahead == 0:
        logger.info("dev_rescue_push_skipped", branch=branch, note="no local commits to push")
        return False
    try:
        await docker.run_cmd(
            docker.build_exec_cmd(
                container_id,
                ["git", "-C", "/workspace", "push", "-u", "origin", branch],
            ),
            timeout=120,
        )
    except Exception as exc:
        if diagnostics is not None:
            diagnostics["push_error"] = str(exc)
        logger.warning("dev_rescue_push_failed", branch=branch, error=str(exc))
        return False
    return await _verify_branch_pushed(docker=docker, container_id=container_id, branch=branch)


def _branch_missing_explanation(branch: str, diagnostics: dict[str, Any]) -> str:
    """Say, in plain words, why the branch is not on origin (#402).

    The old single sentence — "did not push … push the branch manually" — was
    wrong for the most common case: when the agent never created the branch
    there is nothing to push manually, and whoever reads the failure spends
    their first minutes looking for a branch that does not exist.
    """
    probe = diagnostics.get("probe")
    if probe == "no_local_branch":
        why = (
            f"Branch {branch!r} does not exist on origin, and it does not exist in the "
            f"sandbox workspace either — the developer agent never created it, so there is "
            f"nothing to push. Treat this as the agent producing no work, not as a push failure."
        )
    elif probe == "counted" and diagnostics.get("unpushed_commits") == 0:
        why = (
            f"Branch {branch!r} exists in the sandbox but carries no commits of its own, "
            f"so there was nothing to push. The developer agent produced no change."
        )
    elif probe == "failed":
        why = (
            f"Branch {branch!r} is not on origin, and embry0 could not determine whether the "
            f"sandbox had anything to push (the git probe itself failed — see "
            f"dev_rescue_probe_failed). The branch was left untouched rather than pushed blind."
        )
    else:
        n = diagnostics.get("unpushed_commits")
        commits = f"{n} unpushed commit(s)" if isinstance(n, int) else "unpushed commits"
        push_error = diagnostics.get("push_error")
        why = (
            f"Branch {branch!r} has {commits} in the sandbox but is not on origin: the agent "
            f"did not push it and embry0's rescue push also failed"
            + (f" ({push_error})" if push_error else "")
            + ". The work exists in the sandbox only."
        )
    return (
        why + " This breaks the QA handoff: QA cannot clone what was never pushed. "
        "Restart the job after the dev agent has been corrected."
    )


def _coding_agent(state: dict[str, Any]) -> str:
    """The agent this node runs as (spec 2026-10-25, slop dungeon): the Slopmaster on a
    slop-cleanup route, else the developer. A job planned before then has none."""
    from embry0.workflows.issue_to_pr.route_plan import CODING_AGENT_TYPES

    agent = str(state.get("coding_agent") or "developer")
    return agent if agent in CODING_AGENT_TYPES else "developer"


def _fallback_system_prompt(coding_agent: str) -> str:
    """The system prompt when the agent has no row: the Slopmaster's own, the developer's none."""
    if coding_agent != SLOPMASTER:
        return ""
    from embry0.services.slop import SLOPMASTER_SYSTEM_PROMPT

    return SLOPMASTER_SYSTEM_PROMPT


async def _record_cleanup_diff(state: dict[str, Any], configurable: dict[str, Any]) -> dict[str, Any] | None:
    """A Slopmaster run's diff in lines, on state and on the job's verdict; None when it cannot be read.

    The Slopmaster's worth is the code it removed (its experience counts net
    lines removed), and the reviewer reads the same numbers.
    """
    docker = configurable.get("docker")
    container = state.get("sandbox_container_id")
    if docker is None or not container:
        return None
    from embry0.execution.workspace_diff import diff_line_counts, resolve_base_branch

    try:
        base = await resolve_base_branch(docker=docker, container_id=container, configured=state.get("base_branch"))
    except Exception:  # noqa: BLE001 - the count is a bonus, never a failure
        logger.warning("cleanup_diff_base_unresolved", job_id=state.get("job_id"), exc_info=True)
        return None
    counts = await diff_line_counts(docker=docker, container_id=container, base_branch=base)
    if counts is None:
        return None
    verdict = {**(state.get("work_nature") or {}), "cleanup": counts}
    jobs_repo = configurable.get("jobs_repo")
    if jobs_repo is not None:
        try:
            await jobs_repo.update(str(state.get("job_id") or ""), work_nature=verdict)
        except Exception:  # noqa: BLE001 - the row is a mirror; state carries it on
            logger.warning("cleanup_diff_persist_failed", job_id=state.get("job_id"), exc_info=True)
    logger.info("cleanup_diff_recorded", job_id=state.get("job_id"), **counts)
    return verdict


async def developer_node(state: dict[str, Any], config: RunnableConfig) -> Command[str]:
    """Run developer agent — write code, create branch, commit, push, open PR.

    Uses AgentRunner.run() to execute a full Claude Code session in the sandbox.
    Returns a Command that self-routes to review / ask_user_interrupt / max_retries
    based on the executor output, pending agent questions, and budget state.
    """
    writer = _nodes.get_stream_writer()
    # Slop dungeon (spec 2026-10-25): on a slop-cleanup route this node runs
    # the Slopmaster, under its own agent type and definition.
    coding_agent = _coding_agent(state)
    attempt = agent_attempt(state, coding_agent)
    writer(events.node_started(node="developer", agent=coding_agent, attempt=attempt))

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    agent_runner = configurable.get("agent_runner")
    credentials = configurable.get("credentials") or {}
    agent_sessions_repo = configurable.get("agent_sessions_repo")

    user_answers = state.get("user_answers")
    branch_name, taken_branch = await _developer_working_branch(state, configurable)
    branch_name = await _grant_push_branch(state, configurable, branch_name)

    # Collect agent events locally so we can scan for agent_ask_user events
    # after the agent finishes. We still forward every event to the graph
    # stream writer for live streaming.
    collected_events: list[dict[str, Any]] = []

    def _forward_event(event: dict[str, Any]) -> None:
        collected_events.append(event)
        writer(event)

    # Plan C Task 6: load any prior AgentSession for this (job, agent) so the
    # in-sandbox executor can resume the same Claude conversation. Restore
    # failures must NEVER block the agent run — fall back to a fresh session.
    from embry0.agents.session import AgentSession, merged_resume_messages, session_resumable

    resume_session: AgentSession | None = None
    adopted_from_job: str | None = None
    if agent_sessions_repo is not None:
        try:
            prior = await agent_sessions_repo.get(
                job_id=state.get("job_id", ""),
                agent_type=coding_agent,
            )
            # RAV-1092: no own session yet + the job carries a donor job id
            # (PR-comment follow-up) → adopt the session of the developer
            # that created the PR. It already knows the codebase, the task,
            # and every change it made — the comment becomes a delta turn
            # instead of a cold re-exploration.
            if prior is None:
                donor_job = (state.get("context") or {}).get("resume_from_job_id")
                if donor_job:
                    prior = await agent_sessions_repo.get(job_id=str(donor_job), agent_type="developer")
                    if prior is not None:
                        adopted_from_job = str(donor_job)
                        logger.info(
                            "developer_session_adopted",
                            job_id=state.get("job_id"),
                            donor_job_id=donor_job,
                        )
            if prior is not None:
                resume_session = AgentSession(
                    job_id=prior["job_id"],
                    agent_type=prior["agent_type"],
                    mode=prior["mode"],
                    messages=prior.get("messages"),
                    session_id=prior.get("session_id"),
                    session_blob=prior.get("session_blob"),
                )
        except Exception:
            logger.warning(
                "developer_session_restore_failed",
                job_id=state.get("job_id"),
                exc_info=True,
            )
            resume_session = None
            adopted_from_job = None

    # EMB-35: resume engaged ⇒ send only the delta (the resumed session
    # already carries the task + earlier context); fresh run ⇒ full prompt.
    # RAV-1092: an ADOPTED session carries the ORIGINAL task, not this one —
    # the adopted prompt delivers the new instructions (the PR comment).
    if session_resumable(resume_session) and adopted_from_job:
        prompt = _build_developer_adopted_prompt(state, branch_name)
    elif session_resumable(resume_session):
        prompt = _build_developer_delta_prompt(state, branch_name)
    else:
        prompt = _build_developer_full_prompt(
            state, branch_name, configurable.get("embry0_config"), taken_branch=taken_branch
        )
    if coding_agent == SLOPMASTER:
        from embry0.services.slop import slop_brief
        from embry0.services.slop_meter.context import job_meter, meter_sentence

        brief = slop_brief(state.get("work_nature"))
        if brief:
            prompt = f"{prompt}\n\n{brief}"
        # The Slop-O-Meter (spec 2026-10-26): how much of the area landed beyond human attention.
        reading = await job_meter(config, state)
        if reading is not None:
            prompt = f"{prompt}\n\n{meter_sentence(*reading)}"
    # Done when (spec 2026-10-26): the job's finish line, read from its row so
    # a person's edit counts from the next run on, as this run's contract.
    from embry0.services.done_when import contract as done_when_contract
    from embry0.services.done_when import current as current_done_when

    done_when = await current_done_when(
        configurable.get("jobs_repo"), str(state.get("job_id") or ""), state.get("done_when")
    )
    if done_when:
        prompt = f"{prompt}\n\n{done_when_contract(done_when, followup=state.get('done_when_followup'))}"

    # RAV-602: definition (model/tools/skills) from the DB row, per-step
    # template overrides from the current route step; triage's
    # pipeline_config.agent_* still wins on top (resolver precedence).
    from embry0.orchestration.nodes.agent import load_agent_definition
    from embry0.workflows.issue_to_pr.route_plan import step_template_config

    developer_definition = await load_agent_definition(
        config,
        coding_agent,
        {
            "model": "claude-sonnet-5",
            "tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
            "skills": [],
            "system_prompt": _fallback_system_prompt(coding_agent),
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        },
    )
    # SH-P4.4: invocation-scoped log tool — only when init_node recorded the
    # containers the context-proxy CONFIRMED for this sandbox (a job with no
    # case/evidence, or whose enrollment failed, never has the key).
    if state.get("case_log_containers"):
        from embry0.workflows.issue_to_pr.log_tool import grant_log_tool

        developer_definition = grant_log_tool(developer_definition)
        logger.info(
            "developer_log_tool_granted", job_id=state.get("job_id"), containers=state.get("case_log_containers")
        )
    developer_template_config = step_template_config(state, coding_agent)
    result = await _nodes.run_agent_node(
        state={**state, "done_when": done_when} if done_when else state,
        agent_runner=agent_runner,
        agent_type=coding_agent,
        prompt=prompt,
        agent_definition=developer_definition,
        template_config=developer_template_config,
        timeout_seconds=1800,
        on_event=_forward_event,
        credentials=credentials,
        config=config,
        resume_session=resume_session,
    )

    # Plan C Task 6: capture post-run session state so the next invocation of
    # this node (after an ask_user pause, retry, etc.) resumes the same
    # conversation. Skip on agent error (no useful state to persist) and skip
    # when both messages and session_id are absent (avoids writing empty rows
    # before the executor learns to extract these fields). Persist failures
    # are best-effort; never block the workflow.
    if agent_sessions_repo is not None:
        last_output = (result.get("agent_outputs") or [None])[-1]
        if last_output and not last_output.get("is_error"):
            new_messages = merged_resume_messages(resume_session, last_output.get("messages"))
            new_session_id = last_output.get("session_id")
            new_session_blob = last_output.get("session_blob")
            if new_messages or new_session_id:
                mode = last_output.get("mode") or ("claude_max" if credentials.get("oauth_token") else "anthropic_api")
                try:
                    await agent_sessions_repo.upsert(
                        job_id=state.get("job_id", ""),
                        agent_type=coding_agent,
                        mode=mode,
                        messages=new_messages,
                        session_id=new_session_id,
                        session_blob=new_session_blob,
                    )
                except Exception:
                    logger.warning(
                        "developer_session_persist_failed",
                        job_id=state.get("job_id"),
                        exc_info=True,
                    )

    # Scan the agent's event stream for `agent_ask_user` events. If present,
    # surface them to the graph so the router can divert to ask_user_interrupt.
    pending_questions = _extract_ask_user_events({"events": collected_events}, calling_node="developer")

    # Try to extract PR URL from agent output
    pr_url = None
    branch = branch_name
    if result.get("agent_outputs"):
        last_output = result["agent_outputs"][-1] if isinstance(result.get("agent_outputs"), list) else None
        if last_output:
            output_text = last_output.get("output", "")
            import json as json_mod

            try:
                for line in output_text.split("\n"):
                    line = line.strip()
                    if line.startswith("{") and "pr_url" in line:
                        data = json_mod.loads(line)
                        pr_url = data.get("pr_url")
                        branch = data.get("branch", branch)
                        break
            except (json_mod.JSONDecodeError, TypeError):
                pass
            # Fallback: search for GitHub PR URL pattern
            if not pr_url:
                import re

                match = re.search(r"https://github\.com/[^/]+/[^/]+/pull/\d+", output_text)
                if match:
                    pr_url = match.group(0)

    # #478: a job named away from an earlier job's branch must not report that
    # branch as its own. It exists on origin, so the push check would pass and
    # the PR fallback would deliver the earlier job's work under this job. A
    # job with no issue works on its own branch from the start (#558), and the
    # shared name it must not claim may hold a job's work from before that.
    from embry0.orchestration.branch_names import shared_branch_name, taken_branch_name

    shared = shared_branch_name(issue_id=state.get("issue_id"), task=state.get("task"))
    if branch == shared and branch_name == taken_branch_name(shared, state.get("job_id")):
        logger.warning(
            "developer_reported_taken_branch", job_id=state.get("job_id"), reported=branch, branch=branch_name
        )
        branch = branch_name
    elif branch != branch_name:
        # #559: the sandbox may push only branch_name, so another branch the
        # agent reports holds none of this job's work. Recording it would also
        # hand it the push right: a resume enrolls the branch state names.
        logger.warning(
            "developer_reported_other_branch",
            job_id=state.get("job_id"),
            reported=str(branch)[:200],
            branch=branch_name,
        )
        branch = branch_name

    if pr_url:
        writer(events.pr_created(pr_url=pr_url, branch=branch))

    writer(events.node_completed(node="developer", agent=coding_agent, attempt=attempt))

    updates: dict[str, Any] = {
        **result,
        # RAV-1079: keep a job's existing PR link when this run's output had
        # no extractable pr_url (follow-up runs on an existing PR legitimately
        # don't re-announce it) — never null out a known link.
        "pr_url": pr_url or state.get("pr_url"),
        "branch_name": branch,
        "current_stage": "developer_complete",
    }
    # #488: the developer's own description of the change, kept as a fact of
    # its own. The PR fallback reads it when the job ends, and by then the
    # result's agent_outputs hold only the last node's fragment (the review's).
    developer_summary = _final_summary(result)
    if developer_summary:
        updates["developer_summary"] = developer_summary

    if pending_questions:
        from embry0.agents.skills import effective_skills
        from embry0.orchestration.nodes.agent import _enforce_ask_user_cap

        # Brainstorming sessions can legitimately need many turns of design
        # back-and-forth. Bump the cap when the developer's effective skills
        # carry brainstorming (the row, then the template node's delta, then
        # triage's, as the resolver applies them); everything else stays at the
        # default of 5.
        template_skills = ((developer_template_config or {}).get("agent_skills") or {}).get(coding_agent)
        triage_skills = ((state.get("pipeline_config", {}) or {}).get("agent_skills") or {}).get(coding_agent)
        skills_for_dev = effective_skills(developer_definition.get("skills") or [], template_skills, triage_skills)
        ask_cap = BRAINSTORMING_ASK_USER_CAP if "superpowers:brainstorming" in skills_for_dev else DEFAULT_ASK_USER_CAP

        exhausted, cap_updates = _enforce_ask_user_cap(
            state,
            pending_questions,
            max_rounds=ask_cap,
            job_id_for_log=state.get("job_id"),
        )
        updates.update(cap_updates)
        if exhausted:
            pass  # current_stage and error fields already set in updates
        elif updates.get("pending_agent_questions"):
            # Blocking questions exist — the cap was incremented and the
            # workflow will pause at ask_user_interrupt.
            updates["current_stage"] = "developer_asked_user"
            logger.info(
                "developer_asked_user",
                question_count=len(updates["pending_agent_questions"]),
                rounds=updates["agent_question_rounds"],
                job_id=state.get("job_id"),
            )
        else:
            # All questions were auto-answerable — workflow proceeds without
            # pausing. Log so the auto-answers remain observable in the trace.
            logger.info(
                "developer_auto_answered",
                question_count=len(updates.get("auto_answered_agent_questions", [])),
                job_id=state.get("job_id"),
            )

    # Clear consumed one-shot context so it doesn't bleed into future runs —
    # with delta prompts (EMB-35) a stale block would be re-sent verbatim on
    # every subsequent resumed re-entry.
    if user_answers:
        updates["user_answers"] = None
    if state.get("feedback_context"):
        updates["feedback_context"] = ""
    if state.get("user_retry_guidance"):
        updates["user_retry_guidance"] = None
    if state.get("developer_prompt_addendum"):
        updates["developer_prompt_addendum"] = None
    if state.get("developer_focus_files"):
        updates["developer_focus_files"] = []
    if state.get("unfinished_work"):
        updates["unfinished_work"] = None

    # Self-routing: decide the next node based on the updates we just produced
    # and the state we were handed. Priority:
    #   1. agent_questions_exhausted wins — terminal failure (max_retries).
    #   2. Pending agent questions divert to ask_user_interrupt.
    #   3. Budget overrun also diverts to max_retries.
    #   4. Otherwise proceed to review.
    from embry0.orchestration.routing.conditions import check_budget
    from embry0.workflows.issue_to_pr.nodes import _salvage

    # Salvage (spec 2026-10-25): a run that stops before it pushes keeps its
    # work. Every route below that ends the coding step without a verified
    # push commits what the run left uncommitted and pushes it, unfinished.
    salvage: dict[str, Any] = {
        "docker": configurable.get("docker"),
        "container_id": state.get("sandbox_container_id"),
        "branch": branch,
        "job_id": state.get("job_id"),
        "agent": _salvage.coding_agent(result, state),
    }

    if updates.get("agent_questions_exhausted") or state.get("agent_questions_exhausted"):
        goto = "max_retries"
        kept = await _salvage.preserve_unfinished_work(**salvage, reason=_salvage.QUESTIONS)
        if kept and kept["committed"]:
            updates["unfinished_work"] = kept
    elif updates.get("pending_agent_questions") or state.get("pending_agent_questions"):
        goto = "ask_user_interrupt"
    elif check_budget({**state, **updates}) == "over_budget":
        # soft overrun mode only: `hard` never gets here, run_agent_node
        # raises BudgetOverrunError at the ceiling.
        goto = "max_retries"
        kept = await _salvage.preserve_unfinished_work(**salvage, reason=_salvage.BUDGET)
        if kept and kept["committed"]:
            updates["unfinished_work"] = kept
    else:
        # Programmatic verification: the LLM is INSTRUCTED to push the branch
        # (prompt step 5) but doesn't always follow through. If we proceed to
        # review→QA with no branch on remote, QA hits a generic
        # "Remote branch not found" clone error after spinning up the QA
        # sandbox — wastes time + cost + obscures the real cause. Verify
        # here and fail fast with a clear error code.
        push_ok = await _nodes._verify_branch_pushed(
            docker=configurable.get("docker"),
            container_id=state.get("sandbox_container_id"),
            branch=branch,
        )
        push_diagnostics: dict[str, Any] = {}
        # #358, widened by salvage: a run that STOPPED (turn cap, any error)
        # typically leaves its work uncommitted, and _rescue_push_branch refuses
        # a branch with no commits of its own, so the run's output was
        # discarded. Commit it, then push it, whether or not an earlier push
        # put the branch on origin. A clean finish keeps the old behaviour,
        # where an empty branch correctly means "pause".
        stopped = _salvage.stop_reason(result)
        kept = None
        if stopped:
            kept = await _salvage.preserve_unfinished_work(**salvage, reason=stopped, diagnostics=push_diagnostics)
            push_ok = push_ok or kept is not None
        rescued_unfinished = bool(kept and kept["committed"])
        if not push_ok and not stopped:
            # Before stranding a finished branch behind a human decision, try
            # the one mechanical step the agent missed (see _rescue_push_branch).
            push_ok = await _nodes._rescue_push_branch(
                docker=configurable.get("docker"),
                container_id=state.get("sandbox_container_id"),
                branch=branch,
                diagnostics=push_diagnostics,
            )
            if push_ok:
                logger.info(
                    "dev_branch_rescued",
                    branch=branch,
                    job_id=state.get("job_id"),
                    note="agent left the branch local; orchestrator pushed it",
                )

        if push_ok and rescued_unfinished:
            # The work is safe on origin, but it is admittedly unfinished and
            # never self-reviewed. Advancing it into review/QA would spend more
            # money on a predictable rejection and risk presenting a cut-off run
            # as a delivery, so this still pauses — the difference from before is
            # that the human now has a branch to continue from instead of
            # nothing. `continue_retrying` can build on it.
            from embry0.safety.error_codes import ErrorCode

            logger.warning(
                "dev_run_unfinished_but_preserved",
                branch=branch,
                job_id=state.get("job_id"),
                reason=stopped,
            )
            updates["unfinished_work"] = kept
            updates["errors"] = (state.get("errors") or []) + [
                f"The {salvage['agent']} stopped ({stopped}) before committing. embry0 committed its work and "
                f"pushed it to {branch!r} so the run is not lost, but the change is UNFINISHED "
                f"and was never self-reviewed. Continue from that branch rather than starting over."
            ]
            updates["error_code"] = ErrorCode.DEV_RUN_UNFINISHED.value
            updates["current_stage"] = "dev_run_unfinished"
            goto = "max_retries"
        elif not push_ok:
            from embry0.safety.error_codes import ErrorCode

            logger.error(
                "dev_branch_not_pushed",
                branch=branch,
                job_id=state.get("job_id"),
                # #402: the reason, on the same line as the failure. Without it
                # the only clue was a lower-level git error logged separately,
                # which read like an unhandled crash.
                reason=push_diagnostics.get("probe") or "not_probed",
                unpushed_commits=push_diagnostics.get("unpushed_commits"),
            )
            updates["errors"] = (state.get("errors") or []) + [_branch_missing_explanation(branch, push_diagnostics)]
            updates["error_code"] = ErrorCode.DEV_BRANCH_NOT_PUSHED.value
            updates["current_stage"] = "dev_branch_missing"
            goto = "max_retries"
        else:
            if coding_agent == SLOPMASTER:
                cleaned = await _record_cleanup_diff(state, configurable)
                if cleaned is not None:
                    updates["work_nature"] = cleaned
            # Done when (spec 2026-10-26): an unmet item sends the coding step
            # once more, then pauses the job; all met goes on as before.
            from embry0.workflows.issue_to_pr.nodes._done_when import check_done_when

            checked, held = await check_done_when(
                state, configurable, result, done_when, writer=writer, agent=coding_agent
            )
            updates.update(checked)
            # RAV-601: forward progression follows the route plan. Jobs
            # without one (pre-RAV-601 checkpoints resumed mid-flight) keep
            # the legacy fixed hop to review.
            if held is not None:
                goto = held
            elif state.get("route_plan"):
                from embry0.workflows.issue_to_pr.route_plan import (
                    CODING_AGENT_TYPES,
                    ROUTE_TARGETS,
                    advance,
                    next_route,
                )

                # RAV-1271: advance only when the cursor is ON a developer
                # step (fresh forward progression). Re-entry paths — the
                # review retry loop, max_retries continue_retrying, ask_user
                # resumes — park the cursor at the step being retried, and a
                # blind +1 here skipped that step (observed: post-resume
                # developer → QA with no re-review).
                plan = state.get("route_plan") or []
                cursor = int(state.get("route_cursor", 0) or 0)
                if 0 <= cursor < len(plan) and plan[cursor].get("agent_type") in CODING_AGENT_TYPES:
                    updates.update(advance(state))
                target = next_route({**state, **updates})
                goto = END if target == "end" else ROUTE_TARGETS[target]
            else:
                goto = "review"

    return Command(goto=goto, update=updates)
