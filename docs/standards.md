# Standards

A standard is what an organization holds every repository under it to. The first kind is `design`: the token roles an organization's interfaces name, their type scale and their component rules, filled with that organization's own colours, fonts and sizes. embry0 holds one versioned standard per forge owner and kind, keeps each repository's mode under it, and audits a repository against it on request. See [ADR 0014](adr/0014-organization-standards.md) for the decision.

This release holds standards, sets each repository's mode, and runs an audit when asked. Nothing reviews pull requests or files findings as tickets yet, and an audit never changes the repository it reads.

## Schema and values

A standard has two halves:

- The **schema** is the shape every standard of the kind shares: which roles exist, the type scale, the rules. Two organizations with different brands can share one schema.
- The **values** are one organization's fill of the schema's roles: which colour is the accent, which font is the body font.

embry0 ships no values. An organization's values are its configuration, written through the API by an admin.

The owner is the forge owner (a GitHub organization or user, or a GitLab group), the same key an [organization](glossary.md#organization) uses, stored lowercase. A standard applies to the repositories under its owner. It may be written before the organization is registered.

## The schema's shape

The schema is an object with up to three keys. Any other key is refused, so a misspelt key cannot be stored and silently ignored.

| key | shape | meaning |
|---|---|---|
| `roles` | object: role name to an object | the token roles. The object describes the role (`type`, `description` or anything else an auditor should read) |
| `type_scale` | object: step name to an object | the type steps, each with its size, weight and line height as the organization writes them |
| `rules` | list of objects, each with `id` and `text` | the component rules in words. `id` is unique; other keys are kept |

A name (a role, a step or a rule id) is lowercase letters, digits, `.`, `_` and `-`, starting with a letter, at most 64 characters.

The values are an object of role name to value. Every key must be a role the schema declares, and no value may be null; a role with no value is allowed. A value may be any JSON: a colour string, a number, a font stack as a list.

A write is refused with a 422 that lists every problem when the schema or the values are not objects, the schema holds another key, a name is malformed, a rule lacks its `id` or `text`, or a value names an undeclared role. The schema and the values are each at most 32,000 characters as JSON, the prose at most 32,000 characters, and `references` at most 50 entries of 500 characters: all of them ride every audit's task.

## An example

The values below are neutral placeholders, not a recommendation.

```json
{
  "schema": {
    "roles": {
      "color.base": {"type": "color", "description": "page background"},
      "color.surface": {"type": "color", "description": "cards, panels, table headers"},
      "color.text": {"type": "color", "description": "body text at full strength"},
      "color.text.muted": {"type": "color", "description": "labels, table headers"},
      "color.accent": {"type": "color", "description": "the one accent: primary actions, focus"},
      "color.status.ok": {"type": "color"},
      "color.status.warn": {"type": "color"},
      "color.status.error": {"type": "color"},
      "font.body": {"type": "font-family"},
      "font.mono": {"type": "font-family"},
      "radius": {"type": "length"},
      "table.row_height": {"type": "length"}
    },
    "type_scale": {
      "body": {"size": "14px", "weight": 400},
      "table": {"size": "14px", "weight": 400},
      "th": {"size": "12px", "weight": 600, "transform": "uppercase", "letter_spacing": "0.06em"},
      "label": {"size": "12px", "weight": 500},
      "tile_value": {"size": "28px", "weight": 600},
      "h1": {"size": "20px", "weight": 700}
    },
    "rules": [
      {"id": "one-accent", "text": "One accent colour. Status reads through the status roles, never a second accent."},
      {"id": "tables-first", "text": "Lists of records are tables, not cards."},
      {"id": "tokens-only", "text": "Colours, fonts and radii come from the roles, never raw values."}
    ]
  },
  "values": {
    "color.base": "#101014",
    "color.surface": "#18181d",
    "color.text": "rgba(255, 255, 255, 0.92)",
    "color.text.muted": "rgba(255, 255, 255, 0.50)",
    "color.accent": "#3366ff",
    "color.status.ok": "#2bb673",
    "color.status.warn": "#e0a800",
    "color.status.error": "#d64545",
    "font.body": ["Inter", "system-ui", "sans-serif"],
    "font.mono": ["ui-monospace", "monospace"],
    "radius": "8px",
    "table.row_height": "36px"
  },
  "prose": "Dense and dark. The table is the primary surface; cards are for summaries only.",
  "references": ["acme/design-system", "https://example.com/brand"]
}
```

```bash
curl -X PUT http://localhost:8200/api/v1/standards/acme/design \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d @standard.json
```

## Versions

`PUT /api/v1/standards/{owner}/{kind}` stores a complete new version: the body is the whole version, and a field it leaves out (`prose`, `references`) is empty in that version, not carried over. Read the active version first and send back what you keep. The new version is `active` and the one before it becomes `superseded`; at most one version per owner and kind is active, and versions are numbered from 1 without gaps. Writers of one owner and kind queue on a Postgres advisory lock (`standards:<owner>:<kind>`), so two writes at once get consecutive versions.

`GET /api/v1/standards/{owner}/{kind}/versions` lists every version, newest first, with its status. `draft` is in the status vocabulary for proposed versions; nothing writes one in this release.

## A repository's mode

Each repository under the owner has a mode for each kind:

| mode | meaning |
|---|---|
| `off` | the default. embry0 does nothing for the repository under this standard: it is never audited |
| `advise` | audits may run; their findings are advice |
| `enforce` | audits may run; reserved for holding pull requests to the standard, which this release does not do |

An admin sets it with `PUT /api/v1/standards/{owner}/{kind}/repos/{repo}`, body `{"mode": "advise"}`. The repository must be registered and sit under the owner (its first path segment): another owner's repository is a 422, an unregistered one a 404. The body may also carry `exceptions`, a list of `{"rule", "reason", "paths"}` that replaces the repository's exceptions; left out, they are kept. An audit is told about every exception and counts nothing an exception covers.

The state is stored per repository, lowercase, in `repo_standard_state`. `GET /api/v1/repos/{repo}` lists it under `standards`, one entry per kind, with mode `off` for a kind never set:

| field | meaning |
|---|---|
| `mode` | `off`, `advise` or `enforce` |
| `exceptions` | what the repository is excused from |
| `last_audit_job_id` | the newest audit started; it may still be running, or may have failed |
| `score` | 0 to 100, from the newest audit that completed; null before one has, or when its report had no score |
| `findings_open` | the rows of that audit's findings table |
| `last_audit_at` | when that audit completed |

## Audits

`POST /api/v1/standards/{owner}/{kind}/audit/{repo}` starts a read-only audit job and answers 202 with its `job_id`. It is refused with 409 while the repository is in mode `off`, while the owner has no active standard of the kind, while the kind's pipeline template is absent (`design-audit` for a design standard, seeded with the built-in agents), and while the repository's previous audit is still pending or running. Requests that start an audit of one repository together (a double click, a retried tool call) get one 202; the others get a 409 naming the job that started, and the jobs they created are cancelled before they run. An unregistered repository is a 404, one under another owner a 422, and a key whose repository allowlist does not name it a 403 (`repo_not_allowed`), as for `POST /jobs`.

The job runs on the template against a clone of the repository, and its task carries the whole active standard: schema, values, prose, references and the repository's exceptions, with the report contract:

1. The report's first line is `Score: N`, a whole number from 0 to 100.
2. A `## Findings` heading follows, with one markdown table: `Severity | Rule | Location | Evidence | Proposed fix`, one row per finding, at most 40 rows. With nothing to report, the heading is followed by `No findings.` and no table.

When the job completes, embry0 reads its report deliverable (the job's result summary when there is none) and writes onto the repository's state:

- `score` is N from the first line that starts with the word `Score` and a number once markdown emphasis is removed, with N from 0 to 100 and any text after it (`Score: 72`, `**Score:** 72/100`, `**Score** - 72/100, commit abc1234`). No such line leaves the score null, and the log says `standard_audit_without_score`.
- `findings_open` is the number of body rows of the findings table: the first table with a `Severity` or `Location` column under a heading that names findings, else the first table under such a heading. A report with no such heading uses its first table with a `Severity` or `Location` column. A row whose every cell is empty, a dash or "none" is not counted.

Only the repository whose newest audit is that job is written, so an older audit that finishes late writes nothing, and a failed audit leaves the score of the last completed one. When an admin switches the repository to mode `off` between the checks and the start, the job is cancelled before it runs and the answer is a 409. When the switch comes while the audit runs, the job runs to its end and its result is not recorded: a repository in mode `off` keeps the score it had.

## In the dashboard

The organization page (`/orgs/<owner>`) has a **Design standard** line: the active version, how many repositories are held to it (mode advise or enforce) and the lowest score, with a link to the Standards view at `/orgs/<owner>/standards`.

The Standards view lists every registered repository under the owner, lowest score first and those without a score last, each with its mode, score, open findings and newest audit job. An admin changes a repository's mode in place. An operator starts an audit; the button is off in mode off, without an active standard and while the repository's previous audit is pending or running, and a 409 shows its detail under the row. The one gold control is the Audit button of the first repository held to the standard that has never been audited and can be audited now; with none, nothing is gold. Below the table are the active standard itself (prose, references, the colour roles as swatches with their values, the other roles, the type scale and the rules) and its versions.

A repository's page shows one line per kind: its mode, the score and findings of its newest completed audit, and a link to its newest audit job.

The dashboard has no editor for the standard's JSON: a new version is written with `PUT /api/v1/standards/{owner}/{kind}`, and the view names that path.

## API

Reads need any authenticated principal and an audit needs an operator; writing a standard and setting a mode need the `admin` tier. Every write records an audit row: `standard.updated` (owner, kind, version and the version it superseded), `standard.repo_mode_set` (repository, mode and the number of exceptions) and `standard.audit_started` (repository, version and job id).

| Endpoint | Tier |
|---|---|
| `GET /api/v1/standards/{owner}` | read: the active standard of each kind, and every repository with a stored mode |
| `GET /api/v1/standards/{owner}/{kind}` | read: the active version, 404 when none |
| `PUT /api/v1/standards/{owner}/{kind}` | admin: a new version |
| `GET /api/v1/standards/{owner}/{kind}/versions` | read |
| `GET /api/v1/standards/{owner}/{kind}/repos/{repo}` | read |
| `PUT /api/v1/standards/{owner}/{kind}/repos/{repo}` | admin: mode and exceptions |
| `POST /api/v1/standards/{owner}/{kind}/audit/{repo}` | operator: starts a job |

## MCP

| tool | effect |
|---|---|
| `standard_get(owner, kind)` | the active version |
| `standard_set(owner, kind, schema, values, prose="", references=None, confirm=False)` | a new version; needs an admin-scope key, and without `confirm=true` it returns `confirmation_required` and changes nothing |
| `standard_audit(owner, kind, repo, confirm=False)` | starts an audit job; without `confirm=true` it returns `confirmation_required` and starts nothing |

A repository's mode is set through the API, not through MCP.
