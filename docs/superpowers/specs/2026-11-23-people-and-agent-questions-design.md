# People and agent questions in the database (#179)

Status: decided (2026-11-23, section 2). Pull request 1 built the `agent_questions` section (section 6); pull
request 2 builds the people tables and their repository (section 3); the rest follow (section 7). Refs #159, #161,
#175, #179. Line numbers are at `67b9580e`.

## 1. Why

There is no single record of who a person is.

- **A person is named three ways, by three features.**
  - Owners are forge logins: `repo_preferences.owners`, the areas document's owners (#175) and `CODEOWNERS`, resolved
    in one place, `owners_for` (`embry0/services/owners.py:45`).
  - A hand-off names an email (`handoffs.to_email`, migration 125).
  - An ask names a recipient as text (`asks.recipient`, migration 126).
  - The only link between a login and an email is `users.github_login` (migration 125), and it exists only for a
    person who signed in to the dashboard.
- **Nothing says how a chat reaches a person.** The decisions spec left it to each deployment.
- **Working hours are an organization's at best.** The `asks` section lets an organization's layer set `hours`,
  `weekdays` and `timezone` (`embry0/settings/sections/asks.py:204`). A person who works other hours, or in
  another time zone, is asked on the organization's clock.
- **The question caps are the deployment's, read at module load.**
  - `ASK_USER_ROUNDS_CAP` and `USER_RETRY_ROUNDS_CAP` were read from the environment when the agent module loaded
    (`embry0/orchestration/nodes/agent.py:54-73`), one value for every job of the deployment.
  - The brainstorming floor was a constant.
  - How much back-and-forth a team accepts differs by organization and by repository, which the settings layers
    (spec 2026-10-29) exist for.

## 2. Decisions

Each was taken with the recommended option.

- **D1. People are rows, linked to users.**
  - New `people` and `person_identities` tables, not an organization document. Identities must be unique across
    every organization and found by login on every webhook and ask, and a person who signs in must be the same
    person as the one asked. A document can enforce none of that.
  - A person may have a `users` row (they sign in) or not (they are only asked).
- **D2. Identities are qualified by their kind.**
  - An identity is a kind and a value: a login on a forge (`github:alex`, `gitlab:alex`) or a handle in a chat
    (`telegram`, `slack`, `teams`, `discord`). A value is one person's on its kind, whatever its case, not across
    kinds.
  - The same text on two forges may be two people, and one person may have identities of several kinds, or several
    of one kind.
- **D3. A person's hours override their organization's, field by field.**
  - `timezone`, `hours` and `weekdays` set on the person win.
  - A field they leave unset reads the `asks` values of the decision's organization, then the deployment's.
- **D4. Chat handles are stored, and served only to unrestricted keys.**
  - A person may have chat handles: identities of a chat's kind, the chat's id for them (decided for pull request 2,
    in place of one `chat_channel` and `chat_id` on the person).
  - `GET /asks/due` gives each ask its person's handle only to a key with no repository allowlist. An allowlist is a
    read boundary, so a key limited to some repositories reads nothing about people beyond their names.
- **D5. `agent_questions` is a section with organization and repository layers.**
  - `rounds_cap`, `brainstorming_floor` and `retry_rounds_cap`, which every layer may set.
  - A job reads the section once, when it starts, and keeps it.
  - The two variables are imported once and then ignored.
- **D6. Explicit people only, after one backfill.**
  - embry0 creates a person only when someone adds one, with one exception: when the tables are created, each user
    who has signed in becomes one person, linked to them, with their GitHub login as a `github` identity (decided
    for pull request 2). A user who signs in after that is not made a person by itself.
  - A login it meets with no person behind it (an owner, a decision's asker) is listed as unknown, with an "add"
    action, never guessed into a person.
- **D7. Owners become people in one place.**
  - `owners_for` stays the one resolver. Its logins are looked up in `person_identities`, so the feed, decisions
    and asks receive people (or unknown logins), and no consumer reads identities itself.
  - The areas document keeps logins (spec 2026-11-10, section 9).

## 3. People

```
people
  id            TEXT PRIMARY KEY             -- per-<12 hex>
  name          TEXT NOT NULL                -- as the dashboard and a chat say it
  email         TEXT                         -- unique, case-insensitive, when set
  user_id       TEXT                         -- users.id of the person's sign-in, unique when set
  timezone      TEXT                         -- IANA name; unset reads the organization's
  hours         TEXT                         -- "HH-HH", the asks section's grammar; unset reads the organization's
  weekdays      TEXT                         -- "mon-fri", the asks section's grammar; unset reads the organization's
  disabled      BOOLEAN                      -- not asked and not an owner, kept for the record
  created_by, created_at, updated_at

person_identities
  person_id     TEXT NOT NULL                -- people.id, deleted with the person
  kind          TEXT NOT NULL                -- 'github' | 'gitlab' | 'telegram' | 'slack' | 'teams' | 'discord'
  value         TEXT NOT NULL                -- a login as the forge spells it, or the chat's id for the person
  created_at
  UNIQUE (kind, LOWER(value))
```

- **Deployment-wide.** People are deployment-wide, as `users` is: a person may own repositories of several
  organizations, and is one person in each.
- **Values.** A forge login follows its forge's grammar (`embry0/forge_logins.py`), stored without an `@`; a chat id
  is one word of plain text. Every check names its field and never repeats the value (`embry0/services/people.py`).
  A lookup or removal of a value no identity of its kind can be finds nobody.
- **One GitHub login rule.** `forge_logins.GITHUB_LOGIN_RE`, GitHub's own (letters, digits and single hyphens, not
  at either end, at most 39), checks a repository's and an area's owners, a person's `github` identity and, from
  this pull request, a user's login (`PUT /me/github-login`, `PUT /users/{id}/github-login`), which took a trailing
  hyphen or two hyphens running before. A login GitHub would not take matches no owner, so nothing it matched is
  lost; the route answers 422 for it.
- **The backfill stores only what the repository reads back.**
  - A name is cleaned of every character the name check refuses (control, format and private-use characters, a
    line or paragraph separator), trimmed of blanks and cut at 200; an empty one is the email.
  - A login that is not a GitHub login becomes no identity: it stays on the user, matching what it matched before,
    is listed in the person's `person.created` row (`left_off`, with why) and counted in a migration warning.
  - An email or a login that only blanks or case set apart from another user's (the old indexes compare them
    untrimmed) goes to the user whose stored value is trimmed already, then the enabled one, then the oldest; the
    others are left off, the logins listed as above.
- **Hours.** A person's hours are read where the asks loop reads an organization's today. Unset fields fall back
  field by field (D3), and the grammar and validation are the asks section's, so a value moves between the two
  unchanged.
- **Sign-in link.** `user_id` links a person to their sign-in. The backfill (D6) links each user who had signed in;
  after it, a user with a `github_login` and no person is offered as an "add", never linked by itself.
- **The GitHub login, for one release.** `users.github_login` stays what owners are matched by until section 4
  lands. While a person is linked to a user, the user's login is one of the person's `github` identities: the one it
  names while it still is one, else the oldest, else none. A change of the person's GitHub identities or of their
  link writes it, in the same transaction. Setting a user's login (`PUT /me/github-login`,
  `PUT /users/{id}/github-login`) moves the linked person's identity: a login the person holds already leaves their
  identities as they are, any other takes the place of the one the user had, and a cleared login falls to the
  person's oldest GitHub identity left. Unlinking or deleting a person leaves the user's login, so linking the
  person to another user is refused while the first still holds it. The release after section 4 drops the column
  and this sync.
  - **Retired (2026-11-26, migration 162, pull request 8).** The column, this sync, the login routes and the
    stand-in below are gone. Each login no person held was written to a `user.github_login_retired` audit row before
    the column went, for an admin to add on the People page.
- **Audit.** Every write of a person or an identity is audited in its own transaction, and appended to the audit
  file once it commits, as every audited write is; who and what changed:
  `person.created`, `person.updated` (each field before and after), `person.deleted`, `person.identity.added` and
  `person.identity.removed`. A write whose audit row cannot be stored does not happen. The backfill audits each
  person it makes as `embry0:migration`.

## 4. Who a login is

`owners_for(area, repository)` keeps its shape and its order of sources:
1. the area's owners;
2. then the repository's owners;
3. then `CODEOWNERS`.

It now returns, for each login, the person its identity names, or the login marked unknown. Its consumers:

- **The decisions feed** shows a person by name and an unknown login as itself, with "add".
- **Asks** go to people (decided for pull request 4).
  - An owner is a login on the forge of its repository's organization (else the repository's own, else GitHub):
    `<forge>:<login>` is looked up among the identities.
  - A person is asked by their email, else as `<forge>:<login>`. `asks.recipient` keeps that text, so the asks
    queued before go on under the keys they had.
  - An unknown login is still asked, as `<forge>:<login>`, as before: `GET /asks/due` marks it `unknown: true`,
    and the People page lists it with "add" (pull request 6). A disabled person is not asked and is no owner.
  - For one release, a user who signed in with a GitHub login and has no person yet counts as a person holding
    that login, read and never stored, so a user who signed in after the backfill keeps being asked and deciding.
    Retired with the column (pull request 8): a user with no person owns nothing until an admin adds them.
- **Hand-offs** name a person by email. An email with no person is listed as unknown with "add", as a login is.
- **Who may decide.** `_may_decide` resolves the email the assistant verified to a person, signed in or not, and
  compares that person's identities with the decision's owners on its forge, instead of `users.github_login`
  (decided for pull request 4). A caller owns a decision only as the person the directory names for one of its
  owners, so a login left on an unlinked user owns nothing the person holds. Acting as admin still needs a
  signed-in admin; a dashboard session answers as itself; a person with no email answers nothing from a chat. A
  disabled user or person decides nothing, and a person whose sign-in is disabled is disabled with it.
  `GET /decisions?for=me` matches the viewer's person the same way.

## 5. Asks

- **Working hours.** An ask is held to its person's working hours (D3): a reminder is sent in the person's working
  time, by the person's time zone, else the organization's, else the deployment's. `remind_hours`, `expire_days`
  and `max_open_per_person` stay the organization's.
- **Chat handles.**
  - `GET /asks/due` adds `chat: [{channel, id}]` and the `timezone` the ask is timed by to each recipient, for a
    caller with no repository allowlist (D4; decided for pull request 4 in place of a `person` object).
  - A restricted key reads the recipient as before, by key and name, with neither.
  - An ask whose person has no handle is still handed out. The assistant decides where it goes, as it does today.

## 6. `agent_questions` (pull request 1)

```yaml
agent_questions:
  rounds_cap: 10           # rounds of asking a person per job; 0: the first question fails the job
  brainstorming_floor: 15  # a brainstorming developer's cap, when higher than rounds_cap
  retry_rounds_cap: 5      # times a person may continue retrying a job
```

- **What counts as a round.**
  - Each run of triage, the developer or the reviewer that stops with a question a person must answer is one
    round, counted for the whole job. The question past `rounds_cap` fails the job with `ERR_MAX_AGENT_QUESTIONS`.
  - Questions an agent answers itself count toward nothing.
  - Triage's own "more information needed" pauses are counted apart, against the same cap
    (`ERR_MAX_TRIAGE_QUESTIONS`).
  - A developer run that loads the brainstorming skill is capped at the higher of `rounds_cap` and
    `brainstorming_floor`.
- **Layers.** Every key is in `org_keys` and `repo_keys`: a repository's layer wins over its organization's, which
  wins over the deployment's.
- **Read once per job.**
  - `embry0.services.job_settings` reads the section with the other job sections when the job starts and keeps it
    on the job's checkpointed state.
  - Every node reads that copy (`ask_rounds_cap`, `retry_rounds_cap` in `embry0/orchestration/nodes/agent.py`), so
    a change reaches the next job, never a running one, not even after a pause and a resume.
  - A job that started before this release kept no copy and reads the section's defaults, the values the constants
    had.
- **Messages name the setting.** Each failure at a cap names the key that capped it:
  `agent_questions.rounds_cap`, `brainstorming_floor` or `retry_rounds_cap`.
- **Continues past the budget ceiling stay the deployment's.**
  - While `budget.continue_may_exceed_ceiling` is true, a person's continue may take a job past
    `budget.max_budget_per_job_usd`.
  - Before this section, the only bound on how many such continues one job could get was `USER_RETRY_ROUNDS_CAP`,
    which was the deployment's.
  - With `retry_rounds_cap` on the organization and repository layers, a layer could have raised that bound. The
    budget section gives those layers no fleet limit on purpose.
  - So a new deployment-only key, `budget.continue_past_ceiling_max` (default 5, the bound the variable gave),
    counts the continues that took the job past the ceiling. Once that many have, the ceiling bounds the job's
    next continues as `continue_may_exceed_ceiling: false` bounds every one, whatever `retry_rounds_cap` says: a
    continue adds at most what is left under the ceiling, whatever amount the person names, and is offered while
    there is room under it.
  - Continues that keep the job under the ceiling count against `retry_rounds_cap` alone.
  - A job reads the key with its other kept settings when it starts (the budget section joins the job sections;
    its other keys are still read where they are checked), and counts on its state (`continues_past_ceiling`).
- **The variables.**
  - `ASK_USER_ROUNDS_CAP` and `USER_RETRY_ROUNDS_CAP` are import-only `Embry0Config` fields, typed as text so no
    value can stop a start.
  - The first start of the release imports a value an operator set, once (`EnvImport` with a whole-number
    converter). A value that is not a whole number at or above 0 is not imported, and the key keeps its default.
    The old reader fell back to its default for a value that was no number but kept a negative one, which failed a
    job at its first question; neither is a cap an operator meant.
  - After that, `embry0 config` names each still set as governed and ignored. The orchestrator reads neither from
    the environment, and `config_lint` no longer excepts `ASK_USER_ROUNDS_CAP`.
- **Retiring the import.** A later release removes the import and the two fields. #168's import retirement follows
  this section's first deploy.

## 7. Pull requests

1. **`feat(settings): the agent_questions section, read once per job`**: section 6, with this spec.
2. **`feat(people): people and their identities`**: the two tables, the backfill and a repository, with the
   GitHub login kept in step (section 3). No consumer changes, and no API.
3. **`feat(people): the people API`** (decided 2026-11-25):
   - `GET/POST/PATCH/DELETE /people` and identity endpoints. Operators and admins read, admins write, and a key with
     a repository allowlist reads nobody and writes nothing. A write with problems is one 422 naming each by where it
     is, never its value; `PATCH` takes `If-Match` with the person's `updated_at`.
   - The hand-off list (who a decision can be sent to) moves to `GET /handoffs/people`, the same shape; the dialog
     and the docs move with it, and `GET /people` is the people API's.
   - The "add" suggestions (`GET /people/suggestions`): the users with no person, and the owner logins embry0 keeps
     (repository preferences, areas) that no person holds. `CODEOWNERS` owners are read from the forge and are not
     listed.
   - `embry0 people check|diff|apply|export`: `apply` adds an entry without an `id` and changes one with it, deleting
     nobody; each change is guarded by the `updated_at` it was exported with (409). MCP: `people_get`, and
     `people_set` (admin, confirm=true).
   - The login routes make no person for a user without one (D6): they set the login alone, and the user is a
     suggestion. For a person, the answer says when a cleared login falls back to their oldest GitHub identity.
   - A GitHub login is one person's: an identity may not be a login another user signs in as, and a user's login may
     not be another person's identity.
4. **`feat(owners): owners resolve to people`**: sections 4 and 5 together (decided 2026-11-25): owners resolve
   to people on their repository's forge, asks go to people in their own hours with their chat handles, and who
   may decide, from a chat or as "waiting for me", is matched by identity. Unknown logins are marked in
   `GET /asks/due`; their "add" listing comes with the People page.
5. *(folded into pull request 4)*
6. **`feat(frontend): the People page`**:
   - add and edit a person;
   - link a sign-in;
   - add identities;
   - work through the unknown logins.
7. **`chore(settings): retire the agent_questions import`**: one release after pull request 1 first deploys. Done
   by #682, which stopped every such import at once.
8. **`feat(people): retire users.github_login`** (decided 2026-11-26): the column, its sync, the stand-in, the
   login routes (`PUT /me/github-login`, `PUT /users/{id}/github-login`) and `github_login` in every answer, in
   one release once pull request 4 runs. A hand-off may go to anyone who signs in or is a person with an email.

## 8. Acceptance

| Check | How |
|---|---|
| A layer cannot raise how many continues pass the budget ceiling | A repository layer sets `retry_rounds_cap: 100000`; through `max_retries_node`, once `continue_past_ceiling_max` continues have passed the ceiling, a job at it is not offered one; through the API, a used-up job with room under the ceiling is offered a continue, and a named amount past it is capped at the ceiling; a continue under the ceiling is not counted; an organization or repository layer that sets `budget.continue_past_ceiling_max` is refused. |
| An organization's layer and a repository's each set a job's caps | Start jobs for repositories with and without the layers; the first round count at which `_enforce_ask_user_cap` fails each job, the brainstorming developer's cap, triage's own cap and the continue-retrying cap follow the nearest layer (`tests/unit/services/test_agent_questions_settings.py`). |
| A running job keeps what it started with | Start a job, pause it on a question, change the layer, resume it: it keeps its cap; the next job reads the new one. |
| The import writes a set value once | `ASK_USER_ROUNDS_CAP=12` and `USER_RETRY_ROUNDS_CAP=3` are written with source `bootstrap`; a second start imports nothing; `ten`, `-1`, an empty value and the default are not imported (`tests/unit/settings/test_agent_questions_import.py`). |
| The environment is not read any more | In a fresh process with `ASK_USER_ROUNDS_CAP=2` set before the agent module loads, a job with no layer is capped at 10. |
| `embry0 config` names the variables as governed | `governed_lines` lists both with their keys. |
| Every user is one person | Migration 154 makes one person per user, linked, with their name (else email), email, `disabled` and GitHub login as a `github` identity, each audited; a second run makes nobody; an email or login only blanks set apart from another's is left off (`tests/unit/storage/test_migration_154.py`). |
| An identity is one person's, any case | A second person given `github:ADA-L` after `github:ada-l`, or `ADA@example.com` after `ada@example.com`, is refused naming the field; a person is found by either case (`tests/unit/storage/test_people_repo.py`). |
| Every write is audited with it | Each write leaves one audit row naming who and what changed; a write whose audit row fails is not stored. |
| The GitHub login stays in step | A linked person's GitHub identities set the user's login, and setting the user's login moves the person's identity; a login another person holds is refused and changes nothing. Until pull request 8 retired it. |
| People (pull requests 3 to 6) | Each pull request states its own, against sections 3 to 5. |

## 9. To decide in their pull requests

- Who may write people: admin alone (decided for pull request 3). An owner may not add themselves: admins only
  (decided 2026-11-26), so the self-service login route went with pull request 8.
- Whether an identity needs proof (a sign-in through that forge) before asks reach its person: no (decided
  2026-11-26). The admin who adds it vouches for it.
- Whether a person's handle may name more than one chat: yes, decided for pull request 2 (D4).

## 10. Out of scope

- Groups as owners (spec 2026-11-10, section 9, point 3).
- Any change to who may answer an ask or decide a decision beyond the identity lookup (section 4).
- Per-person `remind_hours`, `expire_days` or `max_open_per_person`.
