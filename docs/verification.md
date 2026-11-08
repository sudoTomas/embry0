# Verification: tiers and the base-vs-head test gate

After the reviewer approves a change, the issue-to-PR graph runs a `verify`
node before anything else happens. No agent runs in it and it spends nothing
on a model. It does two things (#298, #299):

1. **Tier** — classify the diff, to decide how much checking the change needs.
2. **Gate** — the orchestrator itself runs the repo's test commands on the
   base commit and on the job's head, inside the dev sandbox, and compares.

Both read the change's file list from the dev sandbox at the start of the
node: the node first stops every process earlier steps left running there
(the [process sweep](#the-process-sweep)), then fetches the base branch
from the forge, forced, and runs a fresh
`git diff --name-only origin/<base>...HEAD`, the committed change, which is
what the head run tests. It never reuses the list the review step stored.
The base branch is the job's; when none was chosen for it, the forge's
default branch (`git ls-remote --symref`), never the workspace's
`origin/HEAD`. Like the gate's own reads, these go to the URL embry0 cloned
the repository from, never to a remote the repository's config names, and
are tried up to three times. When the sweep cannot clear the sandbox, the
forge URL is not known or the diff fails (a repository-local `url.*`
rewrite is refused, so it fails too), the list is unknown, `[]`, the
strict reading: every target runs and the tier is `frontend`.

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
| `test_targets[].command` | — | Run with `bash -c` from the root of a fresh checkout of the commit under test. Must not hard-code `/workspace` (`/workspace` is the job's working copy, which no run tests) and must install its own dependencies into that tree: see [What a test target needs](#what-a-test-target-needs). Max 2000 characters — a longer command is **refused**, never cut. |
| `test_targets[].timeout_seconds` | `1200` | 30–7200. Enforced by `timeout` inside the sandbox. |
| `base_run` | `always` | `on_head_failure` skips the base run for targets that pass on head (same verdict, faster). |
| `head_reruns` | `1` | 0–2. Only a would-be blocker is re-run. |
| `total_timeout_seconds` | `3600` | Budget for the gate's runs, each with its own checkout; it starts after the prepare step and the branch-tip read, which sit outside it. Runs that do not fit are recorded as not run → verdict `error`. |
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

- **Where:** the job's dev sandbox, via `docker exec`. **The tested tree is
  the commit's content.** A prepare step resolves the two commits in the
  job's repository at `/workspace`: head is its `HEAD`, base is
  `merge-base(origin/<base>, HEAD)` (the base tip when the shallow clone
  does not reach the fork point), computed after a forced fetch of the base
  branch, so a remote-tracking ref left in `/workspace` does not choose it.
  The fetch must succeed, or the gate's verdict is `error`. The head must be
  the tip of the job's branch on the forge (`git ls-remote`), the commit
  the pull request carries; otherwise, or when the branch cannot be read,
  the verdict is `error` and nothing runs. Both reads go to the URL embry0
  cloned the repository from, never to a remote the repository's own config
  names, and a repository-local `url.*.insteadOf` or `pushInsteadOf` is
  refused like any other key outside embry0's allowlist
  ([sandbox-git.md](sandbox-git.md)). Each read is tried up to three times,
  2 s and 5 s apart, so a short outage of the forge does not decide the
  gate.
- **A fresh tree for every run.** Right before each run, after the sweep, a
  checkout step builds that side's tree: a new, empty repository under
  `/tmp/embry0-verify/head` or `/tmp/embry0-verify/base`, into which embry0's
  git copies exactly the commit's snapshot (the commit, its trees, its
  blobs; no history) out of `/workspace/.git` as one pack. `git index-pack`
  recomputes every object id from its content, so an object whose content
  does not match its id cannot pass as the commit's; the commit and every
  object of its snapshot must then be there, and the commit is checked out.
  The new repository has git's defaults and nothing of `/workspace/.git`:
  no sparse checkout, worktree config, hooks, replacement refs, grafts or
  alternates (embry0's git also ignores replacement refs and grafts
  everywhere, [sandbox-git.md](sandbox-git.md)). It holds that one commit,
  marked shallow, so `git log` and `git describe` answer there, and has no
  remote; submodule directories stay empty. Whatever the source store
  holds (packed or loose objects, alternates, a promisor remote), every
  object the pack carries gets its id from its content, and the check
  wants the commit's whole snapshot under the ids the commit names: the
  tree is that exact content, or the run errors. The checkout reads only
  git's defaults and embry0's pinned config, and `GIT_*` variables cannot
  be set in a sandbox's environment (the reserved-variable check refuses
  them). The
  same command builds both sides, head re-runs included, and the previous
  run's tree is removed first, so no run starts from what an earlier one
  left in its tree or wrote into the other side's. A checkout that fails
  makes that run `errored` and the gate's verdict `error`, never a pass: a
  person merges. The scratch directory is removed after the gate, whatever
  is in it (a read-only module cache, a symlink in its place).
- **What is isolated, and what is not.** The tree is: what `/workspace`
  holds and the commit does not (build output, a stray or ignored file, a
  local config, an uncommitted edit) is in no run's tree, and no two runs
  share checked-out sources or in-tree build output (`target/`,
  `node_modules/`, `.pytest_cache`). Everything outside the tree is still
  shared — packages and caches installed earlier in the job or by an
  earlier run (`pip install`, global `npm`, `~/.m2`, `~/.cache`) and the
  rest of the filesystem. An editable install made earlier in the job that
  points at `/workspace/src` still imports the working copy's code, so a
  target must install into its own tree (or reinstall from it) rather than
  rely on what is installed. A process a run starts in the sandbox does not
  outlive it: the sweep after the run stops it. So a test that needs a
  service (a database, a dev server) must start it itself, and a build
  daemon (Gradle, sbt, Bloop) starts afresh on every run. Containers a test
  starts through the sandbox's Docker daemon, on repositories that are
  given one, are not sandbox processes and are not stopped. Combined with
  the run order (head #1 → base → head #2), a **head re-run executes
  against dependencies the base run may have installed outside its tree**,
  so a re-run that passes for that reason is recorded as `flaky` and does
  not block — the gate fails open there. A target that installs its own
  dependencies deterministically into its tree (`npm ci`, `poetry install`
  into an in-tree venv, `./mvnw -o`) is unaffected; one that relies on
  ambient state is not something this gate can make reliable.
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
  (`embry0/workflows/verification/scrub.py`). Head and base are compared
  by the names as read; the report then carries every test name (each run's
  and each verdict's lists) and each run's summary line through the same
  scrubber, so the stored result, the job's summary and the pull request
  comment show them scrubbed. Each test keeps its own entry, and a name's
  closing `]` is kept: two tests whose names scrub to the same text are
  listed, and counted, as two. The scrubber also takes, by value, the job's
  secret-typed env vars and the values of its secret-looking ones (as its
  sandbox had them), this process's secret-looking ones and the credential
  registry's, for the tail, the names, the summary and the excerpts in the
  orchestrator's log lines alike, the process sweep's included. A log that
  cannot be read leaves the names unknown, so the target is compared by
  exit code.
- **The commits come from the prepare step**, which prints
  `EMBRY0_VERIFY_HEAD=<sha>` and `EMBRY0_VERIFY_BASE=<sha>`. Exactly one of
  each, each a full 40-hex sha, or the gate's verdict is `error` and a
  person merges. The head sha stored with the result, the one a merge
  approval compares with the PR's head, is that parsed value, which the
  forge has confirmed as the branch's tip, and every checkout is made from
  these two values.
- **Granularity is recorded, not assumed.** A `FAIL <file>` line with no test
  path (jest, and vitest's file-level line) names a *file*: it can show a
  whole file turning red, but it can never show that nothing new broke inside
  a file that was already red. Such a run is marked `name_granularity:
  "file"`, the verdict's `comparison` is `files` rather than `tests`, and any
  `preexisting_failures` verdict — at either granularity — marks the PR
  human-merge-only (`gate_base_red_unverified`). The gate never claims
  "nothing new" from a comparison that could not have seen it.
- **Cost:** no model spend. Wall-clock: the prepare step and the
  branch-tip read, then at most `total_timeout_seconds` for the runs, each
  with its checkout. Each forge read is tried up to three times, 2 s and
  5 s apart (one that runs out of time is not tried again, but one that
  fails just short of it is), so the prepare step's worst case outside the
  budget is about three times its 180 s timeout plus 7 s, and the
  branch-tip read's three times its 60 s plus 7 s. A checkout
  takes about twice as long as a `git worktree add` of the same commit
  (3.3–3.9 s against 1.4–1.9 s for a synthetic repository of 50,000 files,
  245 MB of text in a 165 MB pack, on a fast disk), and the tree's
  repository holds a pack of the snapshot besides the files, about the
  size of the compressed snapshot. Every run checks out and installs from
  an empty tree: base always did, and now head and every head re-run do
  too, instead of reusing whatever the job or an earlier run left
  installed. That is the price of a verdict about the commit. Only one
  tree exists at a time, but with `read_only_root: true` in the sandbox
  profile `/tmp` is a tmpfs, whose contents count against the sandbox's
  memory limit: the tree, its pack and its install must fit there beside
  the tests.

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

### What a test target needs

Each run starts in a fresh tree of its commit, with nothing installed in it.
A command that relied on what the job had already installed or generated in
`/workspace` now gets different verdicts:

| The command | Before | Now |
|---|---|---|
| runs the tests without installing, both sides' failures unreadable | head green from `/workspace`'s installs | both sides red, names unreadable: `unstable`, a person merges |
| runs the tests without installing, the change adds a test whose name both sides print | head green | the new name fails only on head: `new_failures`, which ends the job in `on` mode |
| installs cold within a `timeout_seconds` sized for a warm run, `head_reruns: 0` | head green | head `timed_out`, base green: `new_failures`, blocking |

So a test target's command should:

- install its dependencies into the tree from the commit's own manifests
  (`npm ci && npm test`; `python -m venv .venv && .venv/bin/pip install -e
  . && .venv/bin/pytest`; `./mvnw -q test`), not rely on anything already
  installed, and not on an editable install that points at `/workspace`;
- generate whatever ignored files its tests read (`cp .env.example .env`,
  code generation);
- initialise submodules itself if it needs them, with absolute URLs: the
  tree's repository has no remote;
- not depend on git history or tags: the tree's repository holds the one
  commit (`git describe` finds no tag);
- set `timeout_seconds` to cover a cold install and the tests;
- set `total_timeout_seconds` to about two to three times one target's
  head, base and re-run together, for every target a change can select.

**Before switching `VERIFICATION_MODE` to `on`, check every stored
`test_targets[].command`** against this list: a command that worked against
`/workspace` can block jobs now.

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

### What the sweep assumes

Two limits remain, and the gate's guarantees are only as strong as they
allow.

- **The kernel restricts same-user tracing.** The sweep, each run and each
  log read run as the sandbox's user, the same user as the processes the
  sweep is there to stop. The gate assumes none of those processes can
  trace (`ptrace`) another process of that user: the kernel's Yama setting
  `kernel.yama.ptrace_scope` is 1 or more (the kernel's own default when it
  has Yama; some distributions lower it to 0). At 1 a process may trace
  only its own descendants, unless it holds `CAP_SYS_PTRACE` (which a
  sandbox never does) or the traced process allows it; 2 and 3 are
  stricter. At 0, or on a kernel without Yama, that assumption does not
  hold, and the gate's verdicts rest on less than this page describes.
  The setting is the host kernel's one value, which every container on the
  host shares, so the orchestrator reads it at startup from its own
  `/proc/sys/kernel/yama/ptrace_scope` (sandboxes run under runc, on the
  host kernel, on either runtime). The check describes the orchestrator's
  host kernel only: a sandbox daemon on another host, or a runtime with a
  kernel of its own (gVisor, Kata), reads a different kernel, and the check
  says nothing about it. When it reads 0, finds no Yama, or cannot read a
  value from 0 to 3, it logs `ptrace_scope_permissive` at warning and writes
  a `kernel.ptrace_scope.flagged` audit row, at every start; it starts all
  the same. Both carry a `reason` and a `remedy`, which differ per reason:

  - **`same_user_ptrace_allowed`: the setting is 0.** On the host:

    ```bash
    sudo sysctl kernel.yama.ptrace_scope=1
    echo 'kernel.yama.ptrace_scope = 1' | sudo tee /etc/sysctl.d/99-ptrace-scope.conf
    ```

    then restart the orchestrator; the warning is gone from its log.
  - **`yama_absent`: the kernel has no Yama.** There is no
    `/proc/sys/kernel/yama/ptrace_scope`, so there is no setting for
    `sysctl` to change. The host needs a kernel with Yama enabled: built
    with `CONFIG_SECURITY_YAMA`, and named in `lsm=` when the kernel command
    line sets that list. Boot it, check the setting reads 1 or more (set it
    as above if it reads 0), then restart the orchestrator.
  - **`unreadable`: the value could not be established.** The orchestrator
    could not read the file, or read something other than a value from 0 to
    3, so it cannot tell whether the kernel restricts tracing; the kernel
    may well be fine. Compare `cat /proc/sys/kernel/yama/ptrace_scope` on
    the host with the same read in the orchestrator's container. The host
    should print 1, 2 or 3 (0 is the first case above); when it does and the
    container cannot read the file, the container's `/proc` or a security
    profile stands in the way, and the orchestrator needs read access to
    that one file. Restart the orchestrator once it can read it.
- **A repository given its own Docker daemon.** A repository with the
  Docker grant ([SECURITY.md](../SECURITY.md#what-does-not-hold)) can start
  containers through the sandbox's daemon. Those are not sandbox processes:
  the sweep does not see them, so one started earlier in the job can still
  be running while the tests run, and what it does is outside the gate's
  view. For such a repository the gate's verdict is weaker than for one
  without the grant; its code is trusted code already.

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
