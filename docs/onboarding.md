# Getting a repo onto embry0

Two entry points, by repo age. Neither requires committing anything to the
target repo — QA config lives in embry0's [external config
store](../repo-configs/README.md) (EMB-48).

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
2. **Registers a starter qa.yaml v2** in the external config store. With no
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
embry0 onboard acme/existing-service [--branch main] [--no-smoke]
```

It clones the repo read-only, detects the workspace layout / boot commands
/ ports / health endpoints, drafts the qa.yaml v2, schema-validates it,
smoke-tests it (boot + ready checks only, no QA agent), and writes it to
the store — iterating on failures up to 3 rounds. Present in store =
validated.

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
