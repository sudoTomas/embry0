# Areas of a monorepo

A monorepo holds several products, and the people who answer for one are not the people who answer for the next. embry0 keeps each repository's parts, its **areas**, as a document of its own, and names the part in everything it says about the work there: the proposals it asks owners to answer, the pull requests it opens, and the jobs. The repository carries no file for them. A repository without areas works as before. The design is in [the spec](superpowers/specs/2026-10-25-monorepo-areas-design.md); keeping them in embry0 is [this spec](superpowers/specs/2026-11-10-areas-in-the-database-design.md) and [ADR 0030](adr/0030-areas-in-embry0.md).

## The document

```yaml
# Areas of acme/widgets. The first area whose paths match a file has it.
version: 1
areas:
  - name: Billing
    paths:
      - "services/billing/**"
      - "services/billing-app/**"
      - "web/apps/billing/"
    logs: ["billing-api"]
    owners: ["octo"]
  - name: Search
    paths: ["services/search/**", "web/apps/search/"]
    logs: ["search-api"]
```

| Key | Means |
|---|---|
| `version` | Optional; `1`. A document of a newer version is refused. |
| `areas` | A list of at most 200 areas; empty or left out, the repository names none. |
| `name` | What embry0 calls the area: `[Billing]` in a pull request title, the area on a job or a proposal. Required, 1 to 100 characters on one line, with a letter or a digit in any script. Its slug (`billing`; `Платежи` is `платежи`) names the label `area:billing`, so two areas cannot share one. Names are compared NFC-normalised and case-folded: an accent typed as one character or as a combining one is the same letter. |
| `paths` | At most 200 globs over the repository's files, from its root, in [qa.yaml's grammar](qa-yaml-reference.md): `**` any number of directories as a part of its own, `*` and `?` within one name, `[abc]` one of the characters, and a trailing `/` everything below (stored as `/**`). A `[` in a name is written `[[]`: a route directory `app/[id]/` is `app/[[]id]/`. A glob has no space at its start or end, and none starts with `!`: there is no negation, so list only the paths an area has. The first area whose glob matches a file has it. A glob is matched in time bounded by its length and the path's, whatever `**` and `*` it holds. |
| `logs` | At most 50 strings that, found in a watcher case's log signature or title, put the case in the area. Case does not matter; the name of the container or service the area runs as is the usual choice. |
| `owners` | At most 20 logins (letters, digits and single hyphens; a leading `@` is fine), asked first about the area's work. Leave it out and the repository's owners are asked. An area's owners may answer its decisions in a chat, as a repository's owners may (see [who decides](api.md#decisions)). |

A write is checked whole, and a 422 names every problem by its key (`areas.0.paths.1: '**' stands alone between slashes...`), never its value. The text is kept as written, comments included, up to 256 KiB; every reader reads its checked values, so a change applies at the next read. Every change is recorded in the repository's History and can be undone.

## Changing them

- **The repository page's Areas section**: the areas as a table, an editor of the document with a preview of what a change does, and the map waiting for a person.
- **The API** (operate, and a key whose repository allowlist covers the repository):
  - `GET /api/v1/repos/{repo}/-/areas` (read): `{repo, yaml_text, areas: [{name, slug, paths, logs, owners}], generation, updated_by, updated_at, imported, latest_job, proposal}` with `ETag: "<generation>"`; `?format=yaml` answers the text alone. A repository with no areas is at generation 0. A generation changes with every write and is never used twice, for any repository: not after an undo, and not after a repository is removed and registered again, so an `ETag` read before a change never matches what is stored after it. `imported` says when the repository's old file was imported (below), `proposal` is the newest map.
  - `PUT /api/v1/repos/{repo}/-/areas`: replace the document, YAML or a JSON object (stored as the YAML it dumps to). `If-Match` (an `ETag` a `GET` or a `PUT` answered) refuses with 409 a document someone changed since; `"0"` creates one. Without `If-Match`, the one 409 is a first write that met another storing the repository's first areas at the same moment. `?dry_run=1` checks and compares, storing nothing; `?reason=` is recorded. Answers `{generation, revision_id, changes}` with `ETag: "<generation>"`, `changes` naming each area added, removed or changed, the owners each adds or removes first. An empty document is a document that names no areas.
  - `GET /api/v1/repos/{repo}/-/history` and `POST .../history/{id}/undo`: the areas' changes beside the test setup's and the preferences', and the undo of the newest one, while it is still what is stored. Undoing the change that created the areas stores the empty document. A repository removed from the registry has no areas to put back: its changes are listed, none undoable, and an undo answers 404.
  - The `PUT`, the apply and the undo answer a document with problems alike: 422 with `detail`, a list of `{loc, type, msg}`, one for each problem.
- **MCP**: `areas_get`, `areas_set`, `areas_undo`, `areas_map`, `areas_proposal_get`, `areas_proposal_decide`.
- **The CLI**, for areas kept in a file of the operator's own: `embry0 areas check FILE` checks it offline with the API's own checks, every problem by key; `embry0 areas diff REPO FILE` shows what applying it would change; `embry0 areas apply REPO FILE` shows the diff, asks, and stores it against the version it read (source `file`, `--yes` without a terminal, `--reason` for the history); `embry0 areas export REPO [-o FILE]` writes the stored text as written. The exit codes are `embry0 settings`': 0 done, 1 refused, 2 usage, 3 conflict, 4 orchestrator unavailable.

## Where the area shows

- **Owner proposals.** A context finding is in the area of its file, a watcher case in the area its log signature or title names, an issue in the area one of its labels names (`Billing`, `billing` or `area:billing`), a lead in the area its files, labels or title name. The area's owners are asked; an area without owners asks the repository's ([owner proposals](api.md#owner-proposals)). The Owner proposals page shows the area next to the repository, and the feed's `area` field lets a chat assistant name it in its message.
- **Pull requests embry0 opens.** On GitHub, embry0 reads the files the branch changes from its base: the area most of them fall in labels the pull request `area:<slug>` and starts its title with `[Name]`, unless a conventional scope already names it (`fix(billing): ...`). A change that falls in no area opens as before.
- **Jobs.** The job records the area of the pull request it opened (`area` on `GET /jobs` and `GET /jobs/{id}`), shown before the job's title on the Jobs list and next to the repository on the job page.
- **The scout and the Slop-O-Meter** scout and score each area on its own.

## Map areas

**Map areas** in the repository's Areas section starts the `area-mapper` agent: it reads the repository (its tree, workspace manifests, docs, CODEOWNERS, compose and deploy configuration, and the LogQL the watcher reads its logs with) and reports an areas document, starting from the one embry0 keeps. The document **waits for a person**: nothing changes until someone applies it, and nothing is written to the repository. It runs with the model and key chosen beside the button ([run with](superpowers/specs/2026-10-25-run-with-design.md)); only one map of a repository runs at a time, and a new one retires a map still waiting.

- `POST /api/v1/repos/{repo}/-/areas/map` (operate, `{"run_with": ...}` optional, 202): starts the map, `{job_id, repo, proposal_id}`; 409 while one runs, 429 while the budget gate refuses. The job is unattended, so its cap is a hard stop: the settings document's `budget.area_map_job_usd` (2 USD by default; an organization may set its own), read when the map starts ([configuration.md](configuration.md#settings-reference)).
- `GET /api/v1/repos/{repo}/-/areas/proposal` (read): the newest map, `{id, status, job_id, stale, proposed_text, changes, text_diff, notes_md, failure, ...}`. `status` is `running`, `pending` (waiting for a person), `applied`, `rejected`, `stale` (the areas changed after it started, or a newer map retired it), `unchanged` (it found the areas embry0 keeps) or `failed` (its job stopped, its report carried no document, or the document has problems, each named in `failure`).
- `POST .../areas/proposal/{id}/apply`: stores the map's document as the areas, only while they are still the ones the map started from (else 409, and the map is `stale`). It is a change in History, so an undo puts the earlier areas back.
- `POST .../areas/proposal/{id}/reject`: turns the map down; the areas stay as they are.
- `GET /api/v1/areas/proposals` (read): every map waiting for a person, for the dashboard's brief bar. A repository removed from the registry takes its maps with it.
- **Run again** on a map's job starts a new map, the same as Map.

A map that cannot start (no `map-areas` template, the executor down) changes nothing: the map waiting before it stays waiting, and a job created for it that could not be submitted is marked failed, so it holds nothing. A map's job finishes only its own repository's map: the proposal names the job, and a job request cannot set `pipeline_config.areas_proposal_id` (422), nor the other keys embry0 writes for work it starts, `proposal_id` and `run_with`.

## The old file

Before this release a repository named its areas in `.embry0/areas.yml` on its default branch. The first start of this release reads that file once, in the background, for each repository registered when it upgraded, and stores what embry0 already acted on: the file as written when the document accepts it, else what the old reader read, under a header saying what was left out and why. The old reader matched a `[` as itself, so a path holding one is written `[[]` and keeps naming the same directory. A repository whose areas someone wrote first keeps them, and when two orchestrators read a repository at once, the one that stored its areas is the one recorded. A read that fails is tried again an hour later, and at the next start, up to 24 reads; GitHub answers a private repository the credential cannot see as it answers a file that is not there, so a missing file is taken as missing only when the repository itself reads. After that embry0 never reads the file again; a repository registered later has none read. embry0 never deletes the file: keep it until the next minor release, which removes the import (a rollback to the release before reads it again), then delete it whenever you like. A deployment that skips this release imports nothing: write its areas, or map them again.
