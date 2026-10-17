# embry0 verification map

This directory is the maintained source for verifying what an operator sees in the embry0 dashboard and its API. Read this index before driving, then use the matching feature file as the recipe.

## Baseline preconditions

- A running embry0 stack on the host, with its frontend network present.
- The checkout under review launched with `control-embry0 launch --checkout <dir>` and `control-embry0 doctor` reporting `worth_driving: true` and `served.head` equal to the head under review.
- `EMBRY0_ENV_FILE` (or `EMBRY0_API_KEY`) set so the driver has the deployment's key.
- Never drive a dashboard this run did not launch, except the deployment's own URL after a deploy, named with `EMBRY0_VERIFY_URL`.

## Driving conventions

- Every recipe is read-only. No control that writes is pressed, and `api` sends GET only.
- Prefer stable handles: route paths, `data-testid` attributes, ARIA labels such as `Open menu` and `Close sidebar`, and `nav[aria-label*="readcrumb"]`.
- Drive each touched route at 1440 and at 390 wide.
- Treat every command as literal.

## Proof and skip reporting

- Keep the `doctor` JSON, the `probe` JSON of each route and a screenshot of each in `artifacts/`.
- An API change is proved by the `api` response carrying the new field, next to the page that renders it.
- `probe.errors` must be empty on the touched routes.
- Report an entry point that could not be reached with the command tried and the unmet precondition. Do not report it as verified through another path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the behavior. It then has exactly four H2 sections in this order: `Sub-features`, `How to get to it (user POV)`, `Driving it with control-embry0`, `Gotchas`.

## Features

- [Navigation](./navigation.md) covers the sidebar at desktop and phone width, the drawer and the breadcrumbs.
- [Overview](./overview.md) covers the first screen: tiles, recent issues and the watcher panel.
- [Jobs](./jobs.md) covers the jobs list, its brief task text and a job's detail page.
- [Organizations](./organizations.md) covers registered forge owners, discovery runs and repository facts.
- [Providers and agents](./providers-agents.md) covers the LLM providers page and the agent definitions that use them.
