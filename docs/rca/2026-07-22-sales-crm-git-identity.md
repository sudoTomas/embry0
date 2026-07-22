# RCA — sales-CRM run: GitHub push rejections + slowness (EMB-51)

**Date of report:** 2026-07-22 (standup) · **RCA written:** 2026-07-22
**Reporter:** internal sales-CRM pilot (embry0 run on a repo new to the pipeline)
**Symptoms:** (1) GitHub rejected the pipeline's pushes, citing a fabricated
author email; (2) the run was "painfully slow".

## 1. Push rejections from a fabricated git email — ROOT CAUSE FOUND, FIXED

### Root cause

embry0 sets the sandbox git identity itself, and the two pipeline paths
diverged:

| Path | Identity | Where set |
|------|----------|-----------|
| issue→PR (opens PRs) | `embry0` / `embry0-bot@users.noreply.github.com` (env-overridable) | `embry0/workflows/issue_to_pr/nodes.py` via `embry0/branding.py` |
| QA (all QA sandboxes) | **`embry0 QA` / `qa-agent@embry0.local` — hardcoded** | `embry0/workflows/qa/_subtask_prep.py` (pre-fix) |

`qa-agent@embry0.local` is a non-routable address GitHub cannot associate
with any account; org-level push rules (verified-email metadata rulesets,
DCO-style checks) reject commits carrying it. The issue-path default is a
plausible noreply address but is also rejectable wherever an org restricts
committer emails to its own domain or to real member noreply addresses.
Neither identity was configurable per repo, and no test pinned the QA-path
value — the hardcoded string shipped unnoticed.

### Fix (this change)

- **One resolver for every path**: `embry0/sandbox/git_identity.py` —
  branding default (env-overridable via `EMBRY0_GIT_AUTHOR_NAME` /
  `EMBRY0_GIT_AUTHOR_EMAIL`) with a **per-repo override** stored in
  `repo_preferences` (`git_author_name` / `git_author_email`, migration 41).
  The fabricated `.local` identity is gone.
- Override is settable via `PUT /api/v1/repos/{owner}/{repo}/preferences`
  and the dashboard's Sandbox Preferences form.
- Tests now pin the QA-path identity (no `.local`, branding default,
  override honored) so a regression cannot ship silently.

### Remediation for the sales-CRM repo

If the target org enforces domain-restricted committer emails, set the repo
override to an address its rules accept, e.g.:

```bash
curl -X PUT http://localhost:8200/api/v1/repos/<owner>/<sales-crm>/preferences \
  -H 'Content-Type: application/json' -H 'X-Requested-With: XMLHttpRequest' \
  -d '{"git_author_name": "embry0", "git_author_email": "<bot>@<accepted-domain>"}'
```

Deployment-wide, set `EMBRY0_GIT_AUTHOR_EMAIL` in the orchestrator env
instead.

## 2. Run slowness — ROOT CAUSE PENDING JOB IDS

The sales-CRM repo has **never run on this deployment** (verified against
this instance's `jobs` table 2026-07-22: no sales-CRM rows). The affected
runs happened on the reporter's own embry0 instance; their job ids,
`traces`, and `job_logs` rows are needed to profile token counts, turn
counts, and wall-clock per node.

Working hypotheses, in likelihood order (from the ai-quoting baselines of
$1.25 sonnet / $2.17 grok-direct / 3–9 min per QA run):

1. **Cold repo** — a repo new to the pipeline has no prebaked QA image and
   no warmed shared volume, so every sub-task pays full clone + dependency
   install. First runs on ai-quoting behaved the same before its image was
   baked.
2. **Push-rejection retry thrash** — symptom 1 compounds symptom 2: agents
   that can't push burn turns retrying/diagnosing, inflating wall-clock and
   cost.
3. **Missing repo conventions** — no `.embry0/qa.yaml` tuned for the repo
   and no `repo_preferences` row (language hint, sandbox profile) forces
   triage and agents to spend turns discovering the stack.

**Action:** obtain job ids + timestamps from the reporter, pull
`traces`/`job_logs` for those runs, and profile per-node wall-clock and
token counts against the ai-quoting baselines. If cold-start dominates,
the fix is operational (bake the QA image / warm the volume for the repo —
same path EMB-48/EMB-50 formalize); if turn thrash dominates, the push fix
above removes the biggest source.
