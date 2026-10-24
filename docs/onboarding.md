# Getting a repo onto embry0

Two entry points, by repo age. Neither requires committing anything to the
target repo — the qa.yaml v2 document is stored in embry0's database
(`repo_qa_configs`).

## New project: `embry0 init` (EMB-49)

One command from zero to pipeline-ready:

```bash
embry0 init acme/new-service
# or, if the repo already has an app skeleton to boot:
embry0 init acme/new-service --app web \
  --boot-command "npm ci && npm run dev" \
  --frontend-url http://localhost:3000 \
  --sandbox-profile slim
```

What it does, in one shot:

1. **Verifies the GitHub connection** with the owner-routed token
   (`GITHUB_TOKEN` / `GITHUB_TOKEN__<OWNER>`), failing fast on bad repo
   names or missing access.
2. **Registers a starter qa.yaml v2** in embry0's database. With no
   `--app`, the starter sets `qa_required: never` so the QA verify stage
   skips cleanly — the first issue→PR run on an empty repo is green. With
   `--app`, it writes a managed single-app config (boot command, ready
   check, one starter acceptance criterion) with `qa_required: auto`.
3. **Seeds `repo_preferences`** when `--sandbox-profile` is given
   (validated against the deployment's profiles).
4. **Prints what was configured + the first-issue instructions.**

Refuses to overwrite an existing stored config without `--force`.

API equivalent: `POST /api/v1/repos/bootstrap` with the same fields.

**First green run** = `embry0 init <repo>` + one issue filed (label
`embry0` in Linear, or `POST /api/v1/issues` with `auto_triage: true`).

## Existing project: `embry0 onboard` (EMB-50)

For a repo that already has code, the onboarding agent generates the QA
config instead of you writing it:

```bash
embry0 onboard acme/existing-service [--branch dev] [--no-smoke]
```

It clones the repo read-only, detects the workspace layout / boot commands
/ ports / health endpoints, drafts the qa.yaml v2, schema-validates it,
smoke-tests it (boot + ready checks only, no QA agent), and writes it to
the store — iterating on failures up to 3 rounds. Present in store =
validated.

The repo must be registered (GitHub or GitLab). Without `--branch` the
agent analyzes the repo's base branch: its `base_branch` preference, else
the forge's default branch, else `main`.

In the dashboard the agent starts from the repository page's **Let embry0
set up testing**, in propose mode (below), on the repository's `base_branch`
preference, else the default branch an organization discovery saw, else
the same forge lookup. On an onboard job's page, a
completed job links to the stored setup (or to the proposal on the
repository page); a failed one shows its error and links to the by-hand
form with the repository filled in (the agent's draft is not kept after a
failed run).

API equivalent: `POST /api/v1/jobs` with `{"pipeline": "onboard", "repo":
..., "branch": ..., "skip_smoke": ...}`; `branch` and `skip_smoke` are
optional.

## Propose mode

A repository gets its test setup one of two ways:

- **A starter from a form.** `embry0 init` and `POST /api/v1/repos/bootstrap`
  write a starter from the fields you give them. Nothing is guessed.
- **A draft from the onboarding agent.** The agent reads the code and writes
  the setup for you.

`embry0 onboard`, `POST /api/v1/jobs` with `"pipeline": "onboard"` and the
Organizations Onboard action store the agent's draft as the test setup
straight away. In propose mode the agent takes the same steps (read the
code, write a draft, check it against the schema, start each app and wait
for its ready checks), then stores the draft as a setup proposal in
`repo_setup_proposals` instead. The test setup stays as it was until a
person applies the proposal. A run that ends without a working draft
leaves a failed proposal that says why. A repository has at most one run
going or proposal waiting. `POST /api/v1/repos/{repo}/setup/propose` starts
propose mode, and the proposal is read, applied or turned down under the
same `setup/` path ([API](api.md)). Applying is refused when the test setup
was saved after the run started.

Every change to a stored test setup, from any of these paths, is kept in
the repository's history, and the newest change can be undone
([ADR 0019](adr/0019-setup-proposals.md)).

## A whole owner: the Organizations wizard

In the dashboard, **Organizations** (next to Repos) adds a GitHub
organization or user, or a GitLab group, in four steps:

1. **Connect**: the owner, the forge, and the credential to act with, a
   forge token from the registry or the environment token for that owner.
   **Probe** (`POST /api/v1/orgs/probe`, admin) asks the forge what the
   credential can do there (read, hooks, push) and how many repositories it
   sees, and stores nothing. It is the same probe registering runs.
2. **Discover**: registers the owner (`POST /api/v1/orgs`), then reads
   every repository under it (`POST /api/v1/orgs/{owner}/discover`), with
   its progress shown as it goes.
3. **Report**: one row per repository, with a chip for each sense embry0
   has for it (intake, registry, QA, watch, tracker, UI, boot), filterable
   by name or by a missing sense, and the needs list grouped by kind.
4. **Act**: tick the needs embry0 can do itself (register, hook, onboard,
   watch, and deactivate for repositories gone from the forge), preview
   them as a dry run, then apply them.

Registering an owner changes nothing for any repository until a need is
applied ([ADR 0010](adr/0010-organizations.md)). The wizard lives at
`/orgs/-/new`, and each organization keeps a page at `/orgs/<owner>` with
the latest discovery, a Rediscover button, the rediscovery schedule (every
1 to 168 hours, or off; an admin changes it in place), the context audit
schedule beside it (every 1 to 365 days, or off; [context-audit.md](context-audit.md)), the same act panel
and a line on its design standard ([standards.md](standards.md#in-the-dashboard));
no owner name starts with `-`, so an owner named `new` keeps its page too. A server
without the probe, needs or actions route (FastAPI's own 404 or 405) says
so in place of that step instead of failing; any other error is shown.

## Editing a stored config later

```bash
curl -H "Authorization: Bearer $API_KEY" \
  http://localhost:8200/api/v1/repos/<owner>/<repo>/qa-config          # read
# edit, then:
curl -X PUT -H "Authorization: Bearer $API_KEY" \
  -H 'X-Requested-With: XMLHttpRequest' --data-binary @qa.yaml \
  http://localhost:8200/api/v1/repos/<owner>/<repo>/qa-config          # write (schema-validated)
```

Full field reference: [qa-yaml-reference.md](qa-yaml-reference.md).
