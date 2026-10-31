# What the background features keep

Three features read repositories on their own schedule, with nobody asking for a job: the leads finders, the scout and the Slop-O-Meter. Each reads a repository only when it is registered, active and enrolled in that feature (`background.<feature>` of the settings document, [which repositories](../configuration.md#which-repositories-background-features-read)). This page lists what each keeps on disk and in the database, which of it is personal data, and when it goes.

## At a glance

| Feature | Off on a new deployment? | On disk | Personal data in the database | Removed when a repository leaves its scope? |
|---|---|---|---|---|
| Slop-O-Meter | yes (`slop_meter: false`) | one bare mirror per repository: its whole history | commit authors' and co-authors' names and emails, forge logins mapped to emails, commenters' and reviewers' logins | **yes**, `background.prune_after_days` (7) after it left, unless it comes back first |
| Scout | yes (`scout: false`, and no model until `background_models.scout` names one) | nothing of its own | none in its own rows; the leads it adds hold code paths and quoted lines | no: see below |
| Leads | by their own switch (`leads.enabled`) | nothing | a review lead names the reviewer and the author of the thread | no: see below |

## The Slop-O-Meter

**Disk.** A bare mirror of each enrolled repository, under `SLOP_METER_DATA_PATH` (`/data/embry0/slop-meter/<owner>/<name>.git`, inside the orchestrator's data volume). A mirror holds every branch of the repository's whole history, so it takes about what `git clone --bare` takes; size the volume for the sum of the enrolled repositories.

**Database** (the `slop_meter_*` tables):

| Table | What it holds | Personal data |
|---|---|---|
| `slop_meter_repos` | where reading stands: the mirror's head, commits read, event cursors, the last error | none |
| `slop_meter_commits` | each commit of the default branch as read: date, message, per-file line counts, co-author trailers | the author's name and email, each co-author's name and email |
| `slop_meter_events` | forge comments, review comments and reviews: kind, time | the login of whoever wrote it |
| `slop_meter_logins` | forge login to commit email, one row per login across every repository | a login and its email |
| `slop_meter_judgments` | the model's verdict on each outlier commit, and why | none of its own |
| `slop_meter_weeks`, `slop_meter_scores` | the weekly series and the current score per repository and area | none |

**When it goes.** A repository leaves the meter's scope when its `background.slop_meter` resolves to false (opted out in its own layer, its organization's, or the deployment's document) or when it is unregistered. Making it inactive only stops the reading: what was read is kept, however long it stays inactive. A mirror directory with no row (its rows were lost) leaves the scope the same way when no enrolled repository has its name, but only if it carries the meter's marker: every mirror the meter clones gets `embry0.slopMeter = true` in its own git config, and a mirror cloned before this release gets it at its next fetch. The marker counts only in a bare mirror's own `config`, a regular file read without following a link; a directory holding a `.git` entry is a working checkout, never the meter's, whatever its config says. A directory with no row and no marker may not be the meter's (a mirror from before the marker whose rows are gone, or anything under a wrong `SLOP_METER_DATA_PATH`): it is logged at error level (`slop_meter_orphan_unmarked`) and audited once, and never deleted. A directory whose name is not all lowercase is never the meter's either (it makes only lowercase paths): it is logged once (`slop_meter_mirror_not_lowercase`) and left alone.

Nothing goes at once. Once the release's import has run (*Upgrading* below):

1. The first tick that finds a repository out of scope records when (`background_scope_exits`) and logs `slop_meter_prune_pending` with the time it will be removed: `background.prune_after_days` later, a week unless the deployment's document says otherwise (1 to 365).
2. If it comes back in scope before then (enrolled again, registered again, a `REPO_RENAMES` entry put right), its record is deleted and it keeps everything (`slop_meter_prune_cancelled`). Leaving again starts a new wait.
3. Once the wait is over, the next tick deletes its mirror, then its rows from every table above in one transaction, and each login no remaining event refers to. It logs `slop_meter_pruned` with the row counts and writes an audit row, `slop_meter.pruned`, by `embry0:slop-meter`.

A tick (daily, every few minutes while a history is being read, and once at each start while the schedule is off) never removes anything when the registry read lists **no** repository at all while the meter keeps some: an empty registry read must not cost every repository's data. It logs `slop_meter_prune_refused` at error level, audits it once (`slop_meter.prune.refused`), and records no departure either. Nothing enrolled is no such case: turning the meter off for every repository, or opting out the only one it read, removes what it kept after the same week, which putting the switch back in time cancels; so does a `REPO_RENAMES` mistake put right in time.

A history rewritten upstream is read again from the start, which replaces its commit rows. Rows kept under a spelling that differs from the registry's only by case (`Acme/Widgets` for `acme/widgets`) move onto the registry's spelling, in one transaction, so the meter neither reads the history again nor keeps two copies of its contributors. When the registry itself holds two rows that differ only by case, every background feature takes the same one: the spelling that sorts first by code point, active when either row is.

**The guard on the disk.** The meter fetches into a mirror, and marks it, only when the owner's directory and the mirror are real directories under the root: a link there stops the sync with an error, and nothing is written through it. The deletion opens each directory on the way to `<owner>/<name>.git` relative to the one above it and never through a symbolic link, starting from the real path of `SLOP_METER_DATA_PATH`, so it can delete only a directory that really sits there and never anything a link points at. A link inside a mirror is removed, not followed. A name too long for any directory is no mirror. When the owner's directory or the mirror is a link, or not a directory, nothing on disk is deleted: `slop_meter_mirror_prune_refused` is logged at error level and audited (`slop_meter.prune.refused`, with the path, never the contents) for a person to look at, and the rows go anyway, so the personal data in them does not wait on the disk. A deletion that fails for another reason (`slop_meter_mirror_prune_failed`, a permission or a busy file) keeps the rows and the record, and the next tick tries again.

**Upgrading.** The first start of the release that added `background` enrolls, in each repository's own layer, what the meter read before ([how](../configuration.md#which-repositories-background-features-read)). Until that import has run, the meter removes nothing: a repository it does not read yet may still be one it should. An import that fails is tried again in the same run, a minute later and then less and less often (`settle_enrollment`), with the moment that start's process began. A registered name no settings layer can be stored under (an owner starting with a dot) holds the import until it is renamed or unregistered. It is logged at error level once a process (`background_enrollment_name_unaddressable`), then at debug level on each retry, and audited once (`background_enrollment.blocked`); the gauge `embry0_background_enrollment_blocked` is 1 meanwhile, and the alert `Embry0BackgroundEnrollmentBlocked` fires after 30 minutes of it ([metrics](metrics.md#settings-imports)).

The import also enrolls in the meter every inactive registered repository it keeps rows for: it stays unread while inactive, and keeps what was read. From then on, every repository the meter keeps anything for that the import did not enroll is pending removal, and goes `prune_after_days` after the meter's first tick. That is a repository that had already dropped out of the old set (no job in the 90 days before the upgrade, no owners, no watched stack), one `SLOP_METER_EXCLUDE` names, one `SLOP_METER_REPOS` names that is not registered (logged at error level at the import, `background_enrollment_name_skipped`: the meter read it before, and no longer does; one that is inactive is logged too, and keeps its rows), and one unregistered. Run this right before the upgrade to list them, with your `SLOP_METER_REPOS` and `SLOP_METER_EXCLUDE` in the two arrays, each name lowercased:

<!-- prune-preview:start -->
```sql
WITH lists AS (
    SELECT ARRAY[]::text[] AS include,  -- SLOP_METER_REPOS
           ARRAY[]::text[] AS exclude   -- SLOP_METER_EXCLUDE
),
shown AS (
    SELECT lower(repo) AS repo FROM jobs
     WHERE repo IS NOT NULL AND created_at > now() - interval '90 days'
    UNION SELECT lower(repo) FROM repo_preferences
     WHERE CASE WHEN jsonb_typeof(owners) = 'array' THEN jsonb_array_length(owners) ELSE 0 END > 0
    UNION SELECT lower(repo) FROM watcher_stacks WHERE enabled
),
kept AS (
    SELECT lower(r.repo) AS repo FROM repos r, lists l
     WHERE (r.active AND (lower(r.repo) IN (SELECT repo FROM shown) OR lower(r.repo) = ANY (l.include))
            OR NOT r.active AND lower(r.repo) IN (SELECT lower(repo) FROM slop_meter_repos))
       AND NOT lower(r.repo) = ANY (l.exclude)
)
SELECT lower(m.repo) AS repo FROM slop_meter_repos m
 WHERE lower(m.repo) NOT IN (SELECT repo FROM kept)
 GROUP BY 1 ORDER BY 1;
```
<!-- prune-preview:end -->

It lists rows only: a mirror directory under `SLOP_METER_DATA_PATH` with no row is pending too when it carries the marker, which no mirror cloned before this release does, so on the day of the upgrade none is. To keep one of them, add it to `SLOP_METER_REPOS` before the upgrade (registered and active), or enroll it within the week after: `PATCH /api/v1/config/settings/background?repo=<owner/name>` with `{"slop_meter": true}`.

## The scout

The scout starts read-only jobs; each job's sandbox, logs and saved sessions follow the jobs' own retention (`AGENT_SESSION_RETENTION_DAYS`, `QA_IMAGE_RETENTION_DAYS`, [ADR 0017](../adr/0017-code-retention.md)), like any job. Its own rows are `scout_runs`: which job scouted which repository, area and template, when, and how many leads it added. The leads it adds (`crack`, `weed`) quote code paths and lines.

None of it is removed when a repository leaves the scout's scope. The runs are bookkeeping, and the leads are offers people answer, with their decisions kept. What changes is that the scout no longer reads the repository: it starts no job on it, and a job of it that finishes after that adds no leads (`scout_harvest_dropped`).

## The leads finders

The finders store leads: a failing check, an alert, a flaky check, an unanswered review thread, a backlog ticket. A review lead names the reviewer and the author of the thread by their forge logins. A lead stays until it is answered, or until a whole read of its repository no longer sees it.

When a repository leaves the leads' scope, its leads stay as they are: they are no longer read, so none is closed by a later read either. Its owners answer what is open as any proposal: accept, decline or snooze ([a lead's life](../leads.md#a-leads-life)).

## Not part of `background`

- **The context audit** keeps its own opt-in per organization (`context_audit_days`, [context-audit.md](../context-audit.md)); its jobs follow the jobs' retention.
- **The log watcher** reads the stacks an operator configures (`watcher_stacks`); what it keeps is described in [self-watching.md](../self-watching.md).
