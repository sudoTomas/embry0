# Local verification

Run these before every commit; CI runs the same commands.

## One-time: toolchain and Postgres for the test suites

Install the dev extra first — `ruff`, `mypy` and `pytest` live there, not in
the default dependency set, so a bare `uv sync` leaves the gates unrunnable:

    uv sync --extra dev

Then the Postgres the suites connect to:

    docker run -d --name embry0-test-pg -e POSTGRES_USER=embry0 -e POSTGRES_PASSWORD=embry0_ci \
      -e POSTGRES_DB=embry0_unit_test -p 5432:5432 postgres:16.6-alpine
    docker exec embry0-test-pg psql -U embry0 -d embry0_unit_test -c 'CREATE DATABASE embry0_integration_test;'
    export TEST_DATABASE_URL=postgresql://embry0:embry0_ci@localhost:5432/embry0_unit_test

## Gates

    uv run ruff check .
    uv run ruff format --check .
    uv run mypy embry0
    uv run pytest tests/unit/ -n 4 -v --tb=short --timeout=60
    docker build -f infra/Dockerfile.proxy -t embry0-proxy:latest .
    TEST_DATABASE_URL=postgresql://embry0:embry0_ci@localhost:5432/embry0_integration_test \
      uv run pytest tests/integration/ -v --tb=short --timeout=120
    (cd frontend && npm ci && npx tsc -b && npm run lint && npm run test -- --run && npm run build)
    docker run --rm -v "$PWD/infra/prometheus:/rules:ro" -w /rules --entrypoint promtool \
      prom/prometheus:v3.14.0@sha256:5ce7540c3c00ef4ab0c9d2c995c6a5b9c421f44b4a115d97a2c7af3b1c21cbb0 \
      test rules offhost-backup.rules.test.yml job-events.rules.test.yml background-enrollment.rules.test.yml

The last gate runs the alert rules in `infra/prometheus/` against their
`promtool` tests; it prints `SUCCESS` and exits 0 on a pass.

Integration tests that need something the run lacks skip, and say why:
`requires_minio` and `requires_dind` tests without a reachable
`MINIO_ENDPOINT` or `DIND_ENDPOINT`, the isolation tests without
`EMBRY0_ISOLATION_PROBE_DOCKER_HOST`, the Maven cache test without the JVM
sandbox image, and the proxy reachability tests without
`EMBRY0_PROXY_PROBE_ORCHESTRATOR`. CI has none of these. The proxy
reachability tests exec into the orchestrator container that variable names
and start a sandbox in its DinD, so name only a stack you may do that to.

Everything else that uses Docker runs whenever `docker` reaches a daemon, as
it does on CI's runners. Those tests start containers from
`embry0-proxy:latest` and create the `sandbox-restricted` and
`sandbox-internet` networks on that daemon. On a host that runs a stack you
care about, point `DOCKER_HOST` at a throwaway daemon, or at none to skip
them.

## Name denylist

`scripts/check_denylist.py` fails when a file names something on a denylist:
customers, people, hosts. The terms are kept outside the repository, because a
test that spells out the names it guards publishes them. Only maintainers hold
the file, so contributors skip this gate.

    EMBRY0_DENYLIST_FILE=/outside/the/repo/denylist.txt python3 scripts/check_denylist.py --public
    EMBRY0_DENYLIST_FILE=/outside/the/repo/denylist.txt python3 scripts/check_denylist.py docs frontend/src

- The file holds one Python regular expression per line, matched
  case-insensitively against every line and every file path; `#` starts a
  comment. Keep it mode 600.
- `--public` scans the whole tree with the allowances the public export
  passes (below), read from `scripts/export_public.py`. This repository is the
  public one, so that is the check CI runs.
- Without paths or `--public` it scans `DEFAULT_SCOPE` in the script, the
  paths the retired in-tree name guards covered. In a git checkout only
  tracked files are scanned.
- Exit 0 is a pass. Exit 1 is a match, reported as file, line and denylist
  line, never the matched text. Exit 2 means nothing was checked: no
  `EMBRY0_DENYLIST_FILE`, an empty or unreadable denylist, a denylist inside
  the scanned tree, a scope path that is missing or empty, or a directory or
  git listing that cannot be read. Treat 2 as a failure.

**CI.** The denylist is the repository secret `EMBRY0_DENYLIST`. The
`denylist` job writes it to `$RUNNER_TEMP`, outside the checkout, and runs the
check with `--public`, without `continue-on-error` or `|| true`, so exit 1 and
exit 2 both fail it; an unset secret is exit 2. GitHub gives no repository
secrets to a pull request from a fork or from Dependabot, where the check could only exit 2, so
those runs skip the step and post a notice saying so. Every push to `main` and
every pull request from a branch of the repository runs it.

**Public export.** `scripts/export_public.py` scans the whole exported tree
and stops on any non-zero exit. Its allowances are the public repository's
canonical URLs, which name its owner, in any file; the owner's handle in
`.github/CODEOWNERS`; and each name it writes to `CONTRIBUTORS`, in that file
only. `--allow LITERAL` masks an exact literal before the patterns run (not
when it continues into a longer word, so a sibling repository's URL is not
covered), and `--allow-in PATH LITERAL` does so in one file only. The same
scan by hand, on an exported tree:

    EMBRY0_DENYLIST_FILE=/outside/the/repo/denylist.txt \
      python3 scripts/check_denylist.py --root "$EXPORT_DIR" --public

## CI

`.github/workflows/ci.yml` runs the gates above on every push to `main` and
every pull request to it, on GitHub-hosted runners only: pull requests from
forks run it, so it must never be given a self-hosted runner. Jobs:

- `backend`: ruff, the app import, and the unit suite against a Postgres
  service container of its own.
- `mypy`, `frontend`: the type check and the frontend gates.
- `build-images`, `build-qa-sandbox-image`: build the images with
  `push: false` and smoke-test the QA image. Nothing leaves the runner.
- `security`: hadolint, Trivy (fixable CRITICALs) and gitleaks.
- `integration`: the integration suite against its own Postgres, with the
  proxy image on the runner's daemon.
- `denylist`: the name denylist, above.

The token is `contents: read` for every job, checkout keeps no credentials,
and the only secret is `EMBRY0_DENYLIST`. Actions are pinned to commit shas
and the Postgres image to a digest; `tests/unit/test_ci_workflow.py` fails if
any of that changes. Python dependencies come from `uv.lock`
(`uv sync --locked`), with the uv version set in the workflow.

Dependabot is configured by `.github/dependabot.yml` in the public repository
only. The private development repository leaves the file out, so that
Dependabot opens no pull requests where CI does not run; the public export
adds it. The validated file is kept in a comment on issue 272 of the private
repository.

## Images

After changes under `embry0/sandbox/`, `embry0/safety/`, `embry0/agents/`, the
baked `embry0/execution/` modules, `embry0/execution/proxy/` or
`infra/Dockerfile.*`, rebuild the sandbox and proxy images with the recipes in
[deploy.md](deploy.md#images-registry-pins-and-rebuilds). `infra/deploy.sh`
does not rebuild them.
