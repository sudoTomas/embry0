# Jobs

The jobs list shows every job with repository, status, cost and a short task line; a job's page shows its timeline, logs and deliverables.

## Sub-features

- `jobs-list` lists jobs with paging and status filters.
- `jobs-brief` loads the list with `brief=true`, so task text is trimmed and `task_truncated` marks the trimmed ones.
- `jobs-detail` opens a job's page with timeline, logs and deliverables.

## How to get to it (user POV)

- The `Jobs` entry in the sidebar.
- A job link from an issue, the Console or the Overview.

## Driving it with control-embry0

Preconditions:

- `doctor` reports `worth_driving: true`.
- At least one job exists: `control-embry0 api /api/v1/jobs?limit=1` returns one.

- **List.** Run `control-embry0 probe --route /jobs`. `table_rows` is above 0 and `errors` is empty.
- **Brief payload.** Run `control-embry0 api "/api/v1/jobs?brief=true&limit=5"`. Each job's `task` is short and long ones carry `task_truncated: true`.
- **Page weight.** In the same probe, `text_chars` stays in the low thousands for a page of jobs; a value near a megabyte means the list loaded full task text.
- **Detail.** Take a `job_id` from the API and run `control-embry0 probe --route /jobs/<job_id>`. The page renders with no errors.
- **Phone.** Run `control-embry0 shot --route /jobs --width 390 --path artifacts/jobs-390.png`. The table scrolls inside its container and `horizontal_scroll` is false.

## Gotchas

- `brief` is opt-in on the API; a client that omits it still gets full task text, by design.
- A running job streams logs; the detail page keeps a connection open, so `networkidle` may not settle. The probe waits for content instead.
