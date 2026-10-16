# Pull request review

embry0 can review pull requests (GitHub) and merge requests (GitLab) on a registered repository and post what it finds on them. It is an **advisory reviewer**: it comments, and it never approves, never requests changes and sets no status check. The author and the human reviewers decide.

Review is **off** for every repository until an operator turns it on (Repositories → the repository → *Pull request review*, or `PUT /api/v1/repos/{repo}/preferences` with a `pr_review` object: `enabled`, `depth`, `model`, `ready_authors`).

`ready_authors` says who starts a review by opening or readying a pull request:

| Value | A ready pull request is reviewed when |
|---|---|
| `team` (default) | its author has write access and is not a bot: on GitHub the pull request's `author_association` is in `COMMENT_DISPATCH_AUTHOR_ROLES` (default `OWNER,MEMBER,COLLABORATOR`); on GitLab the merge request's author has Developer (30) access or higher |
| `anyone` | anyone opens or readies it, including from a fork; outsiders' fork pull requests then start read-only reviews on your budget |

Settings saved before `ready_authors` existed read as `team`. It does not change who may comment `/embry0 review`, which always needs write access, and the manual trigger is not subject to it.

## When a review runs

| Forge | Event | Starts a review when |
|---|---|---|
| GitHub | `pull_request` | `ready_for_review`, or `opened` with `draft: false` |
| GitHub | `issue_comment` | on a pull request, a line of the comment starts with `/embry0 review` |
| GitLab | Merge Request Hook | the draft flag goes from true to false (`draft`, or `work_in_progress` on older GitLab), or `open` as non-draft |
| GitLab | Note Hook | on a merge request, a line of the note starts with `/embry0 review` |

Pushes do not start a review. The command is matched case-insensitively at the start of a line, and not inside a code block or a `>` quote. It is not the `@embry0` follow-up tag: a comment with only `@embry0` never starts a review, and a `/embry0 review` comment is never also a follow-up or an `/answer`. A comment or note that carries embry0's own review marker never starts a review, so embry0's comments cannot trigger one.

To review one pull request now, use `embry0 review <repo> <number>` (on GitLab the number is the merge request iid), or call the API it wraps:

```bash
curl -X POST "$EMBRY0_API_URL/api/v1/jobs" -H "Authorization: Bearer $EMBRY0_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"pipeline": "pr-review", "repo": "acme/widgets", "number": 7}'
```

This skips the repository's on/off setting and nothing else. The review runs on the repository's `pr_review` settings, and on the pull request `number` names, so a request that also sends `agent_models`, `pipeline_config`, `pipeline_template`, `sandbox_profile`, `max_budget_usd`, `execution_mode_override`, `auth_mode_override`, `additional_context`, `task`, `branch`, `qa`, `context`, `issue_number` or `skip_smoke: true` is refused with 422, rather than accepted and ignored. `number` is refused with 422 for every other pipeline. An unregistered repository answers 400; any other refusal answers 409 with its reason. A manual review counts as a command, so it continues the previous review the same way a `/embry0 review` comment does.

A trigger is refused, with the reason in the orchestrator log (`pr_review_refused`) and nothing posted, when:

1. the repository is not registered, or its review is off (the manual trigger skips the on/off setting only);
2. the pull request is closed, or the forge would not return it;
3. the pull request is embry0's own (a job opened it, or its branch starts with `embry0/`);
4. a comment's author lacks write access: on GitHub an `author_association` outside `COMMENT_DISPATCH_AUTHOR_ROLES` (default `OWNER,MEMBER,COLLABORATOR`) or a bot; on GitLab a project access level below Developer (30). With `ready_authors: team`, a ready event is held to the same check against the pull request's **author** (on GitLab, `object_attributes.author_id`, not whoever marked it ready; a lookup that fails refuses), with the reason `author_not_on_team`. It is skipped before any key is claimed or budget asked;
5. the same trigger was seen before: a ready event is keyed on its pull request and head sha, a command on its comment id (`pr_reviews.trigger_key`, unique, so this survives restarts);
6. the budget gate refuses the actor: the pull request's author for a GitHub ready event, the user who opened it or marked it ready for a GitLab one, the commenter for a command.

A review that failed or crashed keeps its trigger key, so a redelivery of the same event does not run it again. To ask again, comment `/embry0 review` on the pull request (a new comment, a new key), or use the manual trigger.

## What the review does

Each review is a `pr-review` job, visible on the dashboard, with its own sandbox (the repository's sandbox profile, else `slim`). The sandbox clones the base branch through the git-proxy and checks out the exact head commit the trigger named; a fork's head is fetched from the base repository (`refs/pull/<n>/head`, `refs/merge-requests/<iid>/head`). Either way the job fails with `ERR_PR_REVIEW_INIT_FAILED` and nothing is posted when the head cannot be checked out, and its message says which of two causes it was. *The branch was rewritten after the review was asked for*: the forge answered and that commit is no longer on it, usually because the branch was force-pushed or deleted after the trigger; ask again with `/embry0 review`. *Could not fetch … from the forge*: the fetch failed and the forge did not answer at all, so look at the git-proxy and the repository's forge credential, not at the branch.

The `pr-reviewer` agent runs on the repository's `pr_review.model`, else the deployment's heavy tier (`MODEL_HEAVY`). A stored model that has since lost its price or been disabled is not used: the review runs on the heavy tier and the orchestrator logs `pr_review_model_unusable`. The agent's run is limited to 60 minutes, and each check it runs to 20. Its definition is seeded at startup and written again on every start: an edit under Agents (its prompt, tools or model) takes effect until the orchestrator next restarts, then the seed replaces it. To change the reviewer's model for a repository, use `pr_review.model`, which survives restarts.

The pull request's title, author and description reach the agent inside a block fenced by a random delimiter, labelled as untrusted text to check against the code. The agent is told that an instruction found in that block or in the code is a finding, not an instruction to follow.

| Depth | What runs |
|---|---|
| `full` (default) | Installs dependencies and runs every `dev_checks` entry. With none configured, the agent works out the commands from the repository's CI configuration, Makefile, `package.json`, `pyproject.toml` and README, says what it ran, and suggests them as `dev_checks`. |
| `targeted` | Only the `dev_checks` entries whose paths the change touches (an entry with no paths always runs). With none that match, the agent reviews by reading. |
| `read_only` | Nothing: the agent reads the code and the diff, has no shell, the sandbox gets none of the repository's environment variables, and the head's own agent configuration is removed from the checkout (see [Forks and the trust model](#forks-and-the-trust-model)). |

## What gets posted

The orchestrator posts with the repository's forge credential; nothing is posted from the sandbox.

- **Inline comments** on the lines a finding names, when that line is part of the diff: at most 30, worst first, each starting with **Critical**, **Important** or **Suggestion**. A finding anywhere else (outside the diff, on a binary or deleted file, or past the 30) is listed in the summary under *Elsewhere in the code*. When someone pushed between the review and the publish, nothing is placed inline, since the lines belong to another commit.
- **A summary**: the assessment and counts by severity; the checks run, with the output tail of any that failed (for a fork: *Checks skipped: this pull request comes from a fork*); on a re-review, each earlier finding marked resolved or still open; suggested `dev_checks` as a block to paste; and a footer with the reviewed sha, the job id and how to ask for another review. The job id links to the job's dashboard page only when `DASHBOARD_PUBLIC_URL` is set to an address other than loopback; with it unset or left at its `http://localhost:8200` default the footer links nothing, so a local address never appears on a public pull request.

Everything the agent wrote is redacted for secrets before it is posted, and contained: an unclosed code fence is closed, `@mentions` cannot notify anyone, and `<!--` cannot open an HTML comment. Every comment embry0 posts, inline or summary, ends with the hidden marker `<!-- embry0-review job=<job_id> sha=<sha> -->`.

GitHub gets one review (`event: COMMENT`). If GitHub refuses it with 422, it is posted once more with every finding in the summary and no commit pin. GitLab gets a diff discussion per inline finding (a position GitLab refuses moves into the summary), then one summary note.

Each write is sent once. A 5xx answer or a timeout is not retried, because the forge may have accepted the write before the answer was lost, and a retry would post the review twice. A forge error fails the job with `ERR_PR_REVIEW_PUBLISH_FAILED` and the forge's message. On GitLab, inline discussions that landed before the summary was refused stay on the merge request; the job's message gives their count, and the review is recorded as posted. An unreadable verdict fails the job with `ERR_PR_REVIEW_NO_VERDICT`. Either way the verdict is kept in `pr_reviews.verdict`, and `pr_reviews.posted_at` says whether the review landed.

## Re-reviews

A command (a `/embry0 review` comment or the manual trigger) continues the agent session of the pull request's last posted review: the agent is told what changed since the sha it last reviewed, re-checks its earlier findings, and reviews the new commits. When the branch was rewritten since, it reviews the whole change again and still reports on its earlier findings. When there is no session to continue (the first review, a session too large to resume, or a purged job), it starts fresh with the earlier findings, read from `pr_reviews.verdict`, in its prompt.

## Forks and the trust model

A pull request from a fork is always reviewed read-only, whatever the depth: no command runs, the agent has no shell, and the sandbox gets none of the repository's environment variables. A pull request whose head repository is unknown or deleted counts as a fork. Code a job runs can read its sandbox's credentials, including the LLM credential ([ADR 0008](adr/0008-llm-credential-in-sandbox-env.md)), so running a stranger's tests would hand them that credential.

The agent is the Claude CLI working in the checked-out tree, and that CLI reads project configuration from the tree: skills and commands under `.claude/`, MCP servers in `.mcp.json`, and instructions in `CLAUDE.md`. A skill or command can run shell commands when it is invoked, and an MCP server is a process. So when a review runs nothing (a fork, or the `read_only` depth), the checkout removes from the worktree every `.claude/` directory, `.mcp.json`, `CLAUDE.md` and `CLAUDE.local.md` before the agent starts. The agent still reads what those files say, as text in the precomputed diff. embry0 then writes its own `.claude/settings.json` as for every agent. A same-repo review at `full` or `targeted` depth keeps them: its author has write access, and its checks run the repository's code anyway.

By default (`ready_authors: team`) a ready event starts a review only when the pull request's author has write access and is not a bot, the same standard a `/embry0 review` command is held to. A pull request from anyone else is skipped silently: nothing is posted, no trigger key is taken, and the budget is not charged; a team member can still review it with `/embry0 review`. Bots are refused even with an allowed association, so a dependency bot's pull requests do not each start a review. GitHub may report a member of an organization whose membership is private as `CONTRIBUTOR`, so team mode can skip their pull requests. Such a pull request can still be reviewed with `/embry0 review` from someone the gate recognizes (the same association rule applies to the commenter), and making the membership public avoids it.

With `ready_authors: anyone`, anyone who can open a pull request, including from a fork, starts a review when it becomes ready. What bounds that is the read-only rule above and the budget gate: the daily and monthly spend caps (`DAILY_BUDGET_CAP_USD`, `MONTHLY_BUDGET_CAP_USD`) and the per-actor hourly limit (`RATE_LIMIT_PER_AUTHOR_PER_HOUR`), charged to the pull request's author (on GitLab, to whoever opened it or marked it ready). Choose `anyone` only for repositories where that is acceptable.

## Webhook setup

Subscribe the repository's webhook to these events (see [webhooks](webhooks.md)):

- GitHub: **Pull requests** and **Issue comments**.
- GitLab: **Merge request events** and **Comments**.

GitLab review events reach the reviewer whether or not the GitLab issue sync is set up. The reviewer still needs a GitLab credential to post. A GitLab command also needs the GitLab client the orchestrator builds at startup when a GitLab credential exists, to check the commenter's access level; without it the command is refused as `gitlab_not_configured`. Under `ready_authors: team` (the default) a GitLab ready event needs that client too, to check the merge request author's access level; without it the event is refused as `author_not_on_team` (logged with `detail=gitlab_not_configured`).

No new route: reviews ride the existing `/api/v1/webhook` and `/api/v1/webhook/gitlab`, so what each ingress exposes is unchanged.

## Spend

Each review is a job: it passes the dispatch budget gate (daily and monthly caps, the per-actor hourly limit) before it is created and again before it runs, and its spend counts against the per-job cap like any other job's.
