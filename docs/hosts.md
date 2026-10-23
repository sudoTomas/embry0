# Hosts

A host is a machine that runs code embry0 maintains. A repository says what the code is; a host says where it runs, how embry0 reaches it, and where its logs come from. Hosts are their own record because the facts a maintainer needs after a merge (the logs, whether the deploy landed) live on machines, not in repositories. See [ADR 0013](adr/0013-hosts.md) for the decision.

This release stores hosts, links them to repositories, checks whether each is reachable, and imports them from a tailnet. Nothing reads a host's logs or deploy markers yet; the record is what later work builds on. The dashboard's **Hosts** page (Infra) lists them with the same filters, and adds, edits, probes, removes and imports them through these routes.

## What a host is

A row in `hosts`:

| field | meaning |
|---|---|
| `id` | `host-` and 12 hex digits, minted by embry0 |
| `name` | unique, ignoring case: letters, digits, `.`, `_` and `-` |
| `address` | how to reach it: an address or MagicDNS name for `tailnet`, `host` or `host:port` for `ssh`, `host`, `host:port` or a URL for `http` |
| `transport` | `tailnet`, `ssh` or `http` |
| `roles` | what it does, free words such as `app`, `db`, `worker` |
| `log_source` | where its logs come from: `{"kind": "journald", "selector": "<unit>"}`, `{"kind": "docker", "selector": "<container>"}` or `{"kind": "loki", "selector": "<LogQL selector>"}`, or null |
| `tags` | free labels; an import copies the device's tailnet tags |
| `source` | `manual` (added through the API) or `tailnet` (made by an import) |
| `status` | `active`, `unreachable` or `removed` |
| `last_seen_at` | when it was last seen reachable, by a probe or by the tailnet |

Removing a host keeps its row with `status` `removed` and drops its repository links, so a later import does not bring it back. `PATCH` with `{"status": "active"}` restores it, and so does `POST /hosts` with the same name.

## Repositories a host serves

`repo_hosts` links a repository to a host with a `role` (default `app`). Send `repos` on `POST /hosts`, or on `PATCH /hosts/{id}` to replace the host's links: `[{"repo": "acme/widgets", "role": "app"}]`. A repository path is stored lowercase, the form credential bindings use, and need not be registered.

`GET /api/v1/repos/{repo}` lists the repository's hosts under `hosts`, each with its `role` for that repository. A removed host is not listed.

## The probe

`POST /api/v1/hosts/{id}/probe` asks whether the host is reachable over its transport, and writes the answer to the host's `status` (`active` or `unreachable`) and `last_seen_at`. It changes nothing a person configured.

- **tailnet**: embry0 lists the tailnet's devices (below) and looks for the host by address first (any of the device's tailnet addresses, or its MagicDNS name), then by name (the MagicDNS short name, the full name, or the device's own hostname). The host is reachable when the tailnet reports the device connected: `connectedToControl` when the API sends it, otherwise a `lastSeen` inside `ONLINE_WINDOW` (`embry0/services/hosts.py`). No matching device is unreachable.
- **ssh** and **http**: a TCP connect to the address's port, closed at once with nothing sent, within `PROBE_TIMEOUT_SECONDS`. The default port is 22 for `ssh`, 80 for `http` and 443 for an `https://` URL.

The response is `{"probe": {...}, "host": {...}}`. `probe` has `reachable`, `detail` (a sentence naming the device or the endpoint and, on failure, the exception type), `checked_at`, `last_seen_at` and, for a tailnet host, the matched `device`. A probe that cannot run says nothing about the host and leaves it alone: 422 when a tailnet host has no tailnet credential or an `ssh` or `http` address has no endpoint, 502 when the tailnet API refuses or cannot be reached, 409 for a removed host. A `credential_id` in the body that names an unusable key is also a 409: a credential of another kind, one that is `disabled` or `quarantined`, or one whose secret cannot be decrypted, which embry0 then marks `disabled` with `status_reason=decryption_failed` until `POST /api/v1/credentials/{id}/rotate` re-enters it. Without `credential_id`, a bound key in any of those states is skipped, and the answer is the 422 for no tailnet credential, its detail naming why the key was skipped.

## The tailnet credential

A tailnet is read with a credential of kind `tailnet_api_key` in the [credential registry](credentials.md), provider `tailscale`, host `api.tailscale.com`. Its `metadata` holds the tailnet it reads: `{"tailnet": "-"}` (the default) is the key's own tailnet; otherwise the tailnet's name as the admin console shows it, such as `example.com`. Create it, then bind it at global scope with role `tailnet`, the only scope that role takes.

`PUT /api/v1/credentials/bindings` replaces the whole set of bindings for the scope it names: every global binding left out of the body, such as a key in the global LLM pool, is deleted. So read the global bindings first and send them back with the tailnet one. The global scope holds one `tailnet` binding, so the recipe also drops an earlier one:

```bash
curl -X POST http://localhost:8200/api/v1/credentials \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"kind": "tailnet_api_key", "provider": "tailscale", "label": "tailnet",
       "secret": "tskey-client-...", "metadata": {"tailnet": "-"}}'

curl -s "http://localhost:8200/api/v1/credentials/bindings?scope_kind=global&scope_value=" \
  -H "Authorization: Bearer $API_KEY" \
  | jq '{scope_kind: "global", scope_value: "",
         bindings: ([.bindings[] | select(.role != "tailnet") | {credential_id, role, pinned, priority}]
                    + [{credential_id: "cred-0123456789ab", role: "tailnet"}])}' > global-bindings.json

curl -X PUT http://localhost:8200/api/v1/credentials/bindings \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d @global-bindings.json
```

**The scope the key needs.** embry0 reads one thing with it, `GET https://api.tailscale.com/api/v2/tailnet/{tailnet}/devices`, so the key needs to read the tailnet's devices and nothing more. Either of two secrets works:

- An OAuth client secret (`tskey-client-...`) from the Trust credentials page of the tailnet's admin console, with the scope `devices:core:read` alone. This is the read-only choice. Before each read, embry0 exchanges the secret at `POST https://api.tailscale.com/api/v2/oauth/token` for an access token that lives an hour and carries only that scope, so it stores no token and needs no refresh flow. The client does not expire; revoke it in the admin console. Tailscale answers 404 to a read outside a client's scopes, so a client without `devices:core:read` shows up as the tailnet not found.
- An API access token (`tskey-api-...`) from the Keys page. It acts with every permission of the user who created it, so create it as a user who can see the devices, and rotate it with `POST /api/v1/credentials/{id}/rotate` before the expiry set at creation.

The key is treated like every other registry secret: encrypted at rest, never returned (responses carry `masked_preview`), never logged, and added to the log scrubbers' set when created or rotated. A tailnet API failure is reported by status code or exception type only, never by the text of the request. The probe and the import take an optional `credential_id` to read with another `tailnet_api_key` than the bound one.

## Importing from a tailnet

`POST /api/v1/hosts/import/tailnet` with `{"confirm": true}` (and, optionally, `credential_id`) creates or updates one host per device:

- a device with no host of its name becomes a `tailnet` host named after its MagicDNS short name, with its first IPv4 tailnet address, its tags, `last_seen_at` from the tailnet, and `status` `active` when it is connected, `unreachable` otherwise;
- a host an earlier import made gets its address, status and `last_seen_at` refreshed, and nothing else: roles, tags, log source and repositories are whoever set them;
- a manual host with the same name, or a host a person removed, is skipped.

The answer is `{"tailnet", "credential_id", "results": [{"name", "host_id", "outcome", "detail"}], "counts"}` with outcomes `created`, `updated`, `unchanged` and `skipped`. The import is idempotent: a second identical import reports every host `unchanged` and writes nothing. A device that leaves the tailnet is not removed; remove its host by hand. Without `confirm: true` the route answers 422 and reads nothing. A missing or unusable tailnet key answers as it does for the probe: 422, or 409 for a `credential_id` that names an unusable key.

## API

All under `/api/v1`. Reads need any authenticated principal and a probe needs an operator; creating, changing, removing and importing need the `admin` tier and write an audit row. Mutations carry the `X-Requested-With` header like every other.

| method and path | tier | purpose |
|---|---|---|
| `GET /hosts` | read | `{"hosts", "transports", "sources", "statuses", "log_source_kinds"}`. `?source=manual` or `?source=tailnet` filters; `?include_removed=true` shows removed hosts |
| `POST /hosts` | admin | create: `name`, `address`, `transport`, `roles`, `log_source`, `tags`, `repos`. 409 when a host that is not removed has the name |
| `GET /hosts/{id}` | read | one host with its `repos` |
| `PATCH /hosts/{id}` | admin | any of the create fields and `status` (`active` or `removed`); an omitted field is kept, `log_source: null` clears it, `repos` replaces the links |
| `DELETE /hosts/{id}` | admin | mark removed and drop the links (204) |
| `POST /hosts/{id}/probe` | operator | the probe above; body `{"credential_id"}` optional |
| `POST /hosts/import/tailnet` | admin | the import above; body `{"confirm": true, "credential_id"}` |

Audit actions: `host.created`, `host.updated` (with the field names), `host.removed` and `host.imported` (with the tailnet, the credential id and the counts). A probe writes none.

## MCP

| tool | effect |
|---|---|
| `host_list(source=None, include_removed=False)` | the hosts, with their repositories |
| `host_get(host_id)` | one host |
| `host_probe(host_id, credential_id="")` | the probe, reading a tailnet with `credential_id` or the bound key; writes status and last seen |
| `host_import_tailnet(credential_id="", confirm=False)` | the import; without `confirm=true` it returns `confirmation_required` and changes nothing |

None of them returns a key.
