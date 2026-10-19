# Credential registry

Every account embry0 acts as is a row in the `credentials` table, encrypted at rest, and reaches runtime code through one service, `CredentialResolver` (`embry0/services/credentials/resolver.py`). A repo says which account it uses by a **binding**. The env variables that used to be the only source (`GITHUB_TOKEN`, `GITHUB_TOKEN__<OWNER>`, `GITLAB_TOKEN`, `GITLAB_TOKEN__<GROUP>`) still work: they are an import source and the resolver's last step. See [ADR 0006](adr/0006-credential-registry.md) for the decision, and [Later releases](#later-releases) for what comes next.

## What a credential is

A credential has a `kind`, a `provider`, a `host`, a `label` unique per provider, a write-only secret, an optional git author (`git_author_name`, `git_author_email`) and a `status`:

| kind | providers | used for |
|---|---|---|
| `forge_token` | `github`, `gitlab` | clone, push, pull and merge requests, forge API calls |
| `llm_oauth`, `llm_api_key` | `anthropic`, `xai` | agent runs |
| `llm_api_key` | `openai`, `openrouter`, `google`, `groq`, `deepseek`, `custom`, each once `OPENAI_COMPATIBLE_PROVIDERS` names it ([providers.md](providers.md#openai-compatible-providers)) | agent runs |
| `ssh_key` | `ssh` | orchestrator-side deploys (later release) |
| `tailnet_api_key` | `tailscale` | reading a tailnet's devices to import and probe [hosts](hosts.md) |

**This release stores forge tokens and LLM accounts.** `POST /api/v1/credentials` accepts `forge_token`, `llm_oauth` and `llm_api_key`. xAI `llm_oauth` is the exception: that route answers 422, and the row is created by device-code login or by `embry0 credentials import-env`. `ssh_key` is still a later release. Env values remain the last step when no binding matches.

A `tailnet_api_key` is created with `POST /api/v1/credentials` at host `api.tailscale.com`, with `metadata` `{"tailnet": "<name>"}` (`-`, the key's own tailnet, when omitted). A `custom` LLM key carries `{"base_url": "<API root>"}` and its host is that URL's; `metadata` is refused on every other credential. It binds only at global scope, with role `tailnet`, and has no env fallback. [hosts.md](hosts.md) has the scope the key needs.

A credential's `host` must be the forge's own host in this release: `github.com` for GitHub, the host of `GITLAB_URL` for GitLab. Leave it empty and it defaults to that; anything else is a 422, because clone URLs and API bases are still single-host.

A credential's `status` is `ok`, `disabled` or `quarantined`. An operator sets `ok` or `disabled` through `PUT /api/v1/credentials/{id}`; embry0 itself sets `disabled` with `status_reason=decryption_failed` when the secret cannot be decrypted (see [Encryption and rotation](#encryption-and-rotation)). Quarantine is written only by `CredentialHealth`. Setting `ok` clears it.

## Precedence

For a job on a repo, the forge token comes from the first usable binding in this order:

```
repo > owner > host > global > env
```

| scope | `scope_value` example | matches |
|---|---|---|
| `repo` | `acme/widgets` | that repo only; the exact path, lowercased |
| `owner` | `github:acme` | every repo of the owner or top-level group on that forge |
| `host` | `gitlab.example.com` | every repo on that forge host |
| `global` | empty | every repo on the credential's forge |
| env | `GITHUB_TOKEN__ACME`, then `GITHUB_TOKEN` (or the GitLab pair) | the pre-registry lookup, unchanged |

Scope values are normalized on write by the API and the repository, and the resolver normalizes the same way on lookup:

- **owner**: `<forge>:<owner>`, with the owner lowercased and every `-` and `.` replaced by `_`. The owner `My-Org` on GitHub is `github:my_org`. The `github:` or `gitlab:` prefix is required and must match the credential's provider; `PUT /api/v1/credentials/bindings` answers 422 otherwise. The owner is the first path segment, so a GitLab repo `acme/platform/widgets` matches `gitlab:acme`.
- **repo**: the exact path, lowercased.
- **host**: lowercased, and it must equal the forge's host (`github.com`, or the `GITLAB_URL` host); anything else is a 422.

A bound credential that is `disabled` or `quarantined`, or whose secret cannot be decrypted, is skipped and the next step is tried. A **pinned** binding is not skipped: the job fails with `ERR_CREDENTIAL_UNAVAILABLE` instead of using another account. Pinning is accepted only on LLM bindings at repo or owner scope, so a forge-token binding is never pinned. When nothing matches, not even the environment, resolution fails with `ERR_CREDENTIAL_MISSING`.

One binding per scope, role and provider. `acme/widgets` bound to the credential `alice` and `github:acme` bound to `acme-bot` means `acme/widgets` pushes as `alice` and every other `acme` repo as `acme-bot`.

## LLM accounts

An LLM account is `llm_oauth` or `llm_api_key` on `anthropic` or `xai`, or `llm_api_key` on an OpenAI-compatible provider the deployment enables (`docs/providers.md`). The resolver picks one per provider before the sandbox exists, records it on `credential_uses`, and reuses that row for the rest of the job. Precedence, for one provider:

```
repo > owner > global pool > env
```

Owner is tried as `github:<owner>` and then `gitlab:<owner>`. There is no host step. The global pool is every `ok` credential of that provider bound at global scope, role `llm`, ordered by `priority` ascending and then `last_used_at` ascending with nulls first. A disabled row is skipped. A quarantined row is skipped while `quarantined_until` is null or still in the future; a past `quarantined_until` is usable again without a rewrite.

Pinning is accepted on an LLM binding at repo or owner scope. A pinned credential that is disabled, quarantined, or cannot be decrypted fails the job with `ERR_CREDENTIAL_UNAVAILABLE`. It does not fall through to the pool. A global binding cannot be pinned.

What the sandbox receives:

| provider | kind | sandbox env |
|---|---|---|
| anthropic | `llm_oauth` | `CLAUDE_CODE_OAUTH_TOKEN` |
| anthropic | `llm_api_key` | `ANTHROPIC_API_KEY`, and the OAuth token is unset |
| xai | `llm_api_key` | `XAI_API_KEY` |
| xai | `llm_oauth` | nothing in env; the xai-proxy serves that credential id |
| openai, openrouter, google, groq, deepseek, custom | `llm_api_key` | `EMBRY0_LLM_<PROVIDER>_API_KEY`, and for custom `EMBRY0_LLM_CUSTOM_BASE_URL` |

`auth_mode=oauth` needs an anthropic `llm_oauth` account. `auth_mode=api_key` needs an anthropic `llm_api_key` account, from the registry or the env step below. A mismatch raises the existing `ERR_MISSING_OAUTH_TOKEN` or `ERR_MISSING_API_KEY`. A grok model does not take the Anthropic check.

Env fallback, only when no registry row is usable. For Anthropic it follows the deployment's `DEFAULT_AUTH_MODE`: with `api_key`, `ANTHROPIC_API_KEY` and nothing else; otherwise `CLAUDE_CODE_OAUTH_TOKEN`, then the orchestrator's Claude credentials file. For xAI, `XAI_API_KEY`. For an enabled OpenAI-compatible provider, its key variable (`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, or `CUSTOM_LLM_API_KEY` together with `CUSTOM_LLM_BASE_URL`). Each key logs `credential_env_fallback` once. A repo, job or agent that overrides the auth mode away from the deployment default needs a registry account of the matching kind. `ANTHROPIC_API_KEY` is not an import source.

**Quarantine.** A terminal `ERR_PROVIDER_RATE_LIMITED` sets `quarantined_until` to now plus `CREDENTIAL_RATE_LIMIT_QUARANTINE_SECONDS` (default 900). `ERR_PROVIDER_QUOTA_EXHAUSTED` lasts until the reset the provider named, when it named one, capped at eight days (`QUOTA_RESET_QUARANTINE_MAX`); otherwise it uses `CREDENTIAL_QUOTA_QUARANTINE_SECONDS` (default 3600). `ERR_AUTH_REJECTED` quarantines with no until, so an operator clears it. The hook attributes the failure to the terminal agent output's provider, so a 429 on xAI leaves the Anthropic account alone. A missing provider on that output quarantines every LLM account the job used.

**An xAI 403 is read by its body.** xAI answers three unrelated conditions with HTTP 403, and only the body tells them apart (`embry0/safety/xai_denials.py`; the phrases follow NousResearch/hermes-agent, which handles the same account type):

| Body says | Code | Account |
|---|---|---|
| `personal-team-blocked:spending-limit` / "spending limit" | `ERR_PROVIDER_QUOTA_EXHAUSTED` | quarantined `spending_limit` for `CREDENTIAL_QUOTA_QUARANTINE_SECONDS`, then retried |
| "do not have an active grok subscription" (or "out of available resources" with "grok") | `ERR_AUTH_REJECTED` | quarantined `no_active_subscription` with no until; renew the subscription, then set the credential back to `ok` |
| `[WKE=unauthenticated:` / "access token could not be validated" | `ERR_PROVIDER_TOKEN_STALE` | not quarantined; the xai-proxy counts the hit and the orchestrator's token loop force-refreshes and pushes that account within a minute |

Any other 403 stays `ERR_AUTH_REJECTED`. The run's error leads with what the body meant, then keeps xAI's own words. A job that failed on a stale token is not retried automatically: run it again once the refresh is logged (`xai_credential_force_refreshed`).

**A spent usage window is not a rate limit.** When a subscription account runs out of usage, the Claude CLI ends the run with a synthetic message flagged `rate_limit`, the same flag as a transient 429. Only its text tells them apart: "You're out of extra usage · resets Oct 5, 12am (UTC)". The executor reads that wording (`USAGE_EXHAUSTED_PHRASES` in `embry0/safety/error_codes.py`), and a `rate_limit_info` that rejects a named window, as `ERR_PROVIDER_QUOTA_EXHAUSTED`. It keeps the provider's sentence as the run's error, so the trace summary and the job's `error_message` show it, and it reads the reset: a time alone is its next occurrence, a month and day without a year the next such date, and a reset it cannot place is left unset. A job whose last agent run failed on the account carries that run's code, even where a node would have reported something else, such as triage's `ERR_TRIAGE_MALFORMED`.

**A provider triage picks late.** The pre-check resolves every model named by agent definitions, the job's `agent_models` override, or the model policy (the default model and the tier models). `anthropic` is always in that set. A model that triage selects later, and that none of those sources named, is absent from the sandbox: the env cannot be changed after create. The executor's existing missing-key message is classified as `ERR_CREDENTIAL_MISSING`. A job that needs Grok must name a `grok-` model in an agent definition, the job override, or the model policy.

**Sandbox SDK pin.** The sandbox images pin `claude-agent-sdk==0.2.164`, and agents run the CLI bundled in it (2.1.292); no other Claude CLI is installed (#497, #501). Rate-limit fields are still read with `getattr`, so an image built from an older pin still runs, and a message with no needle falls back to the text classifier.

The xAI refresh loop pushes each registered `llm_oauth` account. It does not also refresh the file-backed token: that lineage rotates on every use, and a second refresh kills the row just imported. After `import-env`, the registry owns it. A sandbox enrolled with a credential id and no pushed token gets 503 `credential_token_missing`.

## Env fallback and the importer

A deployment that upgrades and does nothing else keeps working: when no binding matches, the resolver reads `GITHUB_TOKEN__<OWNER>` then `GITHUB_TOKEN` (or `GITLAB_TOKEN__<GROUP>` then `GITLAB_TOKEN`) exactly as before. Each env key served that way logs `credential_env_fallback` once per process, naming the key, and is listed in `GET /api/v1/stats` under `credentials.env_fallback_keys`. An empty list there, after the stack has run jobs on every forge you use, means nothing is served from env any more.

To move the env tokens into the registry, run the importer once, from inside the orchestrator container so it reads the orchestrator's environment:

```bash
docker exec embry0-orchestrator embry0 credentials import-env
# or
curl -X POST http://localhost:8200/api/v1/credentials/import-env \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"replace": false}'
```

What it reads in this release, and what it creates:

| env variable | credential | binding |
|---|---|---|
| `GITHUB_TOKEN` | github `forge_token`, label `default` | global, forge |
| `GITHUB_TOKEN__ACME` | github `forge_token`, label `acme` | owner `github:acme`, forge |
| `GITLAB_TOKEN` | gitlab `forge_token`, label `default`, host from `GITLAB_URL` | global, forge |
| `GITLAB_TOKEN__ACME` | gitlab `forge_token`, label `acme` | owner `gitlab:acme`, forge |
| `CLAUDE_CODE_OAUTH_TOKEN`, else the Claude credentials file | anthropic `llm_oauth`, label `default` | global, llm, priority 0 |
| `XAI_CREDENTIAL_PATH`, else `/home/orchestrator/.grok/auth.json` | xai `llm_oauth`, label `default` | global, llm, priority 0 |
| `XAI_API_KEY` | xai `llm_api_key`, label `api-key` | global, llm, priority 1 |
| an enabled OpenAI-compatible provider's key variable (`custom`: with `CUSTOM_LLM_BASE_URL`) | that provider's `llm_api_key`, label `api-key` | global, llm, priority 0 |

`XAI_GROK_CLI_STORE`, when set, replaces `/home/orchestrator/.grok/auth.json` as the Grok CLI file the importer reads. `ANTHROPIC_API_KEY` is not imported. The credential file wins over the Grok CLI file when both exist. The stored xAI oauth secret is the JSON payload `refresh_token`, `client_id`, `token_endpoint`. The access token is not stored.

The importer is **idempotent on provider + label**: a credential that already exists with the same secret is left alone, so running it twice creates nothing the second time. One that exists with a *different* secret is reported under `skipped` and left unchanged, unless you pass `--replace` (`"replace": true` over the API), which rotates it to the env value. Two env keys holding the same token become one credential with two bindings. A scope that already has a forge binding for that provider is reported under `skipped` and keeps its binding. The response lists `created`, `bound` and `skipped`; the CLI prints the same, `--json` prints the raw report, and the command exits `0` when nothing was skipped, `2` when something was, `1` on failure. The CLI sends `X-Requested-With: XMLHttpRequest` like the other commands, because the API's CSRF middleware requires it.

After a clean import, remove the `GITHUB_TOKEN*` and `GITLAB_TOKEN*` lines from the repo-root `.env` and restart the orchestrator (`infra/deploy.sh orchestrator`). `credentials.env_fallback_keys` then stays empty.

## Commit identity

The author of the commits a job makes is resolved per field, `name` and `email` independently:

1. the repo's preference (`repo_preferences.git_author_name` / `git_author_email`), if set;
2. else the bound credential's `git_author_name` / `git_author_email`, if set;
3. else the branding default.

So a repo bound to the credential `alice` with author `Alice <alice@example.com>` commits as Alice, unless its preferences override one field or both. A token served from env carries no author, so step 2 is skipped.

## API

All under `/api/v1`. Reads need any authenticated principal; every mutation needs the `admin` tier and writes an audit row.

| method and path | tier | purpose |
|---|---|---|
| `GET /credentials` | read | list: id, kind, provider, host, label, `masked_preview`, `verified_login`, status, `status_reason`, `quarantined_until`, `binding_count`, timestamps |
| `POST /credentials` | admin | create: `kind`, `provider`, `host`, `label`, `secret`, `git_author_name`, `git_author_email`. 409 on a duplicate label, 422 on a wrong host |
| `PUT /credentials/{id}` | admin | `label`, author fields, `status` in {`ok`, `disabled`}. An omitted field is kept; an author field sent as `null` or blank is cleared, so the author falls back to branding |
| `POST /credentials/{id}/rotate` | admin | new `secret`; sets `rotated_at`, clears `decryption_failed` |
| `POST /credentials/{id}/verify` | admin | forge tokens only: asks the forge `GET /user` and stores `verified_login` |
| `DELETE /credentials/{id}` | admin | 409 while bindings reference it |
| `GET /credentials/resolve?repo=&role=forge` | read | dry run: which credential a job on `repo` would get, and why |
| `GET /credentials/bindings?scope_kind=&scope_value=` | read | the bindings of one owner, host or global scope |
| `PUT /credentials/bindings` | admin | replace the bindings of one owner, host or global scope |
| `POST /credentials/import-env` | admin | the importer above |
| `POST /credentials/xai/device-login` | admin | body `{label}`. Starts an xAI device-code login. Returns `login_id`, `user_code`, `verification_uri_complete`, `interval`, `expires_at`. Does not return `device_code` |
| `GET /credentials/xai/device-login/{login_id}` | admin | `{"status":"pending"}`, `{"status":"expired"}`, or `{"status":"done","credential":...}` after the `llm_oauth` row exists. The response never includes the refresh token |
| `GET /repos/{repo}/credentials` | read | the repo's bindings plus the effective forge resolution |
| `PUT /repos/{repo}/credentials` | admin | replace the repo's bindings; registers the repo if unknown, as the QA config write does |

**There is no reveal endpoint.** A secret is write-only once created: responses carry `masked_preview` and never the secret; `verify` never echoes the token and reports a rejection as `HTTP <status>` only; a 422 on a `/credentials` path lists the failing fields without the submitted values; logs, audit details and error messages never contain a secret. Every create, rotate, import and delete also refreshes the log scrubbers' set, so a registry token that never entered the environment is still redacted from agent stderr.

Binding a repo and checking the result:

```bash
curl -X POST http://localhost:8200/api/v1/credentials \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"kind": "forge_token", "provider": "github", "label": "alice", "secret": "github_pat_...",
       "git_author_name": "Alice", "git_author_email": "alice@example.com"}'

curl -X PUT http://localhost:8200/api/v1/repos/acme/widgets/credentials \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"bindings": [{"credential_id": "cred-0123456789ab", "role": "forge"}]}'

curl "http://localhost:8200/api/v1/credentials/resolve?repo=acme/gadgets&role=forge" \
  -H "Authorization: Bearer $API_KEY"
```

The dry run for `acme/gadgets`, which has no repo binding but whose owner is bound:

```json
{
  "id": "cred-ba9876543210",
  "label": "acme-bot",
  "provider": "github",
  "host": "github.com",
  "source": "registry",
  "trace": ["repo:acme/gadgets -> none", "owner:github:acme -> acme-bot"]
}
```

On a deployment that has not imported yet, the same call ends in `"source": "env"`, `"id": null`, a label naming the env key, and a trace step such as `env:GITHUB_TOKEN__ACME`. A skipped credential shows as `owner:github:acme -> acme-bot skipped (disabled)`. The call answers 404 for an unregistered repo or when nothing resolves, and 409 when a pinned credential is unavailable.

**Discovery.** `GET /api/v1/github/discoverable-repos` and `GET /api/v1/gitlab/discoverable-projects` fan out across every registry credential for that forge that is not `disabled`, then the env tokens (`GITHUB_TOKEN` / `GITHUB_TOKEN__<OWNER>`, or the GitLab pair), and subtract repos already registered. Owner-scoped and repo-scoped credentials are included. The same token stored in the registry and the environment is listed once, as the registry credential. When two different tokens can both see a path, the first one wins: registry accounts are walked in label order, then the env tokens.

A row has `credential_id` and `credential_label`. `credential_id` is null for an env token. On GitHub, `token_env_key` is that env key for an env row and empty for a registry row. GitLab rows have no `token_env_key`: an env row puts the env key in `credential_label`. `failed` on the response is the labels whose listing raised, never the token. `?refresh=true` bypasses the cache. The field list is in [api.md](api.md#discovery).

`GET /api/v1/github/repos` is a different list, for the create-issue dropdown, and still walks env tokens only.

## xAI device-code login

`POST /credentials` answers 422 for `provider=xai, kind=llm_oauth`. That secret is a JSON refresh payload (`refresh_token`, `client_id`, `token_endpoint`). The writers are this flow and `embry0 credentials import-env`.

An admin posts `{"label": "grok-a"}` to `POST /api/v1/credentials/xai/device-login`. embry0 reads the xAI discovery document, refuses any device or token endpoint that is not HTTPS on `x.ai`, and returns the user code plus the verification link. `device_code` stays on the server, in memory, until the login finishes or expires. The operator opens the link, enters the code, and the client polls `GET /api/v1/credentials/xai/device-login/{login_id}` every `interval` seconds.

`pending` means xAI has not finished. `expired` means the login's own TTL passed, and embry0 does not poll. `done` creates the row at host `api.x.ai` and returns it as a normal credential (`masked_preview` only). The access token from the token response is not stored. When the global scope has no xAI llm binding yet, the new row is added there at priority 0 and every other global binding is kept. A second xAI account is stored and not added to that scope.

A failure body is one of `endpoint_rejected`, `unreachable`, `error`, `unknown`, `expired_token`, `access_denied`. Any other reason is reported as `error`. The public device client id is a constant in `embry0/execution/xai_device_login.py`. It is not a deployment secret and it is not a request field.

## Dashboard

The Credentials page is `/credentials`, under Infra. Any signed-in operator can read it. The page enables add, import from the environment, rotate, verify, enable or disable, delete, and global, owner and host binding edits for an API-key session or a user whose tier is `admin`. Every other session sees those controls disabled, with the tooltip "Admin scope required"; they are not hidden. A bearer without the admin tier still gets 403.

The list shows `masked_preview` only. There is no reveal, and the page never renders a secret after create or rotate.

The create form passes no GitLab host, so it cannot select GitLab. Create a GitLab account with `POST /api/v1/credentials` or with `embry0 credentials import-env` (the importer sets the host from `GITLAB_URL`). GitHub accounts can be created in the form; the host is `github.com`. For an xAI account (kind LLM API key, provider xAI) the form also offers **Sign in with xAI**, the [device-code login](#xai-device-code-login), which stores an xAI `llm_oauth` account under the label instead of a pasted key; no other kind offers it.

Import from environment runs the importer above. Checking "Replace differing secrets" re-runs it with `replace: true`. The page shows the `created`, `bound` and `skipped` report.

The bindings drawer edits one scope at a time: global, owner or host. Saving replaces that scope's bindings, keeping every other credential already bound there and adding this one at the role its kind binds with: `forge` for a forge token, `llm` for an LLM account, `deploy` for an SSH key, `tailnet` for a tailnet key. The drawer offers only the scopes that role is looked up at: an LLM account global or owner, an SSH key host, a tailnet key global. An owner scope is `github:<owner>` or `gitlab:<owner>` for every role; an LLM account's starts at `github:`, and its resolver looks under both. Removing drops this credential from that scope. Repo bindings are not edited in the drawer.

A repo's detail page has an Accounts section: the effective forge account (its label and whether it came from the registry or the environment), the precedence trace, and the missing-credential error when nothing resolves. A pinned credential that cannot be used is shown in that same place. The forge bind picker lists credentials for that repo's forge, plus "(none — use owner/global/env)", which clears the repo's forge binding and leaves other roles. The picker follows the same admin rule as the Credentials page.

Add repo shows the discovering account on each row that came from the registry (its label). A GitHub row from the environment shows the env key instead. Registering a row that has a `credential_id` binds that credential to the new repo at role `forge`. A 403 on the bind leaves the repo registered and tells the operator "Registered, but binding the account needs admin scope". A row with no `credential_id` is registered without a binding, and so is manual entry.

## MCP

Three tools. None takes a `provider` argument. None creates a credential or returns a secret.

| tool | effect |
|---|---|
| `credential_list()` | registered accounts: id, label, provider, host, masked preview, status, binding count |
| `credential_resolve(repo, role="forge")` | dry run for that repo and role, with the precedence trace |
| `credential_bind(repo, credential_id, role="forge", pinned=False, confirm=False)` | replaces that role's binding on the repo and keeps the others |

Without `confirm=true`, `credential_bind` returns `confirmation_required` and does not change the binding. With `confirm=true` it needs an admin-scope key; the API answers 403 otherwise. `pinned=true` is still rejected on a forge binding — see [Precedence](#precedence). `repo` is `group/name`, or a GitLab subgroup path such as `group/subgroup/project`.

## Proxies

At orchestrator startup the credential proxies launch when any credential for their forge exists that is not `disabled`, in the registry or in the environment: `git-proxy` for either forge, `github-proxy` for GitHub, `gitlab-proxy` for GitLab. A deployment with no `GITLAB_TOKEN` but a GitLab credential in the registry gets its `gitlab-proxy`, started without a baked token: per-sandbox enrollment carries the resolved token, and the forge payload goes only to `git-proxy` and that forge's own API proxy. Rotating or rebinding a forge credential therefore needs no restart; the next sandbox is enrolled with the new token. Adding the *first* credential for a forge does need one (`infra/deploy.sh orchestrator`), because the launch gate is checked at startup.

**Rollout.** `infra/deploy.sh` does not rebuild the proxy image. This release changes the xai-proxy (one access token per credential), so a deploy needs that image rebuilt, then an import from inside the orchestrator (`embry0 credentials import-env`) before grok-via-proxy has a registry account to push. The forge release changed `embry0/execution/proxy/server.py` the same way (the proxy accepts an empty baked token):

```bash
cd infra
docker compose --profile images build proxy-image-builder
docker compose up -d --force-recreate init-push-images orchestrator
```

The migration runs at startup; no env change is required. The importer is an explicit operator action, never a startup side effect.

## Encryption and rotation

Credential secrets are encrypted with the same Fernet provider, and the same key, as per-repo env-var secrets: `ENVIRONMENT_SECRET_KEY`, with `ENVIRONMENT_SECRET_KEYS_OLD` (comma-separated, newest first) for key rotation. Rotating the key the documented way — move the old key into `ENVIRONMENT_SECRET_KEYS_OLD`, set a fresh `ENVIRONMENT_SECRET_KEY`, restart — keeps every credential readable. See [configuration.md](configuration.md#secrets-at-rest).

A credential no listed key can open is never handed to a job as garbage. On the first resolution that meets it, the resolver logs `credential_decryption_failed`, marks it `disabled` with `status_reason=decryption_failed`, and moves on to the next candidate. `verify` answers 409 for it. `POST /credentials/{id}/rotate` with the secret re-entered sets it back to `ok`.

## Audit

Every mutation writes an `audit_log` row with the credential id (and label where relevant), never the secret: `credential.created`, `credential.updated`, `credential.status_changed`, `credential.rotated`, `credential.verified`, `credential.deleted`, `credential.bound` (with scope and credential ids; an empty binding list is how a scope is unbound) and `credential.imported` (with the importer's report).

Which account a job used is in `credential_uses`: one row per registry resolution for a job, with the credential id and a copy of its label (which survives deleting the credential), the job id, role, provider and time. Its `outcome` is `NULL` while the job runs and is stamped at every terminal write: `ok`, the job's error code, `cancelled` when the issue is cancelled through the API, or `ERR_ORPHANED` (`ERR_RESUME_LIMIT`) when the startup sweep reaps a job the previous process left running and boot does not resume (#307). A job boot resumes keeps its rows open. A registry resolution also sets `credentials.last_used_at`. Tokens served from env leave no row; `credentials.env_fallback_keys` is their trace.

## Later releases

Planned, not yet built:

- **SSH keys**: the `ssh_key` kind and host-scoped deploy bindings, read only by orchestrator-side deployers.
- **Mid-job failover**: swapping a quarantined LLM account without restarting the sandbox.
