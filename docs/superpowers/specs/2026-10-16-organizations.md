# Organizations: hold the owner, discover its repositories, say what embry0 needs

Status: slice 1 of 8 shipped with this document (ADR 0010).

## Problem

Every sense embry0 has is switched on per repository. Registration, webhooks, tracker mapping, QA configuration and log watching are each a manual step, taken once per repository by a person who knows the steps. Under an owner with many repositories, most end up never registered, hooked, watched or given a QA configuration, even when the credential in use could reach every one of them.

## Outcome

A person adds a forge owner and embry0 works out, repository by repository, what it would take to maintain it, then tells the person what it needs from them and does the rest itself when asked.

## Shape

1. `organizations` (this slice): owner, forge, credential, probe result (`access`), repo count, status, settings. `POST /orgs` probes the credential against the owner with real forge calls and refuses an owner the credential cannot list. `GET /orgs`, `GET /orgs/{owner}`, `POST /orgs/{owner}/probe`, `PUT /orgs/{owner}/status`. MCP: `org_list`, `org_get`, `org_register` (confirm).
2. `org_repo_facts`: one row per repository per discovery run: archived, default branch, language, pushed_at, open issues and PRs, frontend present, boot hint (package manifest dev script or compose file), tests and CI present, env keys from `.env.example`, access per sense, and embry0's own state (registered, hook, QA config, watcher stack, tracker mapping, deploy marker). `POST /orgs/{owner}/discover` runs it as an analysis-class job so progress streams like any job.
3. `org_needs`: the aggregated list a person must act on, each need also a case awaiting decision so the overview can show it. Kinds: token scope, tracker choice, onboarding candidates, log source missing, deploy marker missing, env values needed, budget.
4. An org-scout agent writes one sentence per repository and the needs narrative, from the facts only.
5. Actions: register selected repositories, install webhooks where the probe found hook access, start onboarding jobs for repositories with a boot hint, create disabled watcher stacks, apply the default tracker. Every action idempotent; a second run is a no-op.
6. Wizard in the dashboard: credential, discovery with live progress, report with one chip per sense, actions with a summary. An organization page keeps the report.
7. Rediscovery on a schedule: a new repository files a need; a vanished one proposes deactivation.
8. Standards (design and later others) keyed by owner, with per-repository mode.

## Rules

- The probe trusts only real calls. `permissions` on a repository reflects the user, not the grant.
- A push grant is proven only by writing a ref, as a separate explicit action; this slice leaves `push` as `null`.
- Registering an owner changes nothing for any repository until a person acts.
- Owner is stored lowercase. A slash is a repository, refused.
- GitLab: read and identity are probed in this slice; webhook access is reported as not probed.

## Verification for this slice

- `tests/unit/services/test_organizations_probe.py`: org-admin token reads and manages hooks; a scoped token is read-only; an invisible owner is not readable; a GitLab group reads with hooks unprobed; the probe never asks for `permissions`.
- `tests/unit/api/v1/test_organizations_api.py`: registry credential probed and stored with its source; env token fallback named by key; unreadable owner refused and not stored; non-forge credential refused; `probe: false` stores as given; slash refused; status and removed filtering; re-probe rewrites access.
- `tests/unit/storage/test_organizations_repo.py` (requires_postgres): idempotent register, lowercase owner, access and discovery updates, removed filter, revival on re-register, refusals.
