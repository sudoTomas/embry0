# A spent account never fails a job: move to a ready model, or wait for the reset

**Why.** Provider fallback (spec 2026-10-25, provider fallback) moves an agent off a paused account, but four ways still ended a job with "the LLM account it ran on refused it: You've hit your weekly limit":

1. The run that first finds an account spent fails, and only the job's later runs move. The account is quarantined only when the job finishes, so every job that started before then fails the same way.
2. With no fallback order (the default), every model of the order spent, or the local model busy past its slot wait, there is nowhere to move, and the job fails.
3. A model or key a person chose for the job never moves, so it fails.
4. The watcher starts an analysis every tick while the account is spent, and each one fails.

A spent account is a state with a known end: the job should take another ready model or wait for that end, never fail.

## What counts as spent

A pause with a known end:

- a registry account quarantined until a time: `rate_limited`, `quota_exhausted`, and the xAI reasons that map to them (`spending_limit`);
- a run of this job that ended with `ERR_PROVIDER_QUOTA_EXHAUSTED` or `ERR_PROVIDER_RATE_LIMITED`: until the provider's stated reset (capped at eight days, as the quarantine is), else the configured quarantine window for the code;
- the local model with every slot taken past `LOCAL_LLM_SLOT_WAIT_SECONDS` (`local_busy`): five minutes, then the run tries again.

A pause with no end (no key at all, a disabled key, a refused token, a provider this deployment does not enable) still fails the job: waiting would not end it.

## 1. A run that finds its account spent moves at once

When a run ends with one of the two account codes:

- the account that served it is quarantined right away (`CredentialHealth.report_job`), not when the job ends, so the next job's pre-check sees the pause;
- if the fallback order has a model whose provider is ready and whose key the sandbox holds, and the model is not one a person chose, the same run runs again on it. Its trace carries `fallback`; the failed attempt's trace stays as it is. The node sees both outputs, the stand-in's last, as with triage's decide-now retry. Each provider moves once per run.

## 2. Nothing ready: the job waits

**Before the job starts.** The account pre-check (before any container exists) checks the job's main provider (the default model's when one is set, else the provider most agent definitions use) and every provider a person chose for the job (its agent models, a Run with key). When one is spent and no stand-in covers it (a chosen one never moves), the job is parked:

- `status = paused` and `jobs.account_wait` (migration 116) = `{provider, reason, until, model, agent, chosen, watch, mode: "relaunch"}`;
- no sandbox is created;
- a job that prefers the local model while a slot is free (the watcher's analyses) is not parked while that model resolves.

**Inside the job.** For pipelines whose graph resumes from a checkpoint (every one but qa, onboard and pr-review), a run whose provider is spent with no stand-in, found before it runs or after it failed, stops the graph inside that node. It raises `AccountWaitInterrupt`, a `BaseException` like the drain's `ShutdownInterrupt`, so no node's failure path turns it into a failed job. LangGraph keeps the last checkpoint, and the job is parked with `mode: "resume"`. The sandbox stays. A forced local run that found no slot parks the same way for five minutes.

For qa, onboard and pr-review see the addendum below: they park too, and start again from the beginning.

## Going on

The account-wait loop runs every 60 s. It goes on with a parked job when:

- its `until` has passed; or
- `watch` is set (the pause was seen in the registry) and the registry now says the provider, or the person's chosen key, is usable: a person cleared the quarantine or added an account.

It claims the job (`paused` to `running`, `account_wait` cleared, in one statement), then:

- `mode: "resume"` continues from the last checkpoint, and the node that stopped runs again;
- `mode: "relaunch"` starts the job from its launch record (#307).

The pre-check runs first either way, so a job woken while its account is still spent is parked again. A person can press **Try now** (`POST /jobs/{id}/resume` on a waiting job does the same at once) or **Cancel**.

## What people see

- The job reads `paused`. `GET /jobs` and `GET /jobs/{id}` carry `account_wait`, with `until` in UTC ISO.
- An `interrupt` event with reason `account_wait` names the agent, the model and the provider. It names no time.
- The job page shows a banner: what it waits for and when it goes on, in the viewer's own time.
- In the world the party is `waiting` with `account_wait`, and the quest scroll says what it waits for.

## The watcher

Before it pays for an analysis, the tick asks the same question about the analysis job. If the job would wait:

- the tick records `skipped_account_paused`, with the provider and the reason in `detail`;
- it creates no job and leaves the window where it is, so the next tick reads those logs again.

An analysis job that parks mid-run anyway is cancelled by the tick, which records the same action. The new action counts neither as analysed nor as a verdict.

## Addendum: qa, onboard and pr-review park too

**Why they did not.** Their graphs are checkpointed like the others, but a resume cannot go on from inside them:

- pr-review and onboard create their own sandbox in their first node, and qa's runs create one per app inside the fan-out. A resume would have to re-enroll each of them.
- Their nodes wrap the agent run in `except Exception` so that their cleanup node always runs. An interrupt raised from inside the run would skip that cleanup.
- qa's per-app runs are sub-graphs gathered with `asyncio.gather`. A `BaseException` from one of them escapes the gather, leaves its siblings running and skips its own `release_sandbox`. This was also true of the qa stage of issue-to-pr after the first change.

**What they do now.** Each starts again from its beginning once the account is ready (`mode: "relaunch"`). Before a pipeline parks, it reaches a point where its own cleanup has run and nothing has been posted:

| Pipeline | Where it parks | What has happened by then | Why starting again is safe |
|---|---|---|---|
| pr-review | the `cleanup` node, after it destroys the review sandbox | a sandbox, a checkout, the spent run | the review is posted only by `publish`, which a failed review skips; nothing reached the forge |
| onboard | the `cleanup` node, after it destroys the analysis sandbox; `analyze` marks the run failed so the graph goes straight there | a sandbox, the spent run | the config store, or the setup proposal, is written only after `validate` and `smoke`; the proposal is not failed |
| qa | `orchestrate_qa`, after every app's run has released its sandbox and before the results are stored | the per-app runs, each ended and cleaned up | `qa_report` posts the check and the comment after this node; per-app rows are upserted, so the run writes them again |

The same point serves the qa stage of issue-to-pr, which parks with `mode: "resume"`: the stage's fan-out runs again from the checkpoint before it.

**How the wait reaches that point.** An agent run in a pipeline that cannot park from inside a node does not raise. It records the wait on its output entry (`account_wait`), and this works in three cases:

- a run that found its account spent with nowhere to move;
- a run that would have started on an account already known to be spent, which is not started at all;
- a forced local run that found no slot.

The qa per-app runs never raise either, in any pipeline. Each app's result carries the run's `error_code` and `account_wait`, and a failed qa job takes an account code as its own `error_code` (#469). The pipeline raises `AccountWaitInterrupt` at its point above, with the latest `until` of the waits it collected.

**Parking a relaunch.** The executor sees which pipeline raised. For qa, onboard and pr-review it destroys the job's sandbox and volumes, deletes the job's checkpoint thread (a relaunch starts a fresh run), and parks with `mode: "relaunch"`. The account-wait loop starts it from its launch record.

**A per-job model is not a person's.** The pre-check no longer waits for the pr-reviewer's per-job model as if a person had chosen it. Like the run itself, it moves to the fallback order's model.

## Limits

- An account served only from the environment (no registry row) cannot be quarantined, so only the job that hit it knows. Its own later runs wait; other jobs find out by failing their first run, then move or wait.
- A parked job holds no run slot. A `resume`-mode job keeps its sandbox while it waits; the proxies forget a sandbox after 24 h, and the resume re-enrolls it (#476).
- A woken job whose sandbox the proxies will not take back is parked again for five minutes.
