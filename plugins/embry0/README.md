# embry0 plugin for Claude Code

Stand up embry0, connect Claude Code to it over MCP, and set it up, all from the chat window.

## Install

Needs Claude Code 2.1.285 or newer (`claude --version`; `claude update` updates it). In Claude Code:

```text
/plugin install embry0 --marketplace https://github.com/sudoTomas/embry0
```

The same in two steps: `/plugin marketplace add https://github.com/sudoTomas/embry0`, then `/plugin install embry0@embry0`.

If you added an embry0 MCP server by hand before (`claude mcp add`), remove it first with `claude mcp remove <its name>`. At the same URL, Claude Code skips the plugin's server and keeps yours, with its own key; at another URL, both load and every tool shows twice.

When the plugin is enabled, its dialog asks for two options:

| Option | What it is |
|---|---|
| `url` | The deployment's dashboard origin, without a trailing slash. Default `http://localhost:8200`, a deployment on the same machine. Use https for any other origin: the key travels in a header. |
| `api_key` | An API key from the dashboard (Settings, then API keys): scope admin to set the deployment up, operate or read to use it. A sensitive option: the dialog masks it, and Claude Code keeps it out of the settings files, in the macOS keychain or elsewhere in `~/.claude/.credentials.json` (mode 600). Leave it empty if you have no deployment yet. |

Change them later with `/plugin configure embry0@embry0`, then check with `/mcp` that the server reconnected; restart Claude Code if it did not. After `claude plugin configure` in a terminal, restart Claude Code.

## Use

| Skill | What it does |
|---|---|
| `/embry0:install` | Stands up a deployment on this machine with Docker: clone, a `.env` with generated secrets, build and start, then mints an admin key and fills in both options. |
| `/embry0:connect` | Checks the MCP server answers and says what to fix when it does not. |
| `/embry0:setup` | Sets a deployment up with you: model access, a forge credential, an organization and its repositories, webhooks, testing and spend limits. |

The embry0 tools also answer plain requests, such as "what is in the queue" or "start a job for this issue". The guide is [docs/claude-code.md](../../docs/claude-code.md).

## Change the plugin

Bump `version` in `.claude-plugin/plugin.json` and in the repository's `.claude-plugin/marketplace.json` together, with every change to the plugin: an installed copy is updated by version, and a unit test checks that the two agree.

## Privacy

The plugin's MCP server talks only to the embry0 deployment you name in `url`, with the key you give it. The `install` skill downloads embry0's source, its Python packages and its base images, and sends nothing about you or your deployment. No skill asks for a secret in the chat: credentials go in on the deployment's dashboard, or through the plugin dialog.
