# ADR 0021: An organization sets its own context audit caps, and the deployment's are the defaults

Status: accepted (2026-10-23)

ADR 0020 capped the context audit schedule at `CONTEXT_AUDIT_MAX_JOBS_PER_TICK` jobs per tick across every organization, each job capped at `CONTEXT_AUDIT_JOB_BUDGET_USD`. This record corrects that clause. An organization's `context_audit_budget_usd` (more than 0 and at most 50 USD) and `context_audit_max_jobs_per_tick` (0 to 50) bound its own audits; they are written with `PATCH /orgs/{owner}` like its interval and read the same way, so a value a PATCH would refuse reads as unset, and unset the two variables apply. Each cap holds per tick for its own organization's repositories, so one organization's due repositories never wait behind another's, and a tick starts at most the sum of the caps. The per-job cap stays a hard stop; the fleet budget caps and the per-author rate limit on `embry0:context-audit` still bound the whole.

Evidence: `ContextAuditLoop.jobs_per_tick` and `ContextAuditLoop.budget_usd` in `embry0/services/context_audit.py`; `context_audit_budget_usd` and `context_audit_max_jobs_per_tick` in `embry0/services/organizations.py`.
