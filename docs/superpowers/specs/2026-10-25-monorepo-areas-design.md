# Areas of a monorepo: say which part of a repository the work is about

**Goal.** embry0 names work by repository. In a monorepo that is too coarse: a watcher case, an audit finding and a pull request all read "app", though one is about billing and the next about search, and the people who answer for billing are not the people who answer for search. Owner proposals (spec 2026-10-24) then ask the repository's owners about everything in it. This spec lets a repository name its **areas**, and makes everything embry0 says about the work carry the area: the proposals, who they ask, the pull requests embry0 opens, and the jobs.

**Non-goals.** No per-area settings (budgets, pipelines, models, QA); no per-area tracker or merge policy; nothing for GitLab merge requests in this version (they open as before).

## Where areas are defined

In the repository, `.embry0/areas.yml` on the default branch, next to the code it describes, so it changes in the same review as the code. A list of `{name, paths, logs, owners}`; see [docs/areas.md](../../areas.md) for the keys. Considered and not chosen: a dashboard setting per repository (it drifts from the code, and a monorepo's layout changes in pull requests, not in a settings page); CODEOWNERS sections (they name owners but not a display name, and say nothing about logs).

The file is read with the repository's organization credential through the contents API and kept an hour per repository, in one process-wide reader the proposals feed and the executor share. A file that is missing, unreadable or not YAML names no areas; an entry without a name is skipped; owners that are not GitHub logins are dropped.

## Which area a thing is in

| Thing | Its area |
|---|---|
| a file | the first area one of whose `paths` globs matches it |
| a change (a branch, a pull request) | the area most of its changed files are in; none when none is |
| a watcher case | the first area one of whose `logs` strings occurs in its log signature or title (case ignored) |
| an issue | the first area one of its labels names, by name, slug or `area:<slug>` |
| a context finding | the area of its file |

Globs: `**` any number of directories, `*` and `?` within one, a trailing `/` all below.

## What carries it

- **Proposals.** Each proposal has `area` (null without one). Its `owners` are the area's when it names some, else the repository's (preference, then CODEOWNERS). The Owner proposals page shows the area by the repository; a chat assistant names it from the field.
- **Pull requests embry0 opens** (the orchestrator's PR fallback, which opens nearly all of them since the sandbox cannot): before opening, embry0 reads the compare of the branch against its base (GitHub lists up to 300 files, enough to say where most of a change is). With an area, the PR gets the label `area:<slug>` and the title `[Name] <title>`, unless the title's conventional scope already names the area (`fix(billing): …`). Best-effort: any failure opens the PR as before.
- **Jobs.** `jobs.area` (migration 109) records the area of the PR the job opened; `JobResponse.area` carries it; the Jobs list shows it before the title, the job page next to the repository.

## Tests

Parser (keys, bad files, owner filtering), globs, majority, logs and labels matching, titles, the cached reader, the compare lookup; proposals stamped with areas and area owners; the PR fallback's title, label and job record, and a repository without areas opening as before; the badges on the Jobs list and the Owner proposals page.
