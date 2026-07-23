# Issue tracker: Linear

Issues and planning for this repo live in Linear — the **RavenCargo workspace**, project
**"Embry0 Platform — Non-Code Agents & Guardrails"** — not GitHub Issues.
The public GitHub repo (`sudoTomas/embry0`) is a PR-only surface; do not open GitHub issues.

> **Tracker moved 2026-07-23.** Historical issues (EMB-1…EMB-54) live in the personal
> workspace (`tomas-mcmonigal`, team `EMB`) and stay there read-only for reference; the
> `linear-embry0` MCP still reaches them. Do NOT file new work in the old workspace.
> The label-trigger webhook (EMB-47) was rewired to the RavenCargo workspace on
> 2026-07-23 (RAV-1023): labeling an issue `embry0` in a mapped project triggers the
> pipeline. Currently mapped: "Raven AI Quoting Platform" → `raven-cargo/ai-quoting`.

## Access

- **RavenCargo workspace (current):** Linear GraphQL API (`https://api.linear.app/graphql`)
  with the RavenCargo API key, or a RavenCargo-authenticated Linear MCP when connected.
  Never print, log, or commit the key.
- **Historical (read-only):** the `linear-embry0` MCP server (personal workspace, team `EMB`;
  tools appear as `mcp__linear-embry0__*`).

## Conventions

- File new work to the **"Embry0 Platform — Non-Code Agents & Guardrails"** project in the
  RavenCargo workspace.
- Titles use the repo's commit-style prefixes (`feat(qa):`, `fix(api):`, `chore:` …).
- Priorities: 1 Urgent / 2 High / 3 Medium / 4 Low.
- Search the team for duplicates before creating an issue.
- Reference tickets by their RavenCargo identifier in branch names and PR descriptions.

## When a skill says "publish to the issue tracker"

Create a Linear issue in the RavenCargo project above (GraphQL `issueCreate`).

## When a skill says "fetch the relevant ticket"

Fetch the issue with description and comments from the RavenCargo workspace; fall back to
the old `EMB-<n>` workspace only for pre-2026-07-23 history.

## Pull requests as a triage surface

**PRs as a request surface: no.**
