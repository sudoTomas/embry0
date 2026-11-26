# Versioning and releases

## Versioning

embry0 follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
The version lives in `pyproject.toml`. Three files carry copies, and
`tests/unit/test_repo_metadata.py` fails when any of them disagrees:
`frontend/package.json`, `frontend/package-lock.json`, and the API's OpenAPI
version in `embry0/api/app.py`.

The version number describes the surface that operators and integrations
depend on:

- the REST API under `/api/v1`, and its WebSocket events;
- the `embry0` CLI and the MCP tools;
- configuration: environment variables and the stored `qa.yaml` schema;
- the plugin entry points `embry0.workspace_providers` and `embry0.workspace_init`;
- the upgrade path. Database migrations run at startup and only move forward.

The Python modules are internals, not a library API.

**Before 1.0.0:**

- **Minor (0.Y.0):** a change that breaks that surface, or that needs the
  operator to do more on upgrade than rebuild and restart. Examples: a new
  required environment variable, a removed endpoint or setting, a manual data
  step.
- **Patch (0.y.Z):** everything else, which means fixes and features that need
  nothing from the operator.

**From 1.0.0** the usual rules apply: major for breaking changes, minor for
compatible features, patch for fixes.

## Changelog

[CHANGELOG.md](../CHANGELOG.md) follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

A pull request that needs operator action on upgrade (a new required variable,
a variable no longer read, an image rebuild beyond the usual deploy, a manual
step) adds its note under **Upgrade notes** of the release it will be part of:
`[Unreleased]`, or `[0.1.0]` until that version is tagged. It says so in its
description too. An operator who runs `main` reads the note before the release
is written.

Pull requests add nothing else to it, because parallel PRs would all conflict
on the same lines. The maintainer writes the rest of each release's entry from
the pull requests merged since the last tag. Their titles are Conventional
Commits, so the type sorts them into Added, Changed, Fixed and so on.

## Releasing

Maintainers only, from an up-to-date `main` with the full test suite green:

1. In `CHANGELOG.md`, move the `[Unreleased]` entries under a new
   `## [X.Y.Z] - YYYY-MM-DD` heading. For 0.1.0, replace `not yet tagged` with
   the date.
2. Set the version to `X.Y.Z` in `pyproject.toml` and in `embry0/api/app.py`,
   and run `npm version X.Y.Z --no-git-tag-version` in `frontend/`, which
   updates both of its package files.
3. Commit as `chore(release): X.Y.Z`, then tag that commit `vX.Y.Z` with an
   annotated tag and push the tag.
4. Create a GitHub release from the tag, using the version's CHANGELOG section
   as its notes.

A release is the tag and its GitHub release. Container images are not
published, and operators build their own from the tag.
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) explains why.

Security fixes go to the latest release and to `main` only, as
[SECURITY.md](../SECURITY.md) says.
