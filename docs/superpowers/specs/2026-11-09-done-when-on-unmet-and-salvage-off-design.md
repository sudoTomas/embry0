# Done-when on unmet, and salvage off: the rest of #182

Status: decided (2026-11-09). Refs #159, #182. Builds on #280, which moved `done_when.retries`, `done_when.ticket_sections` and `salvage.commit_subject` into the settings document (decision L4 of `2026-10-29-settings-layers-design.md`, ADR 0028), and on the done-when (`2026-10-26-done-when-design.md`) and salvage (`2026-10-25-salvage-unfinished-work-design.md`) specs. Line numbers are at `b573c569`.

**Why.** #280 left two keys of #182 open because they are new behaviour, not values that move out of the code:

- **`done_when.on_unmet`.** A job whose coding runs still leave done-when items unmet after its retries always pauses for a person. On a deployment where nobody answers pauses, that job sits until the paused TTL expires it. It then ends `expired`, with its sandbox destroyed and no pull request, though its branch is on origin.
- **`salvage.enabled`.** A stopped coding run's leftovers are always committed by embry0 as `wip: unfinished — …` and pushed. Some repositories take no commit embry0 wrote: they require signed commits, or forbid unreviewed work pushed in embry0's name. There the salvage commit is refused, or it breaks a rule.

## 1. What happens today (the defaults reproduce it byte for byte)

**An unmet finish line.**

- **The check.** After a coding run that pushed (verified), `nodes/_done_when.check_done_when` (`_done_when.py:47-108`) reads the report strictly. While `done_when_retries` is below `done_when.retries` it sends the work back.
- **Once the retries are spent.** It writes:
  - a progress event, `Done when: still not met after a retry: <items>`;
  - stage `done_when_unmet`, with the same sentence appended to `errors`.

  It then goes to `max_retries`, which pauses with that sentence and the options continue, merge as is and stop (`_interrupts.py:166-178`).
- **The developer's prompt** says what is coming: "an unmet or unreported item sends the work back once, then pauses the job" (`done_when.sent_back`).

**Salvage.** `developer_node` (`_developer.py:1229-1330`) calls `_salvage.preserve_unfinished_work` in three places:

- question rounds used up;
- a soft budget overrun;
- a run that stopped: its last coding output is an error, the turn cap included.

Each call does three things:

1. It reads `git status --porcelain`.
2. On a dirty tree, it moves to the job's branch if needed, then runs `git add -A` and `git commit` with the `commit_subject` template.
3. The rescue then pushes every unpushed commit, the agent's own included.

The routing that follows:

- **A stopped run that got a commit** pauses at `dev_run_unfinished` with `ERR_DEV_RUN_UNFINISHED`. Review never runs on it.
- **One pushed with nothing committed** goes on along its route.
- **One that pushed nothing** is judged by the branch probe: on origin it goes on, otherwise it pauses at `dev_branch_missing`.

What depends on a salvage commit:

- the next run's prompt and the pause text;
- a failed job's fallback pull request (#349, `_open_fallback_pr`);
- a later job continuing that pull request (`_continue_failed_pr`).

Nothing else reads salvage commits: not case evidence, not resume, not the Slop-O-Meter. A paused job keeps its sandbox across a pause, a resume and an orchestrator restart, and nothing resets `/workspace` between runs. An abandoned, failed or expired job's sandbox is destroyed.

## 2. `done_when.on_unmet`: `pause | review | fail`, default `pause`

It applies only once the retries are spent. The strict check is unchanged. It is the done-when counterpart of `budget.overrun_mode`, where `soft` pauses and `hard` fails.

| Value | What the job does |
|---|---|
| `pause` | Today's behaviour, byte for byte. This is also the "hand it to a person" value; routing a pause to a named person is #179's ask policy. |
| `review` | The route goes on as if the items were met. The list keeps its report, so review's prompt already shows each item `NOT met` as "a reason for changes_requested", and QA adds the items to its criteria. Review decides; the report is the developer's claim, not proof. Review's own retry limit bounds it. No error is recorded, and a progress event says the change goes on along its route as it stands (a route may put QA before review). The report is marked `on_unmet: review`, on state and on the job row, so the pull request the job is delivered in lists the items not met, as the `fail` pull request does (decision S4 of the review). |
| `fail` | The job ends at stage `done_when_unmet` with a new `ERR_DONE_WHEN_UNMET` (category `verification`), with the same error text the pause gives. The check runs only after a verified push, so the branch is on origin. The existing failed-job path then applies: the fallback pull request says "embry0 did not finish this job", names the unmet items and carries `human-merge-only`, and a later job of the issue continues it. A job that already has a pull request (a follow-up's, or an earlier failed job's it continued) gets `human-merge-only` and a sticky comment with the same note and items there (`embry0.services.unfinished_note`, through the calls the QA and verification comments use, on GitHub and GitLab). A later job that finishes cleanly on a pull request it had from its start edits that comment to say so; one with no such comment gets nothing, and the label stays for a person to remove. A run that ended in an error (its turn cap, a crash) keeps that error as the job's failure; its pull request still lists the items, as any failed job's does when its last report left items unmet. |

**`review` with no review step left in the route** (`route_plan.review_ahead`) pauses exactly as `pause` does, so unmet work is never delivered unjudged. `nodes/_done_when.unmet_action` returns the effective value. The developer's contract is worded from it, so the contract never promises a review the route does not have, and the pause says why it paused.

The developer's contract follows the value:

- `pause`: "…sends the work back once, then pauses the job" (unchanged);
- `review`: "…then hands it to review as it stands";
- `fail`: "…then fails the job".

With no retries, only the last part is said ("hands the work to review as it stands").

**Not offered.**

- **"Deliver as draft".** The forge layer has no draft pull requests, and `fail` already delivers the branch as a pull request only a person merges, marked unfinished.
- **"Deliver as done".** It would present unmet work as a finished delivery with nobody's say. It can be added later as another value.

## 3. `salvage.enabled`: `true | false`, default `true`

`false`: embry0 never makes a commit of its own for a stopped run, the turn-cap commit of #358 included. The job pauses where the commit would have paused it. It also pauses where salvage on could not make its commit (the commit failed, or another branch is checked out while the job's branch exists) and so goes on as if the run had left nothing: with salvage off the changes are still in the sandbox, and a cut-off run is never a delivery. Everywhere else the route is the one salvage on takes.

| A stopped run that left… | Salvage on | Salvage off |
|---|---|---|
| uncommitted changes | committed and pushed; pause at `dev_run_unfinished` | not committed. The same `git status --porcelain` finds them, and nothing is staged, switched or committed. The job pauses at the new stage `dev_run_uncommitted` (`ERR_DEV_RUN_UNFINISHED`). The pause says the work is in this paused job's sandbox, not on its branch, and that stopping the job or letting the pause expire discards it. |
| only the agent's own unpushed commits | pushed by the rescue; the route goes on | the same: the rescue pushes only commits the agent made |
| nothing | its old path (goes on, or `dev_branch_missing`) | the same |
| uncommitted changes salvage on cannot commit (the commit fails, or another branch is checked out while the job's branch exists) | goes on as if the run had left nothing (review, or `dev_branch_missing`) | pause at `dev_run_uncommitted`, as above |

- **Budget and question stops** commit nothing. The pause note says the changes are uncommitted in the sandbox.
- **The next coding run** after a continue is told that its earlier run's changes are uncommitted in this working tree, and to review them, finish them, run the checks, then commit and push.
- **The record** on state is `{branch, agent, reason, committed: false, uncommitted: true, pushed}`. A record written with salvage on is unchanged, and so are its texts.
- **"Merge as is" is not offered** at a pause whose record says the changes are only in the sandbox (`dev_run_uncommitted`, and a budget or questions pause with salvage off): it would complete the job, open a pull request from a branch without them and destroy the sandbox. `POST /jobs/{job_id}/resume` (and the chat's `POST /asks/jobs/{job_id}/resume`) answers 409 naming what the pause offers for any choice the pause's newest `interrupt` event does not list, so the job stays paused with its sandbox (decision a of the second review); a pause recorded without options takes any choice, and the node still reads one it did not offer as a stop.
- **With salvage on, "merge as is" after a salvage commit** (decision P2 of the review) completes the job with a branch ending in embry0's "Do not merge it as it is" commit. The pause hands the salvage record on with its answer, since a resume's result holds only the nodes' updates, and the job's pull request is opened `human-merge-only` with the note a failed job's carries; a pull request the job already had gets the label and the note as a comment.

**What turning it off costs.**

- The work survives only while the paused job's sandbox does.
- A job that fails or expires leaves nothing on origin for its fallback pull request, or for #349 to continue.
- A questions-exhausted pause offers no continue, so its uncommitted changes are lost when it is stopped.

## 4. Layers

Both keys are in `org_keys` and `repo_keys`, beside `retries`, `ticket_sections` and `commit_subject`:

- each touches only the jobs of the repository that reads it;
- neither carries operator-written text into a prompt (`on_unmet` picks one of three fixed sentences), so a repository's layer, which a repository-scoped key may write, may hold them;
- `review` spends review runs inside the job's own budget cap, and `fail` spends less than a pause.

## 5. Reading and writing

A job reads both keys with the rest of its settings, once, when it starts: `read_for_job` dumps the whole `done_when` and `salvage` models into the checkpointed `job_settings`. A resume keeps them. A job started before this release has neither key, and `job_section` reads their defaults with no warning, which is what that job did.

**Readers, all through `job_section(state, …)`:**

- `on_unmet`:
  - `check_done_when`;
  - the developer's contract (`done_when.contract(…, on_unmet=)` and `sent_back`, a required argument with no default);
  - the `done_when_unmet` pause.
- `enabled`:
  - the three salvage calls in `developer_node` (`preserve_unfinished_work(…, commit=)`, required);
  - the `dev_run_uncommitted` pause, the pause note and the next run's prompt, which read the record.

**Not settings reads:**

- the new code's category, public sentence and `_stopped_because` words, which are also the failed job's headline;
- the unmet items in a failed job's pull request (whenever its last report left items unmet), read from the job row first (a resumed job's state holds only what ran after its pause), and the note on a change request the job already had (`_note_on_existing_change_request`, through `embry0.services.unfinished_note`), and the settling of that note by a later job that finishes;
- `job_status_words` and `_STAGE_CODES` for `dev_run_uncommitted`.

**Writing.** Nothing is per key:

- **API.** `PUT /config/settings` and `PATCH /config/settings/{section}`, each with `?owner=` or `?repo=`, answer 422 at the key: a strict `Literal` names its three values, and a strict `bool` says it takes a boolean.
- **CLI and MCP.** The `embry0 settings` verbs with `--org` and `--repo`, and the MCP settings tools.
- **Dashboard.** The Policy tab reads `settable` and the effective view from the API.
- **Docs.** The generated reference and example list both keys.

## 6. Tests

- **Byte-identical defaults.** `tests/unit/workflows/test_unmet_and_salvage_defaults.py` drives 36 scenarios through the nodes, for 5 kinds of job state:
  - the scenarios: the contract and the check on each route shape; every salvage path through a fake sandbox git; each pause; the next run's prompt;
  - the job states: one that read nothing, one that read the defaults, the release before's stored sections, retries 0, and retries 2 with a subject of its own.

  It compares them with values captured from the tree before this change (`tests/fixtures/golden/done_when_and_salvage_defaults.json`, from `b573c569`).
- **Every new test fails on that tree** at its own assertion. The golden test passes there by design and fails under the mutations that change a default.

## 7. Delivery

- One pull request with no migration and no variable.
- `embry0/safety/error_codes.py` gains `ERR_DONE_WHEN_UNMET`. The sandbox images copy that module, but nothing in a sandbox reads the new member, so no image is rebuilt: the orchestrator image only (`infra/deploy.sh`).
- Until someone writes either key, every job behaves as before, but for four deliberate changes from the reviews:
  1. "Merge as is" after a salvage commit opens its pull request `human-merge-only`, saying the branch ends in that unfinished commit (P2).
  2. A resume choice the pause did not offer is a 409 at `POST /jobs/{job_id}/resume` and the chat's route, and the job stays paused; it used to be read as a stop (decision a).
  3. A failed job's pull request names the done-when items its last report left unmet (N-c). With nothing configured, a failed follow-up or continuation job whose last report had unmet items now also gets the "did not finish" comment and `human-merge-only` on the pull request it already had, where nothing was posted before; a failed follow-up whose list was all met, or that had none, gets nothing, as before.
  4. A job that finishes cleanly on a pull request it had from its start reads that pull request's comments once, to settle an earlier job's "did not finish" note (N-d). A job a person completed with "merge as is" settles nothing (`merged_as_is`, set by the pause), since it delivered what stood rather than finishing it.
