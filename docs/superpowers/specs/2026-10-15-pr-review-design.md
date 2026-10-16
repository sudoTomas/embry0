# Review a pull request when it is ready for review

Issue #30. Status: approved design, 2026-10-15.

## Goal

When a pull request (GitHub) or merge request (GitLab) on a registered repo becomes ready for review, embry0 reviews that branch and posts its findings on it, as an advisory reviewer. Anyone with write access can ask for another review with a comment, and that review continues the earlier review's conversation.

Success looks like this: an operator marks a pull request ready, and a review appears on it with a summary and inline comments. After a push, a `/embry0 review` comment produces a review that says which earlier findings were resolved and reviews what changed.

## Decisions

| Topic | Decision |
|---|---|
| Purpose | Advisory comments. It never approves, never requests changes, and sets no status check. |
| Triggers | The transition to ready, or opened directly as non-draft. Pushes do not re-trigger. |
| On demand | A `/embry0 review` comment from someone with write access, on GitHub or GitLab. |
| Who triggers automatic reviews | A per-repo setting, `ready_authors`: `team` (default) reviews a ready pull request only when its author has write access and is not a bot; `anyone` reviews every ready pull request. The `/embry0 review` command keeps its own write-access check either way. |
| Re-review | Continues the agent session of the pull request's previous review. |
| Depth | A per-repo setting: `full` (default), `targeted` or `read_only`. |
| No `dev_checks` configured | The agent works out the commands from the repo (CI config, Makefile, `package.json`, README), says what it ran, and suggests them as `dev_checks`. |
| Forks | Always reviewed read-only, whatever the depth. |
| Model | The deployment's heavy tier, with a per-repo override. |
| Default | Off per repo, until an operator turns it on. |

Out of scope: re-review on every push, approving or blocking, and configuring which severities are posted.

## Approach

This is a standalone `pr-review` pipeline, added the way the `onboard` pipeline was. Each review is its own job, so it gets the budget gate, the dashboard, the audit trail and restart handling. It reuses the existing reviewer's verdict shape and parser (`orchestration/routing/conditions.py`). It does not reuse the issue-to-pr `review` node, which depends on that pipeline's task, acceptance criteria and retry loop.

We rejected two alternatives:
- **Entering issue-to-pr at its review node.** That would mean faking issue state, and it would couple two features that should evolve separately.
- **Running the reviewer directly from the webhook handler.** That has no budget gate, no visibility, and no recovery after a restart.

## 1. Triggers and gates

### Events

| Forge | Event | Condition |
|---|---|---|
| GitHub | `pull_request` | `action == "ready_for_review"`, or `action == "opened"` with `draft == false` |
| GitHub | `issue_comment` | `action == "created"`, the issue is a pull request, and a line of the body starts with `/embry0 review` |
| GitLab | `Merge Request Hook` | `changes.draft` goes from true to false (`work_in_progress` on older GitLab), or `action == "open"` with `draft == false` |
| GitLab | `Note Hook` | `noteable_type == "MergeRequest"`, and a line of the note starts with `/embry0 review` |

The command is matched as a line prefix, case-insensitively. It is a different trigger from the existing `@embry0` follow-up tag (`services/comment_dispatch.py`). A comment carrying only `@embry0` never starts a review, and a `/embry0 review` comment is not dispatched as a follow-up.

The current handlers are extended:
- GitHub: `_handle_pull_request` gains `ready_for_review` and keeps its merge-sync bookkeeping. `_handle_issue_comment` routes the command.
- GitLab: the `object_kind` dispatch in `api/v1/webhooks.py` gains `merge_request` and `note`. Today it only forwards `issue`.

### Gates

Applied in this order. Each refusal is logged with its reason and posts nothing.

1. The repo is registered, and `repo_preferences.pr_review.enabled` is true.
2. The pull request is not embry0's own: no job has it as `pr_url`, and its head branch does not start with `embry0/`.
3. Author trust:
   - A comment command needs the commenter to have write access:
     - GitHub: `author_association` is OWNER, MEMBER or COLLABORATOR, and the author is not a bot. This is the existing `check_comment_author`.
     - GitLab: the commenter's project access level is Developer (30) or higher, checked through the members API (`/projects/:id/members/all/:user_id`).
   - A ready event, when `pr_review.ready_authors` is `team` (the default), needs the pull request's author to pass the same check. On GitHub that is the pull request object's own `author_association` and `user`, through `check_comment_author`. On GitLab it is the merge request's `object_attributes.author_id`, not the user who marked it ready. A failed or impossible lookup refuses. The refusal reason is `author_not_on_team`. With `anyone`, ready events skip this check.
   - The CLI and `POST /jobs` are operator actions and skip this gate.
   - It runs before dedupe and budget, so a refused author claims no key and is never charged.
4. Dedupe:
   - Ready events are keyed on `(repo, number, head_sha)`.
   - Comment commands are keyed on the comment id.
   - The keys are enforced by a unique index on `pr_reviews`, so they survive restarts.
5. Budget: `BudgetGate.check(actor)` runs before the job is created. The actor is the pull request's author for ready events, and the commenter for the command.

### Forks

- GitHub: the head repository differs from the base repository. `PullRequestFact.from_fork` already computes this.
- GitLab: `source_project_id != target_project_id`.

A fork's job is created with depth `read_only`.

### Job

`pipeline_template = "pr-review"`. Its `pipeline_config` carries:
- `repo`, `forge`, `number`, `url`, `title`
- `author`, `head_ref`, `head_sha`, `base_ref`
- `from_fork`, `trigger` (`ready` | `command`), `depth`, `model`

## 2. Pipeline

The package is `embry0/workflows/pr_review/`, with the graph `init → review → publish → cleanup`. Wiring follows onboarding:
- an entry in `_select_workflow`
- `start_pr_review_job`
- a relaunch branch
- `pr-review` added to `NOT_RESUMABLE_PIPELINES`
- the API `Literal`
- the frontend job type
- a CLI command, `embry0 review <owner/repo> <number>`, which is a manual trigger that skips gate 1

### init

- **Sandbox profile:** the repo's `sandbox_profile` preference. With none set:
  - `read_only` uses `slim`.
  - Depths that run checks use a profile with internet egress, so dependencies can install.
- **Clone:** the base repository is cloned through the git-proxy. The head is then fetched:
  - a same-repo pull request: its branch
  - a fork: `refs/pull/<n>/head` (GitHub) or `refs/merge-requests/<iid>/head` (GitLab)

  Both refs live in the base repository, so the git-proxy's one-repository rule holds.
- **Checkout:** the exact `head_sha` from the trigger.
- **Session:** for `trigger == "command"`, init finds the latest completed `pr-review` job for the same `(repo, number)` that has an `agent_sessions` row. It copies that row to this job, as `pr-followup` does.

### review

- **Agent definition:** a new `pr-reviewer`, seeded at startup. Its model is the job's `model`, or the heavy tier.
- **Full prompt contents:**
  - the pull request's title, description and author
  - the base and head shas
  - the changed-file list, capped at 200 (`format_review_scope`)
  - the depth
  - the `dev_checks` to run
  - the rule that it reports findings and never approves
- **Depth:**
  - `full`: install dependencies and run every `dev_checks` entry. With none configured, discover the commands.
  - `targeted`: only the entries whose `paths` match the change.
  - `read_only`: no commands.
- **Limits:** each check gets 20 minutes, and the agent run gets 60 minutes.
- **Resumed prompt:** when the session resumes, the prompt is a delta. It gives `git diff <previous sha>..<head sha>` and asks the agent to re-check its earlier findings and review the change. When there is no usable session (the first review, a blob over `MAX_RESUME_BLOB_BYTES`, or a deleted job), it falls back to the full prompt, plus the previous review's findings as text.

### Verdict

The agent's final message is a JSON object:

```json
{
  "summary": "…",
  "review_comments": [{"file": "…", "line": 0, "severity": "critical|important|suggestion", "comment": "…"}],
  "checks_run": [{"name": "…", "command": "…", "status": "passed|failed|error", "output_tail": "…"}],
  "suggested_dev_checks": [{"name": "…", "paths": ["…"], "command": "…"}],
  "previous_findings": [{"file": "…", "line": 0, "comment": "…", "status": "resolved|still_open"}]
}
```

- `suggested_dev_checks` is filled only when the agent discovered the commands itself.
- `previous_findings` is filled only on a re-review.
- It is parsed with `_review_payload`. An unreadable verdict fails the job, and nothing is posted.

## 3. Publishing

Publishing runs in the orchestrator, with the forge token from `ForgeClient.token_for`. Nothing is posted from the sandbox.

### Placing comments

publish fetches the diff:
- GitHub: `pulls/{n}/files` and its patches.
- GitLab: the MR's diffs and `diff_refs`.

A finding becomes an inline comment only when its file and line are on the new side of a hunk. Anything else goes into the summary under "Elsewhere in the code", with its `file:line`. Inline comments are capped at 30, worst severity first, and the rest are listed in the summary. Each inline comment starts with **Critical**, **Important** or **Suggestion**.

### GitHub

- **Request:** one `POST /repos/{repo}/pulls/{n}/reviews`, with:
  - `event: "COMMENT"`
  - `commit_id: head_sha`
  - `body: <summary>`
  - `comments: [{path, line, side: "RIGHT", body}]`
- **Fallback:** on a 422, it retries once with every finding in the body.

### GitLab

- **Inline comments:** one discussion per finding, at `POST /projects/:id/merge_requests/:iid/discussions`, with a text `position` (`base_sha`, `start_sha`, `head_sha`, `new_path`, `new_line`).
- **Summary:** one note.
- **Fallback:** a finding whose position is refused moves into the summary.

### Summary body

1. An assessment, plus counts by severity.
2. Checks run, with each command and whether it passed or failed. A failure includes its output tail. A fork gets "Checks skipped: this pull request comes from a fork."
3. On a re-review, the earlier findings, each marked resolved or still open.
4. Suggested `dev_checks` as a ready-to-paste block, when the agent discovered them.
5. A footer naming the reviewed sha, a link to the job on the dashboard, and how to ask for another review.
6. The hidden marker `<!-- embry0-review job=<job_id> sha=<sha> -->`.

### Failure

- An unreadable verdict fails the job.
- A forge error after the fallback fails the job with the forge's message.
- Either way, the verdict stays in the job's record.
- `pr_reviews.posted_at` records whether the review landed.

`cleanup` destroys the sandbox on every outcome.

## 4. Settings, storage and rollout

### Setting

`repo_preferences.pr_review` is a JSONB column:
- `enabled`: default false
- `depth`: default `"full"`
- `model`: null means the heavy tier
- `ready_authors`: `"team"` (default) or `"anyone"`; a stored object without it reads as `"team"`

It is added to `_COLUMNS`, `_WRITABLE` and `_CASTS`, and to the preferences schemas. It is edited through `PUT /repos/{repo}/preferences`. The dashboard's repo preferences gain a "Pull request review" card with:
- a switch
- a depth selector, with a line describing each option
- a model picker, with "Default (heavy tier)" first
- a "Who triggers automatic reviews" choice: team (default) or anyone, the latter noting that outsiders' fork pull requests then start read-only reviews on the repo's budget
- a note listing the webhook events the repo needs

### Storage

One migration, numbered `max + 1` when it merges. It:
- adds `repo_preferences.pr_review`
- creates `pr_reviews`, with columns:
  - `id`, `repo`, `number`, `head_sha`, `trigger`
  - `trigger_key` (UNIQUE), which is `ready:<repo>#<number>@<sha>` or `command:<forge>:<comment id>`
  - `job_id`, `created_at`, `posted_at`

### Webhook setup

- GitHub: the "Pull requests" and "Issue comments" events.
- GitLab: "Merge request events" and "Comments".

There are no new routes, so what each ingress publishes is unchanged.

### Docs

- `docs/pr-review.md`: behaviour, settings, and the trust model, including why forks are read-only (ADR 0008: code a job runs can read the sandbox's credentials).
- `docs/webhooks.md`: the events to subscribe.
- `docs/glossary.md`: an entry for the pr-review pipeline.

### Deploy notes

- Orchestrator and frontend, via `infra/deploy.sh`. The migration runs at startup.
- No sandbox image rebuild.

## Testing

Every new test is shown failing first, against a clean tree.

- **Trigger parsing:** GitHub and GitLab fixtures for:
  - ready
  - opened as draft
  - opened non-draft
  - still draft
  - the command
  - an `@embry0`-only comment, which must not trigger a review
- **Gates:**
  - a disabled repo
  - embry0's own pull request
  - a GitLab member below Developer
  - a bot author
  - a ready pull request from outside the team, under `ready_authors: team` (GitHub association, GitLab author access, a failed lookup), and the same one admitted under `anyone`
  - over budget
- **Dedupe:** against a throwaway Postgres; a second delivery of the same key is refused.
- **Forks:** depth is forced to `read_only`, and the forge's head ref is fetched.
- **Comment placement:** from real patch text, covering:
  - added lines
  - deleted lines
  - a renamed file
  - a line just outside a hunk
- **Publish:**
  - the request shape for both forges
  - the 422 fallback
  - the 30-comment cap
  - the summary sections
- **Re-review:** the session copy, the delta prompt, and the fallback when there is no session.
- **End to end:** one run from webhook to publish, with a stub agent and stub forge.
- **Frontend:**
  - a test for the settings card
  - a Playwright check of the card
