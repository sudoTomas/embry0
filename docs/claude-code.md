# Set up embry0 from Claude Code

A Claude Code session can take a running deployment from no configuration to working jobs through embry0's MCP server: model access, a forge credential, an organization and every repository under it, webhooks, testing and spend limits. The embry0 plugin does it all from the chat window, standing up the deployment too if there is none yet. This page shows the plugin first, then the manual connection, and lists what the session does.

## The plugin

The plugin needs Claude Code 2.1.285 or newer (`claude --version`; `claude update` updates it). In Claude Code:

```text
/plugin install embry0 --marketplace https://github.com/sudoTomas/embry0
```

The same in two steps: `/plugin marketplace add https://github.com/sudoTomas/embry0`, then `/plugin install embry0@embry0`. The plugin lives in [plugins/embry0](../plugins/embry0/README.md); this repository is its marketplace. If you added `embry0` by hand as below, run `claude mcp remove embry0` first. At the same URL, Claude Code skips the plugin's server and keeps the one added by hand; at another URL, both load and every tool shows twice.

When the plugin is enabled, its dialog asks for the deployment's URL (default `http://localhost:8200`; https for any other origin) and an API key. The dialog masks the key, and Claude Code keeps it out of the settings files (in the macOS keychain, elsewhere in `~/.claude/.credentials.json`, mode 600), so it never passes through the chat. With no deployment yet, leave the key empty.

| Skill | What it does |
|---|---|
| `/embry0:install` | Stands up a deployment on the session's machine: the [quick start](../README.md#quick-start), with the secrets written into `.env` and never shown. Then it mints an admin key and fills in the plugin's options. |
| `/embry0:connect` | Checks the plugin's MCP server answers, and says what to fix when it does not. |
| `/embry0:setup` | The setup steps below, the same procedure the MCP prompt `setup` serves. |

The plugin's tools are named `mcp__plugin_embry0_embry0__<tool>`, and its MCP prompt is `/mcp__plugin_embry0_embry0__setup`. To change the URL or the key later: `/plugin configure embry0@embry0`, then check with `/mcp` that the server reconnected (restart Claude Code if it did not, and always after `claude plugin configure` in a terminal). For a new host, see [provisioning](ops/provisioning.md).

## Connect without the plugin

### What you need

- A running deployment you can reach. The MCP endpoint is `/mcp` on the dashboard's origin.
- An API key with the admin tier. Make one on the dashboard under Settings, then API keys, with the scope `admin`, and copy the key. Do not run the `claude mcp add` command the tab shows: it puts the key on a command line.
- Claude Code and `jq`.

### Connect the session

Run these in your own terminal, not inside a Claude Code chat: a key typed in the chat is kept in its transcript. They work in bash and zsh.

First the dashboard origin and the key. At the empty prompt, paste the admin key and press Enter; nothing is shown:

```bash
EMBRY0_URL=https://embry0.example.com
read -rs EMBRY0_API_KEY
```

Then write the key to a file only you can read, and add the server:

```bash
mkdir -p ~/.config/embry0
chmod 700 ~/.config/embry0
[ -n "$EMBRY0_API_KEY" ] && rm -f ~/.config/embry0/api-key && (umask 077; printf '%s' "$EMBRY0_API_KEY" > ~/.config/embry0/api-key)
unset EMBRY0_API_KEY
HELPER="jq -Rs '{Authorization: (\"Bearer \" + .)}' ~/.config/embry0/api-key"
claude mcp remove --scope user embry0 2>/dev/null
claude mcp add-json --scope user embry0 "$(jq -n --arg url "$EMBRY0_URL/mcp" --arg helper "$HELPER" \
  '{type: "http", url: $url, headersHelper: $helper}')"
claude mcp list
```

`claude mcp list` should say embry0 is connected.

The key goes from `read` to the file through `printf`, a shell builtin, and the file is made new (`rm -f`) so the umask gives it mode 600. With no key read (the first block not run in this shell), that line writes nothing and a saved key file stays as it is. No command gets the key as an argument: while a command runs, every local user can read its arguments (`ps`, `/proc/<pid>/cmdline`). Claude Code's settings hold the `headersHelper` command, not the key; Claude Code runs that command each time it connects, and the command prints the header from the file. The key is not exported and is unset, so a Claude Code session started from this shell does not carry it in its environment, where a command the session runs could print it.

The `remove` line drops a user-scope `embry0` entry an earlier connection left; with none, it prints nothing. An entry made by an earlier version of this page kept the key in Claude Code's settings: revoke that key on the dashboard. If that page added embry0 to one project only, its entry hides the new one in that project: run `claude mcp remove --scope local embry0` in that project's folder.

`--scope user` makes the server available in every project. To add it to the current project only, use `--scope local` in place of `--scope user` in both lines; Claude Code then runs the helper only in a folder you trust, so start `claude` there once and accept the trust prompt. When you no longer need the key, revoke it on the dashboard, then `claude mcp remove embry0` and delete `~/.config/embry0/api-key`.

With the package installed (`pip install -e .` in a checkout), the same tools also run over stdio: run this in place of the `claude mcp add-json` line. The server reads the key from the same file each time Claude Code starts it:

```bash
claude mcp add --scope user embry0 --env EMBRY0_API_URL="$EMBRY0_URL" -- \
  sh -c 'EMBRY0_API_KEY=$(cat ~/.config/embry0/api-key); export EMBRY0_API_KEY; exec embry0-mcp'
```

Then start `claude`, check `/mcp` lists embry0 with its tools, and type `/mcp__embry0__setup`. That prompt gives the session the whole procedure below. Say what you want set up, for example "set up the GitHub organization acme".

## Secrets stay out of the chat

No MCP tool takes a secret, by design. Add each credential on the dashboard (Infra, then Credentials), or with the API from your terminal, reading the secret from a file so it is never shown:

```bash
jq -n --rawfile secret ./token.txt \
  '{kind: "forge_token", provider: "github", label: "acme-bot", secret: ($secret | rtrimstr("\n"))}' |
  curl -sS -X POST "$EMBRY0_URL/api/v1/credentials" \
    -H @<(jq -Rr '"Authorization: Bearer " + .' ~/.config/embry0/api-key) -H "Content-Type: application/json" \
    -H "X-Requested-With: XMLHttpRequest" --data-binary @-
```

It reads the key from the file of the manual connection above. With the plugin, write that file first: the first block, then the second block up to `unset`. curl reads the header from a file (`-H @file`), here a pipe that `jq` fills, so neither the key nor the secret is an argument. This needs bash or zsh. The answer carries a masked preview, never the secret. Tell the session the label; it finds the id with `credential_list` and does the binding. [credentials.md](credentials.md) has every kind and field.

| Credential | `kind` | What it needs |
|---|---|---|
| GitHub token | `forge_token` | A fine-grained token on the owner: Contents, Issues, Pull requests and Webhooks read and write; Checks, Commit statuses and Metadata read; for an organization, its Webhooks permission read and write too. Workflows write only if jobs may change workflow files. A classic token needs `repo` and `admin:org_hook`, and `workflow` if jobs may change workflow files. |
| Claude subscription | `llm_oauth` | A token from `claude setup-token`. |
| Anthropic API key | `llm_api_key` | The key, and `DEFAULT_AUTH_MODE=api_key` in the root `.env` (then restart the orchestrator), unless the deployment already uses that mode. |

## The setup steps

The prompt walks through these in order. The session asks you before each change. Most tools that change something also refuse to act without `confirm=true`; two do not: `org_discover`, which only reads the forge and refreshes embry0's facts, and `issue_create`, which starts a paid job when auto triage is on.

| Step | What the session does | Tools |
|---|---|---|
| 1. Connection | Checks the deployment answers and the key is admin | `health` |
| 2. Model access | Reads which provider has an account, binds a Claude account at global scope | `provider_status`, `credential_list`, `credential_bindings_get`, `credential_bindings_set` |
| 3. Forge access | Asks the forge what the token can do, binds it to the owner | `org_probe`, `credential_bindings_get`, `credential_bindings_set` |
| 4. The owner | Registers it, reads every repository, lists what is missing | `org_register`, `org_discover`, `org_runs`, `org_needs` |
| 5. The needs | Registers the repositories, installs one organization webhook, sets up testing per repository | `org_act`, `org_webhook_install`, `repo_skip_testing`, `job_list`, `job_wait` |
| 6. Spend limits | Reads the per-job ceiling and the daily and monthly caps, changes them on your word | `settings_effective`, `settings_set_keys` |
| 7. Check | Resolves both accounts for one repository, rediscovers, optionally runs a first job | `credential_resolve`, `org_discover`, `org_needs`, `issue_create`, `job_wait` |

Testing is where setup spends money. The onboard action of `org_act` starts one agent job per repository, which reads the code and writes its test setup. `repo_skip_testing` stores a setup with testing off for a repository at no cost, and a person can start onboarding for it later from its page. The session asks you which repositories get which.

## Settings that are not tools

Some settings live in the root `.env` on the host and take a restart, so no MCP tool changes them:

- `WEBHOOK_PUBLIC_URL` and `GITHUB_WEBHOOK_SECRET`, without which no webhook is installed ([webhooks](webhooks.md)).
- `DEFAULT_AUTH_MODE`, for a deployment whose Claude account is an API key.
- The tunnels and what each one exposes ([deploy](ops/deploy.md)).

A session opened in the deploy checkout on the host can make these changes with you. [configuration.md](configuration.md) lists every variable.

## In an embry0 checkout

A Claude Code session opened in a checkout of this repository has the skill `setup-embry0` (`.claude/skills/setup-embry0`). It checks whether the stack runs and the MCP server is connected, through the plugin's tools or a server added by hand, and then follows the same procedure. With the plugin, it sends a missing connection to `/embry0:connect` and a missing stack to `/embry0:install`.
