# Verification: tiers and the base-vs-head test gate

After the reviewer approves a change, the issue-to-PR graph runs a `verify`
node before anything else happens. No agent runs in it and it spends nothing
on a model. It does two things (#298, #299):

1. **Tier** — classify the diff, to decide how much checking the change needs.
2. **Gate** — the orchestrator itself runs the repo's test commands on the
   base commit and on the job's head, inside the dev sandbox, and compares.

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
  install`, global `npm`, `~/.m2`, `~/.cache`), any service it starts, and the
  filesystem outside the two trees. Combined with the run order
  (head #1 → base → head #2), a **head re-run executes against dependencies
  the base run may have installed**, so a re-run that passes for that reason
  is recorded as `flaky` and does not block — the gate fails open there. A
  target that installs its own dependencies deterministically (`npm ci`,
  `poetry install`, `./mvnw -o`) is unaffected; one that relies on ambient
  state is not something this gate can make reliable.
- **Order per target:** head → base → head re-run(s) only if the target looks
  newly broken.
- **How a result is read:** the exit code per target, plus — when the output
  carries them — failing names from pytest (`FAILED path::test[id with
  spaces]`, read to the ` - ` before the message, so two parametrisations
  never collide), Maven Surefire (`[ERROR]   Class.method:line`), `go test`
  (`--- FAIL: Name`) and vitest (`FAIL file > suite > name`). Exit code
  124/137 = timed out (counts as a failure). The last 1 MiB of output is
  parsed; the last 8000 characters are stored, **scrubbed** of
  credential-shaped values and of assignments whose name ends in
  `TOKEN`/`SECRET`/`KEY`/`PASSWORD`/… (`embry0/workflows/verification/scrub.py`).
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
