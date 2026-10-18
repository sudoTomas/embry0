# Architecture Decision Records

An ADR records one decision that shaped embry0 and is expensive to re-litigate: what was decided, and the evidence — an incident, a migration, a doc or a commit — that forced it. It is not a design doc and not a spec. If you need vocabulary, read [`docs/glossary.md`](../glossary.md); if you need the system reference, read [`docs/architecture.md`](../architecture.md).

The records below are retrospective: they describe decisions the codebase already embodies, written down so agents and new contributors stop rediscovering them.

## Naming

`NNNN-kebab-title.md`, where `NNNN` is the next free four-digit number. Numbers are never reused, and an ADR is never rewritten to say something else — supersede it with a new one and set the old file's status to `superseded by ADR NNNN`.

## Template

```markdown
# ADR NNNN: short title in the imperative or as a statement

Status: accepted (YYYY-MM-DD)

One paragraph stating the decision and its consequence — what is true now,
and what is therefore forbidden or unnecessary.

One paragraph of evidence: the commit, migration, doc or incident that forced
it, cited by hash, path or date. If no single commit carries the decision, use
the date of the commit that landed it and say so.
```

Keep each record under ~150 words. `Status:` is one of `accepted`, `superseded by ADR NNNN`, or `withdrawn`, always with the date. Dates come from this repo — `git log --date=short --format='%ad %h %s' -- <path>` — never from memory.

If your work contradicts a record here, say so explicitly rather than silently overriding it (see [`docs/agents/domain.md`](../agents/domain.md)).

## Index

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-langgraph-agent-sdk-dind-sandboxes.md) | LangGraph + Claude Agent SDK; agents execute in DinD sandboxes, no customer code retained on the host | accepted (2026-04-04) |
| [0002](0002-credential-proxies-per-sandbox-enrollment.md) | Credential proxies with per-sandbox enrollment, no forge tokens in the sandbox env | accepted (2026-04-28) |
| [0003](0003-db-backed-qa-config.md) | qa.yaml v2 lives in the embry0 DB; legacy config channels removed | accepted (2026-08-06) |
| [0004](0004-github-issues-canonical-tracker.md) | GitHub Issues is the canonical tracker; Linear is transitional | accepted (2026-08-07) |
| [0005](0005-two-env-files.md) | Two `.env` files — the repo-root one is the container runtime env | accepted (2026-07-18) |
| [0006](0006-credential-registry.md) | Credentials live in an encrypted registry, resolved through one service | accepted (2026-09-29) |
| [0007](0007-git-proxy-forwards-smart-http.md) | The git-proxy forwards git smart HTTP for the job's repository; the forge token never reaches the sandbox | accepted (2026-10-08) |
| [0008](0008-llm-credential-in-sandbox-env.md) | The agent's LLM credential is in the sandbox env; the command denylist and the CLI's permission rules do not protect it | accepted (2026-10-08) |
| [0009](0009-git-proxy-scopes-push-to-the-jobs-branch.md) | The git-proxy lets a push touch the job's own branch only: no other ref, no deletion | accepted (2026-10-10) |
| [0010](0010-organizations.md) | The organization is the unit embry0 maintains | accepted (2026-10-16) |
| [0011](0011-organization-needs.md) | Needs are derived from facts, and only a person's answer is kept | accepted (2026-10-16) |
| [0012](0012-organization-webhook.md) | One organization webhook replaces a webhook per repository; a delivery for an unregistered repository under it is recorded as a need, or registers the repository when the organization says so, and is then handled as before | accepted (2026-10-16) |
| [0013](0013-hosts.md) | A host is a first-class record, apart from the repository; a tailnet is one source of hosts | accepted (2026-10-16) |
| [0014](0014-maintain-hints.md) | What a repository takes to maintain is read from its facts by rules, never a model | accepted (2026-10-18) |
