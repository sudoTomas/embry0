"""The procedure the MCP prompt ``setup`` hands a client: setting up a deployment from a chat session.

A person who connects Claude Code (or any MCP client) to a running deployment
types ``/mcp__embry0__setup`` and the client receives this text. It names the
tools in the order setup needs them, and every name in it is a registered
tool (``tests/unit/mcp/test_setup_prompt.py`` pins that). The human guide to
the same session is ``docs/claude-code.md``.
"""

from __future__ import annotations

SETUP_PROMPT = """\
Set up this embry0 deployment with the person, through the embry0 MCP tools.

Rules for the whole session:
- Read the state before you change it, and show each change before you make it.
- A tool that takes confirm=true changes something. Call it with confirm=true only after the person approves that exact call.
- Never ask for a secret in this chat, and never put one in a tool call. A token typed here goes into the transcript and to the model provider. A person adds each credential on the dashboard (Infra, then Credentials) or from their own terminal with the API. No tool takes a secret: you only list and bind credentials.
- An organization's onboard action starts one paid agent job per repository. Name the repositories and ask before you start them.

1. Connection and key
- Call health. It answers when the deployment is up.
- Every write below needs a key with the admin tier. A 403 names the missing tier: ask the person for an admin key.

2. Model access
- Call provider_status. available=true says an account exists for the provider: from environment variables (credential_source oauth, api_key or oauth+api_key) or as a credential row (registry). A row counts there even when nothing binds it, and a job gets a row only through a binding.
- With no account, ask the person to add one on the dashboard: a Claude subscription token from `claude setup-token` (kind llm_oauth), or an API key (kind llm_api_key). An API key also needs DEFAULT_AUTH_MODE=api_key in the root .env and an orchestrator restart, unless the deployment already uses that mode.
- For a registry account, call credential_list for its id, then credential_bindings_get with scope_kind "global". When no binding with role "llm" names it, bind it with credential_bindings_set. That call replaces every binding of the scope: send back every binding you read, whatever its role, with the new one added.

3. Forge access
- Call credential_list for a forge_token on the forge. Without one, ask the person to add it on the dashboard. A GitHub fine-grained token on the owner needs Contents, Issues, Pull requests and Webhooks read and write; Checks, Commit statuses and Metadata read; and, for an organization, its Webhooks permission read and write. A classic token needs repo and admin:org_hook.
- Call org_probe with the owner, the forge and the credential id. It reports the identity, read, hooks and the repository count, and stores nothing.
- Bind the token to the owner: credential_bindings_get with scope_kind "owner" and scope_value "<forge>:<owner>", then credential_bindings_set with the full list.

4. The owner and its repositories
- Call org_register with the owner, the forge and the credential id.
- Call org_discover, then org_runs until the newest run's status is done or error. On error, tell the person its error and stop: the steps below read a finished run.
- Call org_needs. Each need names its kind, its repositories and the action that answers it.

5. Act on the needs
- Registration: org_act with action "register" and dry_run=true, show the result, then the same call with dry_run=false.
- Webhooks: for a GitHub organization, org_webhook_install installs one webhook for every repository, present and future. Do not also run org_act with action "hook" there: a repository hook next to the organization's makes GitHub send each event two times. For a user owner, org_act with action "hook" adds one per repository. Both need WEBHOOK_PUBLIC_URL and GITHUB_WEBHOOK_SECRET in the root .env.
- Testing: org_act with action "onboard" lets the onboarding agent write each repository's test setup, one paid job each. repo_skip_testing stores a setup with testing off, at no cost. Ask the person which repositories get which. Then follow the jobs with job_list and job_wait.
- Watching and environment values: report them, and act only when the person asks.

6. Spend limits
- Call settings_effective and read the budget section: the per-job ceiling and the daily and monthly caps. Change them only on the person's word, with settings_set_keys and dry_run=true first.

7. Verify
- For one repository, call credential_resolve with role "forge", then with role "llm" and its provider. Both must resolve.
- An org_act call that changed something, and an org_webhook_install that created the hook, start a discovery run themselves. Wait for it with org_runs (call org_discover only when no run is running), then call org_needs: no not_registered or no_webhook need should be left.
- Optional, with the person's yes: issue_create a small real task with auto_triage, then job_wait on the job it starts.

Report at the end: what is set, what each step measured, and what is left for a person, with the reason.
"""
