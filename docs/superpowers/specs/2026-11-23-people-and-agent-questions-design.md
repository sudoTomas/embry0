# People and agent questions in the database (#179)

Status: decided (2026-11-23, section 2). Pull request 1 builds the `agent_questions` section (section 6); the people
tables follow (section 7). Refs #159, #161, #175, #179. Line numbers are at `67b9580e`.

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
- **D2. Logins are forge-qualified.**
  - An identity is a forge and a login, `github:alex` or `gitlab:alex`: a login is unique on its forge, not across
    forges.
  - The same text on two forges may be two people, and one person may have a login on each.
- **D3. A person's hours override their organization's, field by field.**
  - `timezone`, `hours` and `weekdays` set on the person win.
  - A field they leave unset reads the `asks` values of the decision's organization, then the deployment's.
- **D4. Chat handles are stored, and served only to unrestricted keys.**
  - A person may have a chat handle: a channel and an id in it.
  - `GET /asks/due` gives each ask its person's handle only to a key with no repository allowlist. An allowlist is a
    read boundary, so a key limited to some repositories reads nothing about people beyond their names.
- **D5. `agent_questions` is a section with organization and repository layers.**
  - `rounds_cap`, `brainstorming_floor` and `retry_rounds_cap`, which every layer may set.
  - A job reads the section once, when it starts, and keeps it.
  - The two variables are imported once and then ignored.
- **D6. Explicit people only.**
  - embry0 creates a person only when someone adds one.
  - A login it meets with no person behind it (an owner, a decision's asker) is listed as unknown, with an "add"
    action, never guessed into a person.
- **D7. Owners become people in one place.**
  - `owners_for` stays the one resolver. Its logins are looked up in `person_identities`, so the feed, decisions
    and asks receive people (or unknown logins), and no consumer reads identities itself.
  - The areas document keeps logins (spec 2026-11-10, section 9).

## 3. People

```
people
  id            TEXT PRIMARY KEY
  name          TEXT NOT NULL                -- as the dashboard and a chat say it
  email         TEXT                         -- unique, case-insensitive, when set
  user_id       TEXT                         -- users.id of the person's sign-in, unique when set
  timezone      TEXT                         -- IANA name; unset reads the organization's
  hours         TEXT                         -- "HH-HH", the asks section's grammar; unset reads the organization's
  weekdays      TEXT                         -- "mon-fri", the asks section's grammar; unset reads the organization's
  chat_channel  TEXT                         -- with chat_id, or neither
  chat_id       TEXT
  created_by, created_at, updated_at

person_identities
  person_id     TEXT NOT NULL                -- people.id
  forge         TEXT NOT NULL                -- 'github' | 'gitlab'
  login         TEXT NOT NULL
  UNIQUE (forge, LOWER(login))
```

- **Deployment-wide.** People are deployment-wide, as `users` is: a person may own repositories of several
  organizations, and is one person in each.
- **Hours.** A person's hours are read where the asks loop reads an organization's today. Unset fields fall back
  field by field (D3), and the grammar and validation are the asks section's, so a value moves between the two
  unchanged.
- **Sign-in link.** `user_id` links a person to their sign-in. A user with a `github_login` and no person is
  offered as an "add", never linked by itself (D6).
- **Audit.** Every write of a person or an identity is audited as settings writes are: who, when, and what changed.

## 4. Who a login is

`owners_for(area, repository)` keeps its shape and its order of sources:
1. the area's owners;
2. then the repository's owners;
3. then `CODEOWNERS`.

It now returns, for each login, the person its identity names, or the login marked unknown. Its consumers:

- **The decisions feed** shows a person by name and an unknown login as itself, with "add".
- **Asks** go to people.
  - An ask to an unknown login is not sent. The decision lists it as unknown, so a person can add them, and asks
    the decision's other owners.
  - `asks.recipient` keeps its text: the person's id once known.
- **Hand-offs** name a person by email. An email with no person is listed as unknown with "add", as a login is.
- **Who may decide.** `_may_decide` compares the signed-in user's person with the decision's owners, by identity
  instead of by `users.github_login` alone.

## 5. Asks

- **Working hours.** An ask is held to its person's working hours (D3): a reminder is sent in the person's working
  time, by the person's time zone, else the organization's, else the deployment's. `remind_hours`, `expire_days`
  and `max_open_per_person` stay the organization's.
- **Chat handles.**
  - `GET /asks/due` adds `person: {id, name, chat: {channel, id}}` to each ask whose person has a handle, for a key
    with no repository allowlist (D4).
  - A restricted key reads `person: {id, name}`.
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
2. **`feat(people): people and their forge identities`**:
   - the two tables;
   - a repository;
   - `GET/POST/PATCH/DELETE /people` and identity endpoints, written by the operator tier;
   - the "add" suggestions from `users.github_login` and from unknown owner logins.

   No consumer changes yet.
3. **`feat(owners): owners resolve to people`**: section 4, with unknown logins listed by the feed and decisions.
4. **`feat(asks): a person's hours and chat handle`**: section 5.
5. **`feat(frontend): the People page`**:
   - add and edit a person;
   - link a sign-in;
   - add identities;
   - work through the unknown logins.
6. **`chore(settings): retire the agent_questions import`**: one release after pull request 1 first deploys.

## 8. Acceptance

| Check | How |
|---|---|
| A layer cannot raise how many continues pass the budget ceiling | A repository layer sets `retry_rounds_cap: 100000`; through `max_retries_node`, once `continue_past_ceiling_max` continues have passed the ceiling, a job at it is not offered one; through the API, a used-up job with room under the ceiling is offered a continue, and a named amount past it is capped at the ceiling; a continue under the ceiling is not counted; an organization or repository layer that sets `budget.continue_past_ceiling_max` is refused. |
| An organization's layer and a repository's each set a job's caps | Start jobs for repositories with and without the layers; the first round count at which `_enforce_ask_user_cap` fails each job, the brainstorming developer's cap, triage's own cap and the continue-retrying cap follow the nearest layer (`tests/unit/services/test_agent_questions_settings.py`). |
| A running job keeps what it started with | Start a job, pause it on a question, change the layer, resume it: it keeps its cap; the next job reads the new one. |
| The import writes a set value once | `ASK_USER_ROUNDS_CAP=12` and `USER_RETRY_ROUNDS_CAP=3` are written with source `bootstrap`; a second start imports nothing; `ten`, `-1`, an empty value and the default are not imported (`tests/unit/settings/test_agent_questions_import.py`). |
| The environment is not read any more | In a fresh process with `ASK_USER_ROUNDS_CAP=2` set before the agent module loads, a job with no layer is capped at 10. |
| `embry0 config` names the variables as governed | `governed_lines` lists both with their keys. |
| People (pull requests 2 to 5) | Each pull request states its own, against sections 3 to 5. |

## 9. To decide in their pull requests

- Who may write people: the operator tier, as for areas, or admin alone. Whether an owner may add themselves.
- Whether an identity needs proof (a sign-in through that forge) before asks reach its person.
- Whether a person's handle may name more than one chat.

## 10. Out of scope

- Groups as owners (spec 2026-11-10, section 9, point 3).
- Any change to who may answer an ask or decide a decision beyond the identity lookup (section 4).
- Per-person `remind_hours`, `expire_days` or `max_open_per_person`.
