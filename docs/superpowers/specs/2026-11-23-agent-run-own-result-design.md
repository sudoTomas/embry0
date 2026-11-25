# An agent run's own result

Date: 2026-11-23. Ships with the change that implements it.

## What the orchestrator reads, and from whom

An agent run is the sandbox runner (`python -m embry0.sandbox.runner`), which the orchestrator starts with `docker exec` as the sandbox's user. Everything the run reports reaches the orchestrator as JSON lines on the runner's stdout: its events while it works, then one `final_result` line, which becomes the run's `AgentOutput` (its output, cost, error code, session).

The code a job runs (the agent's tools, the repository's own scripts, anything they leave running) is a process of that same user. Linux lets a process open another process of its user through `/proc/<pid>/fd/<n>`, `/proc/<pid>/mem` and `/proc/<pid>/environ` unless that process is not *dumpable*. The runner was dumpable, so anything of the user could open its stdout and add lines to it, or open its stdin and read the config the orchestrator sent. The orchestrator forwarded only the agent-run event types since the previous change on this branch, but it still took the *last* `final_result` line it read as the run's result.

## What changes

### 1. No other process of the user can open the runner

- **The runner starts on an interpreter its user may run but not read**: `/usr/local/bin/embry0-runner-python`, a copy of the image's python, root-owned, mode `0711`, installed by `infra/Dockerfile.sandbox` and `infra/Dockerfile.sandbox.qa` before they switch to the agent user. When a program its user cannot read is executed, the kernel sets the new process's dumpable flag from `fs.suid_dumpable` instead of 1, so the runner is closed from its first instruction. Every image built `FROM` the slim image (dev-python, dev-jvm-python, profile images) inherits the interpreter. `RUNNER_COMMAND` starts it by absolute path; `-P -s` stay.
- **It depends on the sandbox's capabilities.** The kernel checks that the program is unreadable with the credentials of the process that executes it, and `docker exec` executes the runner directly. So the runner starts closed only when that process holds neither `CAP_DAC_OVERRIDE` nor `CAP_DAC_READ_SEARCH`, as in every sandbox: they run with `--cap-drop=ALL` and `no-new-privileges`, and a profile cannot add a capability back (`embry0/execution/profile_isolation.py`). Measured in the image: executed directly in a container with those flags, the interpreter starts with the flag at 2; with Docker's default capabilities it starts at 1, open (through a shell it starts closed either way, because the shell itself runs without capabilities). A runner started open seals itself and reports it (below). CI's QA image job executes the interpreter directly in a container run with the sandbox's flags and expects it closed, and with Docker's default capabilities and expects it open, so a change to either side fails there.
- **The runner seals itself first thing** (`seal()`, `prctl(PR_SET_DUMPABLE, 0)` through ctypes, before its other imports): for an interpreter the user can read, and as the second of two independent steps.
- **Why both.** The second alone leaves the interpreter's start-up open. Measured in the sandbox image as the agent user, with a process polling `/proc` for a runner and opening its `fd/1`: on the image's own python it opened the stdout of 25 of 25 runner starts although each runner sealed itself; on the execute-only interpreter it opened none of 25.
- **What it does not change.** A process the runner starts is dumpable again: an exec resets the flag. The CLI and its tools talk to the runner over pipes of their own, never through the runner's stdout, and run as before (checked in the image: the CLI ran under a sealed runner and its events flowed). `/proc/<pid>/stat`, `status` and `cmdline` stay readable, and a process of the same user may still signal it: the idle watchdog's `pkill -f embry0.sandbox.runner` and the test gate's sweep work as before (checked in the image). Nothing in embry0 ran `py-spy`, `gdb` or a debugger against the runner, and no image collects core dumps; a sealed runner leaves none, and its failure is still reported by its exit code and stderr.
- **The runner says whether it started open.** `seal()` reports whether the process was dumpable until then, and the runner writes it on its result as `open_at_start`. The orchestrator logs `sandbox_runner_open_at_start` (a warning) when it is true: the runner ran on a readable interpreter, or the host's `fs.suid_dumpable` is 1, which makes an unreadable program dumpable too. The host setting is the kernel's one value for every container on it; 0 and 2 both close the process.

### 2. The run's result carries the run's mark

- **The orchestrator makes a mark per run** (`secrets.token_urlsafe(32)`) and sends it with the config on stdin, under `result_nonce`, with the flag `--result-nonce` telling the runner the config carries it. Never on argv or in the environment: other processes of the user can read both.
- **The runner keeps it apart** (it never reaches the agent's invocation) and writes it on its `final_result` line only, as `nonce`.
- **The orchestrator takes a result only when it is the one `final_result` line of the run and carries the run's mark** (compared with `hmac.compare_digest`). Otherwise it fails the run: `ERR_SANDBOX_RESULT_REFUSED`, with a warning `agent_runner_result_refused` naming the reason and how many results it read. A second `final_result` fails the run whichever line is the runner's: the orchestrator cannot tell, so it trusts neither. A run with no result stays `ERR_AGENT_NO_RESULT`.
- **The mark goes no further**: the run's output and the orchestrator's logs never carry it.
- **The event-type allowlist stays**, as a second, independent check on everything else the runner's stdout carries.

### 3. An image older than this

Images the deploy recipe does not rebuild keep the runner of the day they were built: prebaked per-repo QA images whose base digest was never recorded (selection still uses them), and profile images built `FROM` an older base.

**Decision: such an image runs agents for this release, and is refused by the next one.** (Done: the release after refuses it, #673.)

- An image without the interpreter: `docker exec` exits 127 naming it, before anything ran. The orchestrator runs the same command once more on the image's own `python`, with the mark.
- A runner that does not know `--result-nonce`: argparse refuses the flag (exit 2, `unrecognized arguments: --result-nonce`, no stdout line) before the runner imports anything. The orchestrator runs it once more without the flag and without the mark, and takes its result unmarked. A second `final_result` still fails the run.
- Neither retry can run an agent twice: each fires only when the first exec wrote no stdout line and exited with that exact failure.
- Each is a `sandbox_runner_stale_image` warning with an `outcome` and a count on `embry0_sandbox_runner_stale_image_total{outcome="interpreter_missing"|"result_unmarked"}`, beside the existing `argv_fallback` and `config_too_large`.
- Why not refuse now: refusing would fail every run on those images the moment this deploys, before an operator could rebuild them, and they run with exactly the exposure they had before this change. Refusing in the next release gives the counter one release to show which images are left. That change replaces both retries with `ERR_SANDBOX_IMAGE_STALE`.
- **The refusal (#673).** Both retries are gone. Either failure, still only before anything ran, fails the run with `ERR_SANDBOX_IMAGE_STALE`: the image, what it lacks, and that no agent ran. The counter keeps both outcomes, now meaning refused, and the log line is an error. The refusal is worded for any capability an image built before the release lacks, so a later one (a provider whose key moves) refuses the same way.

## Deploying it

Sandbox-baked code changes (`embry0/sandbox/runner.py`, `embry0/execution/agent_runner.py`) and so do both sandbox Dockerfiles: rebuild every sandbox image with the recipe in `docs/ops/deploy.md` ("Images: registry, pins and rebuilds"). Then rebuild prebaked QA images listed as `unknown`, and profile images, or watch the stale-image counter for them. `docs/ops/deploy.md` has the check that an image carries both the interpreter and the mark.

## Tests

- `tests/unit/execution/test_agent_run_result.py`: the mark rides stdin only and differs per run; a marked result is taken and the mark goes no further; a result without the mark (absent, another, null, a number, a list) fails the run; a second result fails it in either order, and when both carry the mark; both older-image retries, alone and together, and that neither fires once the runner did anything or for another failure; the runner starts on its interpreter; `open_at_start` is logged only when true.
- `tests/unit/sandbox/test_runner_seal.py`: a process of the same user cannot open a running runner's `fd/1`, `fd/0` or `mem`; `seal()` reports whether the process was open; what a sealed runner starts is dumpable again; the runner writes the mark on its result and nowhere else, and reports `open_at_start`.
- `tests/unit/infra/test_runner_interpreter_image.py`: both images install the interpreter `0711` as root before switching user, and fail the build unless the agent user can run it, cannot read it and runs the SDK check on it.
- CI's QA image job executes the interpreter directly, as the orchestrator does, in a container run with the sandbox's flags (`--cap-drop=ALL`, `no-new-privileges`) and expects it closed; with Docker's default capabilities, and the image's own python, it expects it open, so the check can fail. `tests/unit/infra/test_runner_interpreter_image.py` pins that step's flags.
