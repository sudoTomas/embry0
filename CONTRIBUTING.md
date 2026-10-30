# Contributing to embry0

Thanks for your interest in contributing. This document covers the practical workflow; the [README](README.md) covers what embry0 is and how to run it.

## Development setup

Backend (Python 3.12+, managed with [uv](https://docs.astral.sh/uv/)):

```bash
uv sync --extra dev                # the dev extra holds pytest, ruff and mypy
uv run pytest tests/unit -q        # fast unit suite
uv run ruff check .                # lint
uv run ruff format .               # format
```

Tests that need Postgres connect to `TEST_DATABASE_URL` (default `postgresql://embry0:embry0@localhost:5432/embry0_test`); without a database they skip or fail. **Point it only at a scratch database:** the fixtures drop and recreate its `public` schema (`DROP SCHEMA public CASCADE`), empty every table between tests, and create the database if it is missing, so everything in it is destroyed. Never use a deployment's database. [docs/ops/verification.md](docs/ops/verification.md) starts a throwaway Postgres container for this, and lists every gate CI runs.

Optional: `uv tool install pre-commit && pre-commit install` runs ruff and some basic file checks on each commit ([.pre-commit-config.yaml](.pre-commit-config.yaml)).

Frontend (Node 22.13+):

```bash
cd frontend
npm install
npm test
npm run build
```

Full stack (Docker). Compose run from `infra/` interpolates `infra/.env`, and the orchestrator reads the repo-root `.env` (ADR 0005), so fill in both templates first:

```bash
cp .env.example .env               # the containers' settings and secrets
cp infra/.env.example infra/.env   # what compose interpolates; the shared values must match
cd infra
docker compose --profile images build
docker compose up -d
curl -s http://localhost:8200/health
```

Or `embry0 start` from the repo root, which reads only the root `.env` (see the README).

## Ground rules

- **Modularity**: subsystems have clear boundaries and well-defined interfaces, and are independently testable. Keep it that way.
- **Tests accompany changes**: bug fixes come with a regression test; features come with coverage for their contract.
- **No drive-by refactors**: keep diffs scoped to what the PR is about.
- **Secret settings**: a new `Embry0Config` field that holds a credential is typed `SecretSetting`, so `embry0 config` and logs mask it. A field whose name only looks secret (it contains `token`, `secret`, `pass`, `key`, `credential` or `dsn`) is typed `Annotated[<type>, NOT_SECRET]`. `tests/unit/test_config_redaction.py` fails while any such name is unclassified.
- **Vendor-neutral naming**: see below.

## Vendor-neutral naming

embry0 is a general-purpose product under the MIT license. A capability that any
operator can use must not be named after the first customer, deployment or
repository that motivated it. This applies to **branch names, file names,
identifiers, config keys, log fields, test fixtures, commit subjects and
user-facing strings** — not just to code.

The distinction is capability versus configuration:

- **Capability** — multi-tenant hosting, per-instance credentials, a sandbox
  profile. Always generic: `tenant-hosting`, not `acme-tenant-hosting`;
  `dev-jvm-python`, not `acme-backend`.
- **Configuration** — one operator's URLs, hostnames, tokens, VM sizes. Belongs
  in a deployment's own environment or database, not in this repo. Where a
  concrete value genuinely has to appear in-tree (a runbook, a worked example),
  prefer a placeholder such as `gitlab.example.com` or `acme/backend`.

Why it matters beyond tidiness: a customer's internal hostname in a user-facing
error message or a test fixture is published to everyone who installs embry0,
and it survives in git history long after the name is changed.

Two specific traps this project has already hit:

- **Error messages are user-facing strings.** An `e.g.` in a validation message
  is read by every operator, not only by the person who wrote it.
- **Test fixtures propagate.** The first example URL written into a test becomes
  the example the next twenty tests copy.

Known violations are tracked as their own issue rather than fixed piecemeal; a
rename touching git history is deliberate, coordinated work, not a drive-by.

Maintainers check names with `scripts/check_denylist.py`, against a denylist
kept outside the repository; see
[docs/ops/verification.md](docs/ops/verification.md#name-denylist). Don't
write a test that spells out a name it guards against: that publishes the
name.

## Commit messages

Conventional Commits with a subject ≤ 72 chars, imperative mood, no trailing period; body wrapped at 72 explaining what/why. Scopes in use include: `api`, `sandbox`, `qa`, `frontend`, `infra`, `cli`, `safety`, `execution`.

```
fix(sandbox): cap worker threads from container CPU limit
```

## Pull requests

1. Fork / branch from `main`.
2. Make sure `uv run pytest tests/unit`, `uv run ruff check`, and (if you touched it) the frontend test suite and build all pass.
3. Fill in the PR template. Small, focused PRs review faster.
4. If the change needs operator action on upgrade, such as a new required setting or a manual step, say so in the description. The release notes repeat it ([docs/releasing.md](docs/releasing.md)).

## Security issues

Do **not** open a public issue — see [SECURITY.md](SECURITY.md).
