# Verification: tiers and the base-vs-head test gate

After the reviewer approves a change, the issue-to-PR graph runs a `verify`
node before anything else happens. No agent runs in it and it spends nothing
on a model. It does two things (#298, #299):

1. **Tier** — classify the diff, to decide how much checking the change needs.
2. **Gate** — the orchestrator itself runs the repo's test commands on the
   base commit and on the job's head, inside the dev sandbox, and compares.

Both read the change's file list from the dev sandbox at the start of the
node: the node first stops every process earlier steps left running there
(the [process sweep](#the-process-sweep)), then runs a fresh
`git diff --name-only origin/<base>...HEAD`. It never reuses the list the
review step stored. When the sweep cannot clear the sandbox or the diff
fails, the list is unknown, `[]`, the strict reading: every target runs and
the tier is `frontend`.

Code: `embry0/workflows/verification/` (pure logic + the sandbox runner),
`embry0/workflows/issue_to_pr/nodes/_verify.py` (the node),
`embry0/services/verification_publish.py` (PR comment, label, deliverables).

## Configuration — in the embry0 database, never in the target repo

`repo_preferences.verification` (JSONB), set through
`PUT /api/v1/repos/{owner}/{repo}/-/preferences`:

```json
{
  "verification": {
    "test_targets": [
      {"name": "jvm", "paths": ["platform/**"],
       "command": "cd platform && ./mvnw -pl api -am -q test", "timeout_seconds": 1800},
      {"name": "web", "paths": ["frontend/**"],
       "command": "cd frontend && npm ci --legacy-peer-deps && npm test"}
    ],
    "base_run": "always",
    "head_reruns": 1,
    "total_timeout_seconds": 3600,
    "tiers": {"backend_paths": ["platform/**", "ml/**", "infra/**"]}
  }
}
```

| Field | Default | Meaning |
|---|---|---|
| `test_targets[].name` | — | `[A-Za-z0-9][A-Za-z0-9._-]{0,59}`, unique. |
| `test_targets[].paths` | `[]` | Globs (qa.yaml `changed_paths` grammar). The target runs when a changed file matches; `[]` = always; an unknown diff runs every target. |
| `test_targets[].command` | — | Run with `bash -c` from the root of the checkout under test. Must not hard-code `/workspace` (base runs in a separate worktree) and must do its own dependency install. Max 2000 characters — a longer command is **refused**, never cut. |
| `test_targets[].timeout_seconds` | `1200` | 30–7200. Enforced by `timeout` inside the sandbox. |
| `base_run` | `always` | `on_head_failure` skips the base run for targets that pass on head (same verdict, faster). |
| `head_reruns` | `1` | 0–2. Only a would-be blocker is re-run. |
| `total_timeout_seconds` | `3600` | Budget for the whole gate. Runs that do not fit are recorded as not run → verdict `error`. |
| `tiers.*_paths` | built-in | Each list REPLACES the built-in list of that class; omit to keep it; `[]` = "no such paths". |

A preferences `PUT` is a partial update (#350): only the fields in the body
are written, so setting `verification` leaves `dev_checks`, `base_branch` and
the rest alone. Omit it to keep the stored value; send `{}` or `null` to clear
it:

```bash
API=http://localhost:8200/api/v1/repos/acme/backend/-/preferences
AUTH=(-H "Authorization: Bearer $API_KEY" -H "X-Requested-With: XMLHttpRequest")
jq '{verification: .}' verification.json \
  | curl -s -X PUT "${AUTH[@]}" -H 'Content-Type: application/json' -d @- "$API"
```

No config, no targets, or a stored object that no longer validates → the gate
reports `not_configured`, does not block, and says so on the PR. A job never
fails because of verification config.

`VERIFICATION_MODE` (root `.env`) — **ships in `shadow`**:

- `shadow` (default): decide and store with `mode=shadow`. No blocking, no QA
  re-routing, no PR comment, no label — nothing written outside embry0's own
  database. Promote to `on` once the stored decisions read right.
- `on`: additionally fails a job on a NEW test failure, prunes browser QA by
  tier, and posts the verdict and the `human-merge-only` label on the PR.
- `off`: the node does not run.

An unrecognised value, and a missing config object, are logged and read as
`shadow`. The gate can only *block* a job for a repo that stored
`test_targets`; but note that in `on` mode a repo with **no** stored config
still gets a comment and the `human-merge-only` label on any code change
(`gate_not_configured`), which is why the default is `shadow`.

**The deployed-target QA fix is structural and is NOT gated by this
setting**: the issue-to-PR graph's QA node drops `target: deployed` apps in
every mode, including `off`. Shadow therefore does not re-open the failure
mode that killed the seven jobs.

## The gate

- **Where:** the job's dev sandbox, via `docker exec`. Head runs in
  `/workspace`; base runs in a detached `git worktree` of
  `merge-base(origin/<base>, HEAD)` under `/tmp/embry0-verify/base` (the base
  tip when the shallow clone does not reach the fork point). The worktree and
  logs are removed afterwards.
- **What is isolated, and what is not.** Only the *working tree*: the two runs
  never share checked-out sources or in-tree build output (`target/`,
  `node_modules/` under each tree, `.pytest_cache`). Everything the container
  shares is still shared — packages installed by the command itself (`pip
  install`, global `npm`, `~/.m2`, `~/.cache`) and the filesystem outside the
  two trees. A process a run starts in the sandbox does not outlive it: the
  sweep after the run stops it. So a test that needs a service (a database, a
  dev server) must start it itself, and a build daemon (Gradle, sbt, Bloop)
  starts afresh on every run. Containers a test starts through the sandbox's
  Docker daemon, on repositories that are given one, are not sandbox
  processes and are not stopped. Combined with the run order
  (head #1 → base → head #2), a **head re-run executes against dependencies
  the base run may have installed**, so a re-run that passes for that reason
  is recorded as `flaky` and does not block — the gate fails open there. A
  target that installs its own dependencies deterministically (`npm ci`,
  `poetry install`, `./mvnw -o`) is unaffected; one that relies on ambient
  state is not something this gate can make reliable.
- **Order per target:** head → base → head re-run(s) only if the target looks
  newly broken.
- **How a result is read: from the run's exit status alone.** A run is one
  `docker exec` of a POSIX `sh` script that enters the directory to run in,
  redirects all of its own output to the run's log, runs the command
  (`bash -c`) under `timeout` and exits with the command's status. The
  orchestrator takes that status from the exec itself and times the exec on
  its own clock; nothing the command prints can change either. The exec
  therefore prints nothing. The script runs under `sh` (dash in the sandbox
  images) because bash, started under an `LC_ALL` the image has no locale
  for, prints a warning on stderr before anything can be redirected; the
  command's own bash prints it into the log instead. The verdict is:

  | The exec | Run |
  |---|---|
  | exits 0, prints nothing | `passed` |
  | exits 124 or 137, prints nothing | `timed_out` (counts as a failure) |
  | exits anything else, prints nothing | `failed` |
  | prints anything (a daemon message with 125/126/127, say) | `errored`: the command could not be started |
  | prints the run script's one line: the directory could not be entered | `errored`, with that note |
  | exits 255, prints nothing, and its log cannot be read afterwards | `errored`: docker lost the sandbox |
  | returns no status, or a negative one (the docker client itself was killed) | `errored` |
  | does not return within the orchestrator's limit (the in-sandbox `timeout` plus a grace) | `errored`: the docker client hung, which says nothing about the tests |

  Whatever the exec did, the run is `errored` when the sweep after it does
  not clear the sandbox ([below](#the-process-sweep)). A 255 with nothing
  printed is otherwise the command's own status, `failed`: the sweep and the
  log read that follow show the sandbox still answers.

  An exec that prints past its output cap is stopped and `errored`. Only
  124/137 from the exec itself is `timed_out`, a red run.
- **Failing names come from the log**, read back in an exec of its own (the
  last 1 MiB, `tail -c`) only after the sweep that follows the run: pytest
  (`FAILED path::test[id with spaces]`, read to the ` - ` before the
  message, so two parametrisations never collide), Maven Surefire
  (`[ERROR]   Class.method:line`), `go test` (`--- FAIL: Name`) and vitest
  (`FAIL file > suite > name`). The last 8000 characters are stored,
  **scrubbed** of credential-shaped values and of assignments whose name
  ends in `TOKEN`/`SECRET`/`KEY`/`PASSWORD`/…
  (`embry0/workflows/verification/scrub.py`). A log that cannot be read
  leaves the names unknown, so the target is compared by exit code.
- **The commits come from the prepare step**, which prints
  `EMBRY0_VERIFY_HEAD=<sha>` and `EMBRY0_VERIFY_BASE=<sha>`. Exactly one of
  each, each a full 40-hex sha, or the gate's verdict is `error` and a
  person merges. The head sha stored with the result, the one a merge
  approval compares with the PR's head, is that parsed value.
- **Granularity is recorded, not assumed.** A `FAIL <file>` line with no test
  path (jest, and vitest's file-level line) names a *file*: it can show a
  whole file turning red, but it can never show that nothing new broke inside
  a file that was already red. Such a run is marked `name_granularity:
  "file"`, the verdict's `comparison` is `files` rather than `tests`, and any
  `preexisting_failures` verdict — at either granularity — marks the PR
  human-merge-only (`gate_base_red_unverified`). The gate never claims
  "nothing new" from a comparison that could not have seen it.
- **Cost:** no model spend. Wall-clock only: at most
  `total_timeout_seconds`.

Per-target status:

| Head | Base | Status | Blocks |
|---|---|---|---|
| green | any / not run | `passed` (names that were red on base are recorded as `fixed`) | no |
| red, green on re-run | any | `flaky` | no |
| red | green | `new_failures` | **yes** |
| red | red, test names on both sides | `new_failures` if head has a name base lacks, else `preexisting_failures` (`comparison: tests`) | only if new |
| red | red, only file names | same test, `comparison: files` — a newly red *file* still blocks; "nothing new" is not provable | only if new |
| red | red, names unreadable | `preexisting_failures`, `comparison: exit_code` | no |
| red | could not run | `error` | no |
| could not run | — | `error` | no |

With several head attempts only names failing in **every** attempt count as
new; the rest are `flaky`. **When base itself cannot build**, base is simply
red: nothing is blamed on the change, the target is `preexisting_failures` by
exit code, and the PR is marked human-merge-only because a new failure could
hide behind the old one.

Every `preexisting_failures` verdict, whatever the comparison, marks the PR
human-merge-only: the base is red, so a person decides.

Gate verdict = the worst target: `new_failures` > `error` > `unstable`
(pre-existing and/or flaky) > `passed`; or `not_configured` /
`no_matching_targets` when nothing ran. Only `new_failures` fails the job —
`ERR_VERIFY_NEW_TEST_FAILURES`, stage `verify_failed`.

### The process sweep

Before and after every run (head and base, every attempt), and once when
the node starts, the orchestrator stops the processes left in the dev
sandbox (`embry0/workflows/verification/sweep.py`). An agent can leave a
server or a loop running there, and a test run can leave processes of its
own; either could touch the files or the log a later step reads.

The sweep is one `docker exec` as the container's default user, a bash
script that uses builtins only, so it starts no process itself. It runs
with `LC_ALL=C`: it runs no project code, and bash under a locale the image
lacks would warn on stderr. It stops, with `SIGKILL`, every process that
user owns except PID 1 (the container's init: sandboxes start with
`docker run -d --init … sleep infinity`), the container's own command (the
oldest process other than PID 1, by start time; `sleep infinity`, without
which the container stops) and itself. It repeats kill passes, at most 20
of them a quarter-second apart, until one finds nothing to stop **and**
could read every process it listed: a process whose PID could not be read
keeps the scan open, since it may be one the pass cannot see yet. It
ignores zombies, which have already exited; a process whose main thread
exited while its other threads still run also shows state `Z`, so a zombie
is one whose `Threads:` count is 1. Its one line of output reports what it
stopped, what survived and how many processes it could not read. A process
that survives, one it could not read at the last pass, a sweep that fails,
or a report that is not exactly that one line makes the run `errored`
(`not run: before the run, …` or `not judged: after the run, …`), and the
gate's verdict `error`. Processes of other users, root's among them, are
outside its reach and are left alone.

## Tiers

`classify_diff(changed_files, rules)` is pure. Each path is tested
frontend-first, then backend, then neutral:

| Tier | When | Verification |
|---|---|---|
| `neutral_only` | every file is docs (`docs/**`, `*.md`, `*.rst`, `LICENSE*`, `.gitignore`) | nothing to run |
| `backend_only` | no frontend and no unknown file | the gate; **no browser QA** |
| `frontend` | any frontend file, **any file matching no rule**, or an unknown diff | the gate + browser QA against a stack built from the branch |

Built-in frontend paths: `frontend/**`, `web/**`, `ui/**`, `client/**`,
`**/public/**`, `**/static/**`, `**/templates/**` and the extensions tsx, jsx,
vue, svelte, css, scss, sass, less, html, htm. Built-in backend paths: py,
java, kt, scala, go, rs, rb, php, cs, sql, properties and the usual build
manifests/lockfiles. `.ts`, `.js`, `.json`, `.yml`, Dockerfiles and shell are
deliberately unknown (→ stricter tier) until a repo's rules claim them.

**A branch job never uses QA against a deployed target as a gate.** The
deployed site does not contain the branch, so QA there was inconclusive, the
re-run matched, and approved jobs died with `ERR_QA_FAILURES_UNRESOLVED`
(7 jobs). Two mechanisms:

- `verify` prunes the route plan's QA step unless the tier is `frontend`, triage
  asked for QA, **and** the stored QA config has a `target: managed` app (one
  embry0 boots from the branch). Until the ephemeral branch stack (#302) exists,
  a frontend change in a deployed-only repo skips QA and its PR is marked
  **human-merge-only**.
- The graph's own QA node drops `target: deployed` apps on every QA run inside
  issue-to-PR, however it was reached (`qa.exclude_deployed_apps`).

Deployed-target QA is unchanged for **standalone** QA jobs (`pipeline=qa`).

## The result

One document per job (`VerificationResult`, `schema_version: 1`), stored in
`job_verifications.report` and — with every run's `output_tail` blanked, so
checkpoints stay small — on `JobState["verification"]`:

```json
{"schema_version": 1, "mode": "on",
 "tier": {"tier": "backend_only", "reason": "…", "counts": {…}, "frontend": [], "backend": ["…"], "neutral": [], "unknown": []},
 "gate": {"verdict": "passed", "blocking": false, "base_ref": "main", "base_sha": "…", "head_sha": "…",
          "targets": [{"target": "jvm", "status": "passed", "comparison": "none", "new_failures": [], "preexisting": [], "flaky": [], "fixed": [], "blocking": false}],
          "runs": [{"target": "jvm", "side": "head", "attempt": 1, "outcome": "passed", "exit_code": 0, "duration_ms": 412000, "failed_tests": null, "summary": "Tests run: 212, …", "output_tail": "…"}]},
 "browser_qa": {"planned": "skipped", "reason_code": "backend_only"},
 "blocking": false, "human_merge_only": false, "human_merge_reasons": [], "config_error": null}
```

`job_verifications` columns mirror the parts worth filtering on (`tier`,
`gate_verdict`, `blocking`, `browser_qa`, `human_merge_only`, `base_sha`,
`head_sha`, `mode`). **For auto-merge (#310):** look the verdict up with
`JobVerificationsRepository.latest_for_head(repo, <PR head sha>)` — a verdict
for any other commit is stale — and merge only when `human_merge_only` is
false. `human_merge_reasons`: `no_branch_stack`, `qa_not_requested`,
`gate_new_failures`, `gate_error`, `gate_not_configured`,
`gate_no_matching_targets` (the last two only when code changed),
`gate_base_red_unverified`.

The dashboard reads the row through `GET /jobs/{job_id}/verification`
(404 when none); the job page's Verification tab shows the verdict, the tier,
the gate table from `report.gate.targets` and the browser QA line.

## Two things to know before routing work at this

- **Every builtin template with a developer now has a reviewer** — `verify`
  runs after an approved review, and since 2026-09-27 `quick-fix` is
  triage → developer → reviewer, so a triage that picks it no longer skips
  the gate (it did: the self stack's first accepted fix, #403, merged with no
  verdict). A custom template without a reviewer still gets no verification.
- **A resumed job re-runs the whole gate.** The result lives on the
  `job_verifications` row, not in the resumed stream, and the head may have
  moved — so re-running is correct, but it is not free: budget the same
  wall-clock again on every resume.

At job end (completed or failed, including a job the gate blocked — its PR is
opened by the orchestrator fallback so the verdict has somewhere to go) the
result is published once: three `report` deliverables (base runs, head runs,
verdict — JSON), a sticky comment on the PR/MR, and the `human-merge-only`
label when a person has to merge. The comment never contains command output.
