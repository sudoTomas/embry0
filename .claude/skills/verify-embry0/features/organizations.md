# Organizations

A forge owner (a GitHub organization or user, a GitLab group) registered with its credential, what a probe found the credential can do there, and the facts a discovery run read for every repository under it.

## Sub-features

- `org-list` lists registered owners with forge, access and repository count.
- `org-probe` reports what a credential can do under an owner: list, manage webhooks, the count the owner record gives.
- `org-runs` shows discovery runs with repositories seen of total and a summary.
- `org-repos` lists one fact row per repository: archived, language, boot hint, frontend, tests, CI, env keys, webhook, and embry0's own state.
- `org-needs` lists what embry0 still needs from a person to maintain the owner, one need per kind and repository set.
- `org-webhook` reports whether the organization webhook points at this deployment.
- `org-ui` is the Organizations page and the Add organization wizard, on branches that carry them.

## How to get to it (user POV)

- The `Organizations` entry in the sidebar, on branches that carry the page.
- The API: `/api/v1/orgs`, `/api/v1/orgs/<owner>`, `/api/v1/orgs/<owner>/runs`, `/api/v1/orgs/<owner>/repos`, `/api/v1/orgs/<owner>/needs`, `/api/v1/orgs/<owner>/webhook` (all GET).
- The MCP tools `org_list`, `org_get`, `org_needs` (read-only); `org_register`, `org_webhook_install` and `org_act` write, so an operator runs them.

## Driving it with control-embry0

Preconditions:

- `doctor` reports `worth_driving: true`.
- At least one owner is registered: `control-embry0 api /api/v1/orgs` lists one. Registering and discovering write, so an operator does them, never this skill.

- **List.** Run `control-embry0 api /api/v1/orgs`. Each owner carries `access` with `read`, `hooks` and a `detail` line from the probe.
- **Runs.** Run `control-embry0 api /api/v1/orgs/<owner>/runs`. The newest run is `done` with `repos_seen` equal to `repos_total` and a `summary`.
- **Facts.** Run `control-embry0 api /api/v1/orgs/<owner>/repos`. Each row carries `facts` with the keys above.
- **Needs.** Run `control-embry0 api /api/v1/orgs/<owner>/needs`. Each need carries a `kind`, its `repos`, a `status` and, while open, the `case_id` of its healing case.
- **Webhook.** Run `control-embry0 api /api/v1/orgs/<owner>/webhook`. It says whether an organization hook points here (`installed`) and the `target_url`.
- **Page.** On a branch with the page, run `control-embry0 probe --route /orgs` and `control-embry0 shot --route /orgs --width 390 --path artifacts/orgs-390.png`. Rows render, `errors` is empty, `horizontal_scroll` is false.

## Gotchas

- The owner record's repository count can differ from what the listing returns (forks, archived, internal repositories and visibility). Compare the run summary, not the probe's count.
- A webhook counts as pointing at this deployment only when its URL starts with `WEBHOOK_PUBLIC_URL`. A deployment whose webhooks arrive through a tunnel hostname must set `WEBHOOK_PUBLIC_URL`; unset, hooks read as unknown and the needs list asks for it (`hook_origin_unknown`).
