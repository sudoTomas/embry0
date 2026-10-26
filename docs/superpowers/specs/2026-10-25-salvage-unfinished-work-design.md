# Salvage: a coding run that stops before it pushes keeps its work

**Why.** #358 kept the work of a developer that hit its turn cap: embry0 committed what the run left in the worktree, pushed it, and paused the job with `ERR_DEV_RUN_UNFINISHED`. Every other way a coding run stops still lost it:

- **An error ending.** The conversation outgrew a model proxy's request cap, the run timed out, or the agent crashed. The gate was the turn cap alone, so the uncommitted edits stayed in the sandbox, the branch never reached origin, and the pause said "Developer finished without pushing its branch".
- **The job's budget.** A developer run that crosses the per-job cap routes straight to the pause, before the push check. Nothing was committed or pushed, and the pause said "Review failed after 1 attempts", though no review ran.

Observed: a grok developer worked 120 turns (21 edits, 7 new files) and stopped on its turn cap with the job over budget. Everything stayed uncommitted in the sandbox, and the run before it had already lost its work the same way to a proxy error.

## What counts as stopped

A coding run (the developer, or another agent the developer node runs) **stopped** when:

- its last output is an error (any code: turn cap, timeout, a proxy or context refusal, a crash);
- the job reached its budget after it; or
- it ran out of question rounds.

A clean finish is not a stop. A run that ends without an error and leaves a dirty tree keeps #358's behavior: nothing is committed for it, and an empty branch means the agent produced nothing.

## What embry0 does

After a stopped run:

1. **Commit.** If `git status --porcelain` in `/workspace` shows changes, embry0 stages them (`git add -A`, which honours `.gitignore`) and makes one commit on the job's branch:
   - Subject: `wip: unfinished — <agent> stopped (<reason>)`.
   - Body: embry0 made the commit, not the agent; the run stopped before it committed, so the change is incomplete and never self-reviewed; the next run continues from it.
   - Author: the sandbox's git identity, as every job commit.
   - When the agent never created the branch, the commit goes on a new branch of that name from where the worktree stands. A different branch checked out under that name is left alone.
2. **Push.** The existing rescue pushes the branch: `git push -u origin <branch>` through the job's git-proxy grant, never forced. It runs only when the branch has commits origin lacks. It covers both a branch never pushed and one pushed earlier in the run.
3. **Pause, unfinished.** When a commit was made and pushed, the job pauses with `ERR_DEV_RUN_UNFINISHED` (stage `dev_run_unfinished`), as #358 does. Review is not run on a salvage commit.
   - **Budget and question stops** keep their own route to the pause. The pause summary adds the branch that holds the work.
   - **A stopped run with nothing uncommitted** behaves as before: a branch it pushed moves on, and one it left local is rescued.

A failure anywhere in salvage is logged and changes nothing: the job takes the path it took without it.

## What people and the next run see

- **The pause summary** names the agent, the reason and the branch:
  - "developer stopped (turn cap) before committing; embry0 kept its work on `<branch>`…";
  - a budget pause says the job reached its budget, instead of "Review failed".
- **The next coding attempt** after `continue_retrying` gets a prompt section: its earlier run stopped, embry0 committed what it left as `wip: unfinished …` on `<branch>` and pushed it, and it should review that commit against the task, finish what is missing, run the checks, then commit and push, not start over.
- **State.** `unfinished_work` (`{branch, agent, reason, committed}`) is a one-shot key: written when salvage runs, and cleared once a coding run has read it.

## Not changed

No migration, no new error code, no sandbox image change: the commit and push run from the orchestrator through `docker exec`, as #358's do.
