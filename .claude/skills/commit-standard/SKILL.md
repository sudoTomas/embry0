---
name: commit-standard
description: embry0's git commit standard. Use this skill EVERY time you write a commit message — distilled from Chris Beams 7-rules, Tim Pope 50/72, and Conventional Commits, anchored to embry0's existing scope vocabulary.
---

# embry0 Commit Standard

Adapted from the Ormus Commit Standard (OCS v1.0). The shape is **Conventional Commits** with strict 50/72 + embry0's house scopes.

---

## The shape

```
<type>(<scope>): <imperative summary>
                                                       ← blank line
<body — wrap 72, lead with why, then what, then notes>
                                                       ← blank line (if footers follow)
<footer-key>: <footer-value>
```

### Subject — `<type>(<scope>): <imperative summary>`

| Rule | Detail |
|---|---|
| `<type>` | One of `feat fix refactor perf docs test style chore build ci revert` — lowercase |
| `(<scope>)` | Optional. Lowercase, kebab-case. See scope vocabulary below |
| `: ` | Colon + single space |
| `<imperative summary>` | Imperative mood (`add` not `added`). Lowercase first letter. **No trailing period.** |
| Length | Subject ≤ 72 chars. Aim ≤ 50. Hard limit 72 (GitHub truncates `git log --oneline` longer) |
| Banned | Emoji prefixes (gitmoji), AI footers without consent, multi-clause subjects with " and " (split into two commits) |

### Body

- Wrap at 72 columns
- Lead with **why** (the user-facing problem), then **what** (the change), then **notes** (gotchas, alternatives considered, follow-ups)
- Plain prose. No marketing language ("powerful", "stunning", "best-in-class")
- Reference related issues / design docs by issue number or in-repo path

### Footers

| Footer | When to use |
|---|---|
| `Refs: #N` | References issues / PRs the change relates to |
| `Fixes: #N` | Closes issue N when this PR merges |
| `BREAKING CHANGE: <description>` | API/contract change. Required for major-version bumps |
| `Co-Authored-By: <Name> <email>` | Genuine co-authorship only. **NOT** for AI tools unless the user explicitly asks |

**Banned footers**: `🤖 Generated with Claude Code`, `Co-Authored-By: Claude <noreply@anthropic.com>` (default — only with explicit user opt-in)

---

## embry0 scope vocabulary

The scope is the closest meaningful unit. Use these existing scopes from embry0's git history (per `git log --oneline`):

| Scope | Use when touching |
|---|---|
| `qa` | `embry0/workflows/qa/`, qa-related agent seed, qa pipeline graph |
| `integration` | `tests/integration/` |
| `pipeline-editor` | `frontend/src/components/pipeline-editor/` |
| `dashboard` | `frontend/src/pages/DashboardPage.tsx` + dashboard components |
| `stats` | `embry0/api/v1/stats.py` + stats response shape |
| `agents` | `embry0/agents/` + `embry0/storage/repositories/agent_definitions.py` |
| `triage` | The triage workflow node |
| `sandbox` | `embry0/sandbox/` + sandbox image / Dockerfile.sandbox |
| `proxy` | `embry0/execution/proxy/` |
| `storage` | `embry0/storage/` (migrations, repositories) |
| `api` | New `embry0/api/v1/<file>.py` endpoint |
| `divine` | `frontend/src/components/divine/` |
| `badge` | `frontend/src/components/ui/Badge.tsx` |
| `layout` | `frontend/src/components/layout/` (AppLayout, TopBar, Sidebar) |
| `types` | `frontend/src/lib/types/` |
| `intake` | Voice/text intake (when WS4 lands) |
| `notifications` | Notifications inbox (when WS5 lands) |
| `proposals` | Scanner proposals (when WS6 lands) |
| `bug-reports` | Bug-report FAB (when WS3 lands) |

Skip the scope only when the change is genuinely repo-wide (e.g. `chore: bump dependency major across the stack`).

---

## Type taxonomy (embry0 practice)

| Type | Use when | Example |
|---|---|---|
| `feat` | New behavior, capability, or surface visible to a user/caller | `feat(stats): live state + per-repo + top-expensive + recent_issues` |
| `fix` | Bug fix — observable wrong behavior is now right | `fix(qa): destroy sandbox before cleanup_qa_resources removes the qa-net` |
| `refactor` | Internal restructure with **no behavior change** | `refactor(dashboard): rewrite DashboardPage with Companion-style density` |
| `perf` | Performance improvement | `perf(triage): cache pipeline_template lookups` |
| `docs` | Documentation only | `docs: add Companion-pattern-import umbrella + pipeline auto-arrange spec/plan` |
| `test` | Test additions/fixes only | `test(integration): Phase 5 — QA gate routing smoke` |
| `style` | Formatting/whitespace, no logic change | `style: ruff --fix` |
| `chore` | Maintenance, deps, tooling | `chore: add high-standard PR template for the contribution loop` |
| `build` | Build system, Docker, CI infra | `build(sandbox): bundle vibium binary` |
| `ci` | CI workflow changes | `ci: add type-check to lint job` |
| `revert` | Revert a prior commit | `revert: feat(divine): pulse intensity tied to job count` |

---

## Anti-goals (matches embry0's existing anti-goal style)

- **No bare `chore: misc`** — Scope is optional only when the change is genuinely repo-wide. Otherwise pick a real scope.
- **No multi-clause subjects with " and "** — Split into two commits. One concept per commit.
- **No WIP commits on shared branches** — Squash locally before push.
- **No emoji** in any commit message.
- **No promotional language** in the body — pragmatic, fact-based, lead with why.
- **No AI co-author footers** unless the user explicitly opts in.

---

## Quick reference: subject patterns from embry0's existing history

```
feat(qa): destroy sandbox before cleanup_qa_resources removes the qa-net
fix(qa): destroy sandbox before cleanup_qa_resources removes the qa-net
test(integration): Phase 5 — QA gate routing smoke
docs: bring architecture.md + README up to speed with the QA agent
docs: enterprise-scale roadmap note
chore: add high-standard PR template for the contribution loop
feat(pipeline-editor): auto-arrange via dagre
feat(badge,divine,layout): badge primitive + divine identity layer + density opt-in
feat(dashboard,stats): Companion-style dense dashboard with per-repo + top-expensive aggregations
```

Multi-scope commits (comma-separated) are valid when the change inherently spans related areas — e.g. `feat(badge,divine,layout)` because the change ships a new primitive AND wires it into existing layout/divine surfaces.

---

## How to use this skill

When you're about to write a commit message:

1. **Pick the type** — does this commit add user-visible behavior (`feat`), fix wrong behavior (`fix`), or restructure without changing behavior (`refactor`)?
2. **Pick the scope** — what's the smallest meaningful unit being touched? Use one from the table above.
3. **Write the subject in imperative mood** — "add", not "added"; "fix", not "fixes".
4. **Add a body if the why isn't obvious from the subject alone** — wrap at 72.
5. **Add footers only if needed** — `Refs:`, `Fixes:`, `BREAKING CHANGE:`.
6. **No emoji. No AI co-author footer. No "and" in the subject.**

---

*Adapted from `~/.claude/docs/commit-standard.md` (Ormus Commit Standard v1.0). Per-repo `CONTRIBUTING.md` always wins over this skill.*
