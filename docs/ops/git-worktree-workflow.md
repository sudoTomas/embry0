# The Git Worktree Workflow — one-pager

**The rule this exists to enforce: the main checkout is what's deployed.**
The directory a server (or an AI agent polling the repo) runs from must
always match `origin/main` exactly — never a half-finished branch, never
uncommitted edits. All work happens somewhere else.

## What is a worktree? (from zero)

A git clone normally gives you ONE working directory; switching branches
changes the files in place. `git worktree` lets one clone check out
**several branches at once, each in its own directory**. They share one
`.git` history (no duplicate clones, instant creation), but files in one
directory never touch the others.

```
~/repos/myservice            ← main checkout: ALWAYS on main = deployed
/fast/myservice-worktrees/
  fix-login/                 ← worktree on branch alice/fix-login
  new-report/                ← worktree on branch bob/new-report
```

## The workflow (create → branch → PR → merge → prune)

```bash
# 0. One-time: pick a home for worktrees OUTSIDE the main checkout
mkdir -p /fast/myservice-worktrees

# 1. Start a task: fresh worktree on a fresh branch off origin/main
cd ~/repos/myservice
git fetch origin
git worktree add /fast/myservice-worktrees/fix-login \
    -b alice/fix-login origin/main

# 2. Work there — edit, commit, repeat. The main checkout is untouched.
cd /fast/myservice-worktrees/fix-login
...edit...
git add -A && git commit -m "fix(auth): handle expired session on login"

# 3. Push and open a PR
git push -u origin alice/fix-login
gh pr create --base main

# 4. After the PR merges: update the main checkout, then prune
cd ~/repos/myservice
git pull --ff-only origin main        # deploy step happens from here
git worktree remove /fast/myservice-worktrees/fix-login
git branch -d alice/fix-login
```

## Rules

1. **Never commit, branch, or edit in the main checkout.** It moves only
   by `git pull --ff-only origin main`. If `git status` there is ever
   dirty, something went wrong — fix that first.
2. **One worktree per task, fresh off `origin/main`.** Don't reuse an old
   worktree for a new task; `git fetch` first so "main" means today's
   main, not last week's.
3. **Everything reaches main through a PR.** No direct pushes to main.
4. **Prune when done** (step 4) — stale worktrees accumulate confusion.

## Why this matters for AI-polling repos

Agents (embry0, Talon, deploy pollers) read the main checkout assuming it
IS production. A branch checked out there, or uncommitted edits, silently
feeds the agent — and whatever it deploys or reviews — a state that exists
on nobody's PR. The worktree layout makes that impossible by construction.

## Handy commands

```bash
git worktree list          # every checkout of this clone and its branch
git worktree remove <dir>  # remove one (refuses if it has dirty files)
git worktree prune         # clean up records of manually deleted dirs
```

*Live example: embry0 itself is developed exactly this way — deploy
checkout at `~/repos/embry0` (always main), one worktree per issue under
`/fast/embry0-worktrees/`.*
