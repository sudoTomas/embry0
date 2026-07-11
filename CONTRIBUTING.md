# Contributing to embry0

Thanks for your interest in contributing. This document covers the practical workflow; the [README](README.md) covers what embry0 is and how to run it.

## Development setup

Backend (Python 3.12+, managed with [uv](https://docs.astral.sh/uv/)):

```bash
uv sync
uv run pytest tests/unit -q        # fast unit suite
uv run ruff check embry0 tests    # lint
uv run ruff format embry0 tests   # format
```

Frontend (Node 20+):

```bash
cd frontend
npm install
npm test
npm run build
```

Full stack (Docker):

```bash
cd infra
docker compose --profile images build
docker compose up -d
curl -s http://localhost:8200/health
```

## Ground rules

- **Modularity**: subsystems have clear boundaries and well-defined interfaces, and are independently testable. Keep it that way.
- **Tests accompany changes**: bug fixes come with a regression test; features come with coverage for their contract.
- **No drive-by refactors**: keep diffs scoped to what the PR is about.

## Commit messages

Conventional Commits with a subject ≤ 50 chars, imperative mood, no trailing period; body wrapped at 72 explaining what/why. Scopes in use include: `api`, `sandbox`, `qa`, `frontend`, `infra`, `cli`, `safety`, `execution`, `docs`.

```
fix(sandbox): cap worker threads from container CPU limit
```

## Pull requests

1. Fork / branch from `main`.
2. Make sure `uv run pytest tests/unit`, `uv run ruff check`, and (if you touched it) the frontend test suite and build all pass.
3. Fill in the PR template. Small, focused PRs review faster.

## Security issues

Do **not** open a public issue — see [SECURITY.md](SECURITY.md).
