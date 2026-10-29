# Merge approvals from the operator's chat

Phase 2 of closed-loop self-healing (#319). embry0 asks, in a chat the
operator chooses, whether to merge a PR it opened; a human answers there;
the operator's client relays the answer; embry0 checks it and squash-merges.

```
job completes with a PR on MERGE_APPROVAL_REPOS
  → embry0 records an ask pinned to the PR head sha        (merge_approvals row, pending, posted_at NULL)
  → the sweep waits for that commit's CI to settle          (or MERGE_APPROVAL_CI_WAIT_MINUTES, default 45)
  → merge.approval_requested ─ webhook → the operator's chat   (summary + ✅/⚠️/⛔ recommendation with reasons)
human: "merge it"
  → the operator's client calls merge_decide(approval_id, "approve", decided_by, chat_id, confirm=true)
  → embry0 re-checks, then merges (or reports "would merge" in dry run)
  → merge.completed / merge.failed / merge.stale / merge.rejected ─ webhook → the operator's chat
```

## What the ask says

- **Summary** — the embry0 reviewer agent's own `summary` from the job's
  review; for a PR no embry0 job reviewed, the first prose paragraph of the PR
  body (embry0's boilerplate and bare issue references skipped); else the title.
- **Recommendation** — fixed rules over facts, never a model's opinion (the
  PR text is agent-written and could argue a model into "merge it"). Rules in
  `embry0/services/merge_recommendation.py`:

  | Verdict | When |
  |---|---|
  | ⛔ don't merge | CI red · conflicts with base · gate found new failures on this commit · reviewer requested changes · reviewer saw tests/lint/typecheck fail · a critical review comment |
  | ⚠️ review first | CI not settled by the cap · touches a denylisted path · no embry0 review · important review comments · reviewer unsure it fixes the logged failure · overlaps an open PR · gate error / red base / untested frontend · file list incomplete · large diff (>800 lines or >30 files) |
  | ✅ merge | none of the above |

  "No deterministic test gate configured" is shown as a note, not a
  downgrade: it is true of nearly every PR today and would make every
  verdict "review first".

The recommendation is advice to the human. It never gates the merge — the
checks below do, whatever the recommendation said.

## Nobody has to say "don't merge"

An ask left alone retires itself (`closed` with the reason when its PR is
merged or closed on GitHub; `expired` after `MERGE_APPROVAL_EXPIRY_DAYS`,
default 7). A push to the PR retires the posted ask as `stale` and posts a
fresh one once CI has run on the new commit. "Don't merge" still works, for
anyone who wants the decision on record.

## What embry0 checks before merging

The client asserts who answered and in which chat; embry0 does not trust any
free text beyond that. On every approve:

| Check | If it fails |
|---|---|
| `chat_id` equals `MERGE_APPROVAL_CHAT_ID` (empty = nothing is accepted) | 403, nothing recorded |
| The PR is still open and its head is the sha the ask named | `stale`; a fresh ask for the new head is posted |
| Every check run and commit status on that sha finished passing, and at least one exists | refused, ask stays `pending` |
| The job's verification gate is not `blocking` (new test failures) | refused, ask stays `pending` |
| GitHub reports `mergeable: true` — a `null` (GitHub has not computed it yet, common seconds after CI finishes) is re-read up to 4 times, 3 s apart, before it counts as a refusal (#400) | refused, ask stays `pending` |
| `PUT /pulls/{n}/merge` with `sha` pinned and `merge_method: squash` | 409 → `stale`; other errors → `failed` (may be approved again) |

A PR already merged outside embry0 is recorded as `merged` with its merge
commit, and never merged twice. Two replies racing for one ask cannot both
act: every state change is one conditional `UPDATE`.

Paths on the plan §7.3 denylist (migrations, auth/OIDC/session, infra/compose/
Dockerfiles, `.github/`, dependency manifests and lockfiles, secret-looking
files) are **flagged in the ask and do not block**. The denylist exists to stop
autonomous merges; an approval in the chat is a human merge.

## States

`pending` → `approved` (in flight, or the final dry-run "would merge") →
`merged` | `failed` | `stale`; `pending` → `rejected`; and, with no answer,
`pending` → `closed` | `expired` | `stale`. A dry-run approval
can be approved for real once the mode is `on`.

## Auth: the `merge` key scope

The chat client holds a `merge`-scope API key (admin-minted). It may make every GET,
plus exactly these mutations: `POST /merge-approvals/{id}/decide`,
`POST /cases/{id}/accept` (accepting a watcher finding of a stack this
orchestrator watches, which dispatches one paid job per call), both
agent-question answer routes, and `POST /pipelines/validate`. Everything else
is 403: a leaked copy cannot submit, cancel or resume a job directly, edit env
vars or trigger the watcher, but it can start paid work, one job for each
finding it accepts. The allowlist is matched on the full route template, include
prefix and all (`embry0/api/deps.py:MERGE_SCOPE_MUTATIONS`, read through
`embry0/api/route_table.py`).

## API and MCP

- `GET /api/v1/merge-approvals?status=pending` — asks, newest first, plus the current `mode`.
- `GET /api/v1/merge-approvals/{id}`
- Who decided (`decided_by`) follows [the audit log's rule](api.md#who-changed-something-outside-the-audit-log).
- `POST /api/v1/merge-approvals/{id}/decide`, two forms on one route →
  `{outcome: merged|would_merge|rejected|refused|stale|failed|already_decided, status, detail, …}`:
  - the relay's: `{decision: approve|reject, decided_by, chat_id}`, with `chat_id` checked against `MERGE_APPROVAL_CHAT_ID`; accepted from the `merge`-scope key, the master key or an admin, since `decided_by` names whoever answered (an operate key or an operator session gets 403 pointing at the dashboard's form);
  - the dashboard's: `{decision}` alone, admin tier (an admin session or an admin-scope key); the actor is who authenticated, the stored `chat_id` is the literal `dashboard`, and no chat needs to be configured.
  A body with one of `decided_by` and `chat_id` but not the other is 422.
  Each decision writes a `merge.decided` audit row carrying `via` (`chat` or `dashboard`).
- MCP: `merge_approval_list`, `merge_approval_get`, `merge_decide` (confirm-gated).

## Deciding from the dashboard

The approval chat is one channel; the dashboard's merge-approvals page is the
other (spec 3.8). An admin presses approve or reject there, the dashboard
sends `{decision}` and nothing else, and embry0 names the actor from the
session or key that made the call. The same re-checks run (PR head unchanged,
CI green, verification gate not blocking, mergeable); `MERGE_APPROVAL_REPOS`
still decides which repositories are asked about at all. A `merge`-scope key
cannot use this form: it is the relay's key, and the relay answers for a chat.

The page is **Merge approvals** in the Work section: the asks by status,
pending first, and each ask's own page with the change, the checks, the
reviewer's verdict, the recommendation and the two buttons.

## Rolling out

1. Deploy with `MERGE_APPROVAL_MODE=dry_run` and `MERGE_APPROVAL_CHAT_ID` set.
2. Mint a `merge`-scope service key for that client and swap it in for the operate key.
   The relay cannot decide until it holds that key: the relay's form is refused
   to an operate key with a 403 that points at the dashboard's form.
3. Watch a few asks and "would merge" answers in the chat.
4. Set `MERGE_APPROVAL_MODE=on` in the root `.env` and recreate the orchestrator.
   The token for the repo's owner (`GITHUB_TOKEN__<OWNER>`) needs pull-request
   write (contents: write) for the merge call.
