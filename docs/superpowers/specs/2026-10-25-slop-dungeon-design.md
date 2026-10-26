# Slop is not a bug: the slopometer, the Slopmaster and the Slop Dungeon

**Why.** embry0 files everything it finds as a fix. The watcher's tickets say "Fix …", and triage routes every code job to the developer as a defect to repair. Much of what it finds is not a defect: it is slop. The code works, or fails noisily, because of how it is shaped. A developer handed slop as a bug patches the symptom (another guard, another retry, another catch-all), and so adds more slop. The framing is the problem: the agent is given context that is not accurate.

## What slop is

Every finding and every code job gets a **nature**:

- **bug**: the behavior differs from what the code, its docs or the ticket intend. Fixing it changes behavior.
- **slop**: the behavior is what the code says, and the trouble is the code's shape. Cleaning it keeps behavior and changes shape. Its kinds:
  - `duplication`: the same logic in more than one place, drifting apart;
  - `dead_code`: paths, flags, branches or files nothing reaches;
  - `half_wired`: a feature or abstraction started and not finished, or wired in only partly;
  - `contradicting_layers`: two layers that disagree about the same thing (a config that the code overrides, a validator that a caller bypasses);
  - `symptom_patch`: guards, retries, fallbacks or catch-alls stacked on a symptom instead of its cause;
  - `misleading_docs`: comments or docs that say something the code does not do;
  - `off_pattern`: code that ignores the repository's own way of doing the same thing.
- **noise**: nothing to do.

The **slopometer** is the classifier's output, one shape everywhere:

```json
{"nature": "slop", "slop_score": 72, "kinds": ["symptom_patch", "duplication"],
 "evidence": [{"path": "svc/outbox.py", "why": "three retry wrappers around one insert"}]}
```

`slop_score` (0–100) is how much of the trouble is shape rather than behavior: 0 for a pure bug, 100 for pure slop. `evidence` is at most 8 entries. The parser (`embry0/services/slop.py`) is lenient: an unknown kind is dropped, a score is clamped, a nature outside the three makes the whole verdict absent. An absent verdict changes nothing; it never fails a job.

## Where it is classified

1. **The watcher**, before it files. The analysis contract gains optional lines before `BODY:`:
   ```
   NATURE: <bug|slop>
   SLOP_SCORE: <0-100>
   SLOP_KINDS: <comma-separated kinds>
   SLOP_EVIDENCE: <path> | <why>      (up to 8 lines)
   ```
   Noise stays `NO_ISSUE`, and a proposal whose own verdict says `noise` reads as `NO_ISSUE` too. A slop proposal's title reads as a cleanup ("Clean up …"), its draft ticket opens with a **Slop, not a bug** section that names the score, kinds and evidence, and its case stores the verdict (`healing_cases.work_nature`).
2. **Triage**, for every job. The decision gains an optional `work_nature` in the same shape, filled for code jobs. Triage reads a watcher verdict in the ticket and makes its own call.

## How slop routes

The least invasive sound route is a **template, not a job kind**. `JobKind` is a closed enum mapped to routes and to the API's patterns; slop is still code work, so the job stays `code`. A new builtin template, `slop-cleanup` (triage → slopmaster → reviewer → qa → output), has no `default_for_kind`.

- `plan_route` picks it when triage's verdict is `slop` and the job is code, unless a person named a template for the job. A job working a watcher case may take it too: it keeps review and QA, which is what that rule protects.
- `slopmaster` is a builtin agent definition that runs on the **developer node**: the template step maps to the developer node the way `reviewer` maps to review. The node reads `state.coding_agent` and runs that definition under that agent type, so its traces, events and sessions say `slopmaster` and the Slopmaster earns its own experience. The branch, push, PR and QA machinery is the developer's, unchanged.
- The node measures the cleanup's diff against the base branch (`git diff --numstat`) after the run and stores `{added, removed}` on the job (`jobs.work_nature.cleanup`).
- The reviewer is told the change is a slop cleanup: approve only when behavior is unchanged (tests pass, no public behavior moved), the change removes or merges more than it adds or is plainly simpler, and it adds no new guards, retries, fallbacks or catch-alls.

## The Slopmaster's instructions

Its system prompt (`SLOPMASTER_SYSTEM_PROMPT`):
1. First find out what the code is meant to do: tests, callers, docs, history. Write it down before changing anything.
2. Keep that behavior exactly. Run the tests before and after; add a characterization test where none covers the path being cleaned.
3. Delete, merge, inline and simplify. Prefer a diff that removes more lines than it adds.
4. Never add a guard, retry, fallback, catch-all or flag to make a symptom go away. If the cause is a real bug, stop and say so instead of patching it.
5. Follow the repository's own patterns, and remove the comment or doc that lied.

The job prompt also carries the verdict: score, kinds and evidence.

## Owner proposals

A watcher case whose verdict is slop reads as a cleanup: summary "The watcher found slop …", action "Run the Slopmaster's cleanup on the draft ticket", and `nature`, `slop_score` and `slop_kinds` on the proposal.

## The Slop Dungeon (the world)

- **Leads**: a slop case on the quest board is a `slop` lead: verb **Clean up**, creature `slop_ooze`, led by the Slopmaster. The art manifest has no sheet for the ooze, so it is drawn as an original 12×12 pixel glyph (like the Slop Dungeon's cave), listed in `GLYPH_CREATURES`; the art guard checks the glyph exists and that no sheet claims the kind, so a sheet drawn later replaces it cleanly.
- **Dungeons**: `GET /world` gains `dungeons`, one per repository and area with open slop: `{repo, area, score, open, cleaned}`. The **slop score** of a dungeon is `round(100 × (1 − Π(1 − sᵢ/100)))` over its open slop leads' scores: one heavy item or many light ones raise it, and it falls as each is cleaned. `cleaned` counts the area's slop-cleanup jobs merged in the last 30 days. The map draws the dungeon beside its town (or its region) with its score.
- **The Slopmaster** (avatar `miner`, colour `emerald`) is a character like any agent. A merged slop cleanup earns it `cleaned`: 20,000 coding XP plus 300 per net line removed (`removed − added`, at most 1,000 lines). Opening and merging a cleanup's PR count as the Slopmaster's, not the developer's.

## Storage

Migration 118 adds `jobs.work_nature` and `healing_cases.work_nature` (JSONB, NULL = not classified). Expand-only.

## Not covered

- Tickets people write are classified by triage only when a job starts; the backlog's proposals carry no nature.
- Context-audit findings keep their own framing (a misleading line in an instruction file is already their subject).
- A job that resumes from before this change has no `coding_agent` and runs as the developer.
