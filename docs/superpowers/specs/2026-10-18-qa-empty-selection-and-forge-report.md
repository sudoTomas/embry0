# QA runs that select no app, and a forge-neutral QA report

Issue #65. Found on a deployment whose repositories live on GitLab, while
verifying an unrelated deploy with a standalone QA job.

## Problem

A standalone QA job (`POST /jobs` with `pipeline: "qa"`, default branch, no
overrides) finished in five seconds as `completed`. The stream carried the
three node pairs and `job_finished`; `GET /jobs/{id}/qa/attempts` returned an
empty list. No sandbox beyond the bootstrap one, no browser, no agent, no
line in the orchestrator log saying why nothing ran. Then the report node
logged `qa_report_skipped_no_github_token_or_repo` with `has_token: false`
for a repository the same job had resolved as `gitlab`.

Two defects, three links in the first:

1. **An empty app selection reports `passed` silently.**
   - The stored config declared the provider's app under one name
     (`workspace_provider.config.apps: ["."]`) and its `apps:` entry under
     another (`apps: { web: ... }`). `resolve_apps_to_qa` returns
     the intersection of the provider's affected set and the `apps:` keys,
     which is empty.
   - `StaticAppsProvider.validate` does report that mismatch, but its
     messages carry no `error:` / `warning:` prefix. `validate_against_qa_config`
     classifies messages by that prefix and drops everything else, so the
     only check that could have caught the misconfiguration measured
     nothing. The `npm-workspaces-turbo` provider prefixes; the fake used by
     the tests prefixes; only the static provider does not.
   - `qa_orchestrator_node`'s `if not apps:` branch returns
     `overall_status="passed"` without emitting `qa_apps_resolved`, without a
     progress event and without a log line. The executor maps `passed` to
     job status `completed`.
2. **The report step is GitHub-only.** `qa_report_node` looks up a GitHub
   token for every repository, logs a GitHub-specific skip when it finds
   none, and has no GitLab path at all. On GitLab deployments the sticky QA
   comment is never written and the log blames a missing GitHub token.

## Design

### 1. Provider validation messages are classified fail-closed

- `StaticAppsProvider.validate` prefixes both of its messages with
  `error: `. Both are errors: a provider with no apps can never run anything,
  and a `qa.yaml` app the provider does not declare can never be selected.
- The `WorkspaceProvider.validate` docstring states the contract: every
  message starts with `error:` or `warning:` (case-insensitive).
- `validate_against_qa_config` treats a message with neither prefix as an
  error and logs `qa_validate_message_unprefixed` with the provider's name,
  so a provider that forgets the prefix fails the run instead of being
  ignored. This is the "prove a check can fail" rule from CLAUDE.md applied
  to the validation step itself.

### 2. An empty selection is visible, and without a change context it fails

In `qa_orchestrator_node`, when the selected app list is empty after every
selection rule (`force_all_apps`, the affected set, deployed gating,
`exclude_deployed_apps`):

- Emit `events.qa_apps_resolved(apps=[])` and a `progress` event whose
  message states the reason. Log `qa_no_apps_to_qa` with `declared_apps`,
  `provider_affected`, `n_changed_files` and `reason`.
- `reason="diff_touched_no_declared_app"` (the diff is non-empty): the
  outcome stays `passed`. A pull request that changed nothing under QA gates
  green, as today and as documented. The reason sentence is appended to
  `outcome.validation_warnings`, so the sticky comment says so instead of
  showing an empty table.
- `reason="no_change_context"` (the diff is empty): the outcome is
  `overall_status="failed"`, `final_status="failed"`,
  `qa_state["error_code"] = "ERR_QA_NO_APPS"`, and `failure_summary` /
  `error_message` carry one sentence naming the declared apps, the
  provider's affected apps and the three remedies: `qa_required: always`,
  `force_all_apps` on the job, or an app with no `prefixes` (always
  affected). A standalone run on the default branch always has an empty
  diff, so this is exactly the vacuous case from the issue.
- `qa_required: never` keeps `passed` (an explicit operator opt-out) but now
  also emits `qa_apps_resolved(apps=[])` and the progress event, so the
  stream says why nothing ran.
- New `ErrorCode.QA_NO_APPS = "ERR_QA_NO_APPS"`, category `CONFIG`, public
  text "QA selected no app to run: the branch has no changes against its
  base and no app is always on". Listed in `docs/architecture.md` next to
  `ERR_QA_CONFIG_INVALID`.

Why fail rather than run every app: promoting empty-diff runs to
`force_all_apps` would change the cost of every default-branch QA run on
every deployment; the opt-ins exist for operators who want that. Why fail
rather than `skipped`: the operator asked for QA and the configuration
cannot select anything, which is a configuration error; `failed` with a
`CONFIG` code lands in the same bucket as `ERR_QA_CONFIG_INVALID`, where the
dashboards and the error taxonomy already look.

### 3. The report step is forge-aware

- `init_orchestrator_node` already resolves the forge for the clone; it now
  writes it to `qa["forge"]`.
- `qa_report_node`:
  1. No `issue_number` (a standalone QA job has no issue or pull request to
     comment on): log `qa_report_skipped_no_change_request` and return,
     before any token lookup.
  2. `forge = qa_state.get("forge")`, falling back to
     `resolve_forge(repos_repo, repo)` for states written before this
     change, then to `"github"`.
  3. `github`: unchanged path. The skip log becomes
     `qa_report_skipped_no_forge_token` with `forge="github"`.
  4. `gitlab`: `make_gitlab_client(embry0_config, credential_resolver)` and
     `client.upsert_sticky_issue_note(repo, issue_number, body)`. A failure
     is logged as `qa_comment_upsert_failed` with `forge="gitlab"`; it never
     fails the run, same as GitHub.
- New `GitLabClient.upsert_sticky_issue_note(repo, iid, body, *, marker) -> int`:
  `GET /projects/{id}/issues/{iid}/notes` through the shared `paginate`,
  earliest note whose body contains the marker is updated with
  `PUT /projects/{id}/issues/{iid}/notes/{note_id}`, otherwise
  `POST /projects/{id}/issues/{iid}/notes`. Returns the note id. Mirrors
  `upsert_sticky_comment` for GitHub, marker included.

Why an issue note and not a merge request note: in the issue-to-PR pipeline
QA runs before the pull request exists, and on GitHub the comment lands on
the issue through the issues endpoint that pull requests share. The GitLab
equivalent of that target is the issue's notes.

### 4. Documentation

`docs/qa-yaml-reference.md`: step 4 of the flow, the `static-apps` section
and the gotchas say that `apps:` keys must be the names the provider
declares (a mismatch fails the run with `ERR_QA_CONFIG_INVALID`), that an
affected but undeclared app is skipped with a warning in the comment and a
log line, and that a run with no change context and no selected app fails
with `ERR_QA_NO_APPS`. `docs/architecture.md`: the error-code table gains
`ERR_QA_NO_APPS`.

## Out of scope

- `verification_publish` still posts a plain (non-sticky) note on GitLab.
- The issue-to-PR routing bounces a `failed` QA to triage even for
  configuration errors; pre-existing, unchanged.
- Correcting the stored config on the deployment that hit this is an
  operator step performed at deploy time, not a tree change.

## Verification

- Every new behaviour has a unit test shown failing on the pre-fix tree.
- Full unit suite on a throwaway Postgres, failure set diffed against `main`.
- On the deployment that reported the issue, after deploy and before the
  config is corrected: the same standalone QA job fails with
  `ERR_QA_CONFIG_INVALID`, its message names the mismatch, the stream shows
  `qa_apps_resolved` is not reached (validation fails first), and the report
  step logs `qa_report_skipped_no_change_request`. After the config is
  corrected, the job runs one attempt.
