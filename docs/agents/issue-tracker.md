# Issue tracker: Linear

Issues and planning for this repo live in Linear — team **embry0 (`EMB`)** — not GitHub Issues.
The public GitHub repo (`sudoTomas/embry0`) is a PR-only surface; do not open GitHub issues.

## Access

- **Preferred:** the `linear-embry0` MCP server (registered in the local Claude Code config;
  tools appear as `mcp__linear-embry0__*`).
- **Fallback:** Linear GraphQL API (`https://api.linear.app/graphql`) with the API key read
  from `~/.linear-embry0.key` as the `Authorization` header. Never print, log, or commit the key.

## Conventions

- Team key `EMB`. Active project for QA/pipeline work:
  "ai-quoting Autonomous QA & Pipeline Optimization" (EMB-27…EMB-36). File new work there
  when it fits; otherwise file to the team without a project.
- Titles use the repo's commit-style prefixes (`feat(qa):`, `fix(api):`, `chore:` …).
- Priorities: 1 Urgent / 2 High / 3 Medium / 4 Low.
- Search the team for duplicates before creating an issue.
- Reference tickets as `EMB-<n>` in branch names and PR descriptions.

## When a skill says "publish to the issue tracker"

Create a Linear issue in team `EMB` (MCP `create_issue`, or GraphQL `issueCreate`).

## When a skill says "fetch the relevant ticket"

Fetch the `EMB-<n>` issue with description and comments via the MCP or GraphQL.

## Pull requests as a triage surface

**PRs as a request surface: no.**
