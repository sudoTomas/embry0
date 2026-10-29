# Knowledge maps: a codebase's parts, marked and sent to agents

A knowledge map is a scanner's picture of a codebase, or of every system an organization runs: its parts (modules, files, scripts, tables, services, jobs, ...), the links between them, and what each part costs to keep. embry0 shows the map on its **Maps** page. People mark parts **bloat**, **keep** or **not sure**, and a part marked bloat can go to an agent that removes it in a pull request. The design is in [the spec](superpowers/specs/2026-10-27-knowledge-maps-design.md).

embry0 does not scan. A scanner outside it reads the codebase however it likes (the code, git history, production measurements) and uploads the result in the format below. Any tool that writes the format works.

## The format: `embry0.map/1`

One JSON document per map:

| Field | What it holds |
|---|---|
| `format` | `"embry0.map/1"` |
| `slug` | The map's name in URLs: lowercase letters, digits and hyphens, at most 64. Optional; it must match the upload's path when present. |
| `title` | What the page calls the map. |
| `owner` | The forge owner (organization, account or group) the map belongs to. Optional: absent, it is the owner of `repo`. When both are given they must agree. See [Who owns a map](#who-owns-a-map). |
| `repo` | `owner/name` of the repository the map describes, or `null` for an organization-wide map. |
| `commit`, `generated_at`, `source` | The commit read, when (ISO 8601), and what produced it, as shown on the page. |
| `kinds` | `[{"id", "label"}]`: the kinds of part, in the order a legend lists them. |
| `areas` | `[{"id", "label", "summary", "x", "y", "r"}]`: groups of parts, drawn as labelled rings. |
| `nodes` | The parts, below. At most 20,000. |
| `edges` | `[{"s", "t", "rel"}]`: a link from part `s` to part `t` (`rel` says how: `calls`, `reads`, `runs`, ...). At most 100,000; a link to a part the map does not have is dropped. |
| `details` | `{"<part id>": {...}}`: one record per part, read when the part is opened (below). |

A part (`nodes[]`):

| Field | What it holds |
|---|---|
| `id` | Unique in the map, at most 300 characters. Keep it stable across scans: marks and sends are kept by it. |
| `kind`, `area`, `label`, `sub` | Its kind and area ids, its name, and a short line under the name. |
| `path`, `line`, `url` | Its file in the repository, a line in it, and an `https://` link to it. |
| `repo` | `owner/name` when the part lives in a repository other than the map's (an organization map names one per system). It must be one of the map owner's repositories. Otherwise a part with a `path` lives in the map's `repo`, and a part with neither (a database table, a host) lives in none. |
| `x`, `y`, `r` | Its place and size on the plane, in any units; the page fits the whole map to the screen. A part without a place is put on a spiral. |
| `signals` | Up to 8 short sentences that call for attention. |
| `removal` | For a part that is not a file but can be removed by a code change, how (at most 600 characters): a database table the repository's migrations own is dropped by a new migration. Such a part lives in the map's `repo` and can be sent for removal. |
| `bloat` | `{"score": 0-100, "band": "keep" \| "look" \| "bloat"}`: the scanner's reading of how likely the part is not needed. |
| `age`, `use` | `{"first", "last", "changes", "authors"}` and `{"level": 0-3, "n", "unit"}`, shown as given. |

A detail record (`details["<id>"]`), every field optional:

- `narrative`: `{"does", "why", "useful", "against", "lean", "sure"}`, what the part does, why it exists, the case for keeping it and against, and a reading (`lean`) with a confidence (`sure`).
- `bloat`: `{"score", "band", "against": [{"text", "w"}], "for": [{"text", "w"}], "risks": ["..."]}`, the reasons behind the score, each with its weight, and what needs a person rather than a removal.
- `use`: `[{"label", "n", "unit", "text", "window", "source"}]`, how much it is used, and where that was measured.
- `history`: `{"added": {"date", "author", "subject", "pr"}, "last": {"date"}, "changes", "prs": [{"number", "title", "url"}], "tickets": ["..."]}`.

## Uploading

```bash
gzip -c map.json | curl -sf -X PUT "http://<embry0-host>:8200/api/v1/maps/<slug>" \
  -H "Authorization: Bearer $EMBRY0_KEY" -H "Content-Type: application/json" -H "Content-Encoding: gzip" \
  -H "X-Requested-With: XMLHttpRequest" --data-binary @-
```

- **Gzip or plain JSON**: send `Content-Encoding: gzip` for a compressed body. The decompressed document may be up to 64 MB; a bigger one is refused with 413.
- **Over the LAN**: the LAN vhost takes a body of up to 32 MB on `/api/v1/maps/`. The public dashboard vhost keeps nginx's 1 MB default, so a scanner uploads over the LAN.
- **The key**: an `operate` key does it, for a map whose owner the key covers ([Who owns a map](#who-owns-a-map)). A key limited to a repository allowlist may also upload only a map whose `repo` is on its list, never an organization map, whose parts name only repositories on its list, and replace only a stored map whose `repo` is on its list; it marks only parts of repositories on it ([API keys](api.md#api-keys)). A map that names neither an `owner` nor a `repo` belongs to no one, and only an admin may upload it.
- **Valid JSON only**: `NaN`, `Infinity`, a number too large for a double, a NUL character in a string and a lone UTF-16 surrogate are refused with 422.
- **What an upload keeps**: embry0 keeps the last upload per owner and slug. Marks and sends are kept by part id across uploads. A part marked bloat that is still on the map has its lead refreshed with the new evidence; a part that left the map closes its lead, which is what a merged removal looks like on the next scan. An upload closes only its own map's leads, and only in repositories the uploading key may act on.

## Who owns a map

Every map belongs to a forge owner: the `owner` its document names, else the owner part of its `repo`, lowercased (`acme` for `Acme/widgets`). An organization-wide map (`repo: null`) names its `owner`; an upload can also give it with `?owner=` on the address when the document cannot. A map holds only its owner's repositories: a part that names a repository of another owner is refused with 422. A slug names a map within its owner, so two owners can each have a map called `shop`.

Who sees and changes a map follows the caller's [repository allowlist](api.md#repository-allowlist). A key covers the owners of the repositories on its list (with `REPO_RENAMES`, an old and a current owner alike); a key or a person without a list covers every owner. A caller lists and reads only the maps of owners it covers, and any other map is not there for it (404). Uploading, marking, sending, deleting or giving away a map of an owner it does not cover is refused with 403 `repo_not_allowed`, naming the owner as `<owner>/*`, logged and audited as a refused repository is. Replacing, deleting or giving away a stored map is also checked against the map's own `repo`, so a key limited to some of an owner's repositories changes only the maps of those.

A map that belongs to no one (no `owner`, no `repo`) is an admin's alone: only an admin without an allowlist sees it, and only an admin uploads, marks, sends or deletes it (anyone else gets 403 `map_has_no_owner`). On the upgrade that brings owners, each stored map gets its repository's owner, and an organization map gets none and waits for an admin. An admin gives a map an owner on its page (**Set owner**) or with `PUT /api/v1/maps/{slug}/owner`; the map takes its marks, its sends and its leads (and their links) with it, and the change writes a `map.owner_changed` audit row. The upload's rules hold: a map of a repository stays with that repository's owner, and a map whose parts name another owner's repositories cannot move to an owner they are not under.

A weed lead is keyed by repository, slug and part id, so an unowned map and an owned map of one slug can name the same part's lead. The map that opened it keeps it: another map of the slug does not show, close or rewrite it, and a removal of that part from the other map is refused, in a batch too. The refusal names the holding map only to a caller who may see it; anyone else is told an admin must release the lead.

**Addressing a map.** Every route takes `?owner=<owner>` (empty, `?owner=`, for the map of no owner). Without it, an address names the one map of the slug the caller sees, so the addresses from before owners keep working; when the caller sees several, it answers 409 and names their owners, and when it sees none, 404, whatever other owners have. The page links carry the owner, so a link keeps opening its map after another owner uploads one of the same slug.

## The Maps page

`/maps` lists every map the viewer may see, with its owner. A map's page draws its parts on the scanner's layout: drag to move, the wheel or `+` and `-` to zoom, `0` to fit, a click to open a part. Colour the parts by bloat score, by marks, by agent work, by kind or by area; find parts by name, id or path; pick out the bloat candidates, the parts marked, or the parts sent. The address keeps the open part (`?part=<id>`), so a link opens it.

An open part shows what the map knows about it (its signals, bloat score and reasons, what it does, use and history), everyone's marks, and its agent work: the newest job sent for it, running, its pull request open, merged or failed, with its cost.

## Marks become leads

A mark is one person's verdict on a part: **bloat**, **keep** or **not sure**, with a note. Pressing your own verdict again takes it back.

A part an agent can remove (a file or folder in a repository, or a part with a `removal` note) and that someone marks bloat becomes a [`weed` lead](leads.md) on that repository, titled **Remove &lt;part&gt;**, with the map's evidence as its details and a link back to the part. It is offered to the repository's owners like every lead, shows on the quest board, and is answered through the [owner proposals](api.md#owner-proposals). When nobody marks it bloat any more, the lead closes. A part with neither (a whole system or repository on an organization map, a host, a table no code owns) keeps its marks on the map only: retiring it is not a code change.

## Sending parts to agents

From a part, an admin picks what the agent does and its [Run with](api.md#run-with):

- **Remove** (a part an agent can remove only): accepts the part's weed lead, recorded as that person's yes. Sending a removal is the decision, so the sender's bloat mark is recorded first. The agent deletes the part and everything only it uses, drops every mention of it, runs the tests and opens a pull request; it does not re-assess whether to keep it, and it changes code only, never a database or a live system. A removal sent again after its proposal was answered starts a new job on the same lead.
- **Fix**: an issue in embry0 labelled `embry0-map` and `map:<slug>`, dispatched like any issue: the smallest fix the evidence calls for, in a pull request.
- **Analysis**: a read-only job on the `analysis-default` template: what the part does, whether the evidence holds, what depends on it, and the change it recommends as a diff, not applied.

The part's kind is picked from your mark: bloat sends a removal when the part can be removed, anything else an analysis. A whole system or repository can still go for a fix or an analysis. **Send my bloat marks** sends every part you marked bloat that was never sent for removal, or whose removal failed, as one batch of removals, 50 parts per request.

**Many at once.** Search or filter the map, then **Send N picked out**; or **Send my bloat marks**. Pick what the agents do, untick any part, and send. By default the parts become **one job per repository** (one pull request): a hundred backup tables are one migration, not a hundred pull requests whose migration numbers clash. Each part's lead is accepted with that one job, and each part's agent work shows it. A part that cannot go for the chosen kind is listed with the reason and left out. A batch carries at most 200 parts; a very long one lists each part more briefly, and one that still does not fit one job's task is refused, never cut.

Every send is recorded with the job it started or the reason it did not start (a part in no repository, the budget gate, a key the repository may not use), and the part's agent work shows it.

## API

The routes are in [the API reference](api.md#knowledge-maps).
