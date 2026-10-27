# Knowledge maps: a scanner's map in embry0, marked and sent to agents

## Why

Teams already map their codebases outside embry0: a scanner reads the code, the git history and production measurements, lays the parts out on a plane and scores each one for how likely it is not needed. People read the map and decide: this script is dead, this table is a leftover, this module should stay. Then the decision has to travel to an agent, and today it travels by hand: a ticket written from the map, then a job started from the ticket. Two systems, a form in between, and the agent is usually asked to *analyse* a part a person has already decided to remove, which spends money confirming the decision.

embry0 is where agent work starts, is watched and is reviewed. So the map belongs in embry0 too: the same parts, the same evidence, and a mark that is the decision, one click from a pull request.

## Decisions

1. **embry0 shows maps; it does not scan.** Scanners differ by stack and by what they can measure (a production database, a metrics server, a process list). embry0 defines one upload format, `embry0.map/1`, and any scanner that writes it is a source. A scanner built into embry0, for repositories nobody maps yet, is a later piece of work; the format is what it will write.
2. **The format carries what a viewer and an agent need, nothing else**: parts with a place on the plane, links, kinds and areas for the legend, and one detail record per part (what it does, why it exists, the bloat reasons with their weights, use, history). The scanner's own fields stay with the scanner. Field reference: [maps.md](../../maps.md).
3. **A map is its last upload.** Marks and sends are kept by part id, so they outlive uploads; a scanner keeps ids stable across scans. A part that leaves the map closes its lead: after a removal merges, the next scan no longer sees the part, and its lead goes with it.
4. **A bloat mark is a lead.** A part someone marks bloat that lives in a repository becomes a `weed` lead on it (`source_key` `map:<slug>:<part id>`, `data.source = "map"`). It is offered to the repository's owners like every lead, shows on the quest board, and is answered through the owner proposals. No new lead kind: a weed is already "something to pull out that changes no behavior". When nobody marks the part bloat any more, its lead closes, one lead at a time (`LeadsRepository.close`): the bulk `mark_gone` would close every other weed of the repository.
5. **Sending a removal is the decision.** `remove` records the sender's bloat mark, then accepts the lead through the same start as an owner's yes, so the job, the proposal decision and its audit row are what every accepted lead gets. The task says the mark is the decision: delete the part and what only it uses, drop its mentions, run the tests, open a pull request, do not re-assess; make no change only if something that stays still uses it. It changes code only, never a database or a live system. A removal sent again after its proposal was answered starts a new job on the same lead and records no second yes.
6. **Fix and analysis are direct.** A fix becomes an embry0 issue (labels `embry0-map`, `map:<slug>`) dispatched like any issue; an analysis is a read-only `analysis-default` job. Neither is a lead: nobody decided anything yet.
7. **The default follows the sender's mark.** A send without a kind removes a part its sender marked bloat and analyses anything else. "Send my bloat marks" sends every part the viewer marked bloat that was never sent for removal, or whose removal failed, in batches of 50.
8. **Every send is recorded** with the job it started or the reason it did not start, and the part's work shows the newest one joined to its job: running, a pull request, merged, failed, and its cost. This is what a scanner's own viewer polls to show results on its map.
9. **Run with on every send**, checked once per repository of the batch, as on every route that starts agent work.
10. **Uploads ride the LAN.** A large map is several MB gzipped, past nginx's 1 MB default. The LAN vhost takes 32 MB on `/api/v1/maps/`; the public vhost keeps the default. The orchestrator caps the decompressed document at 64 MB and refuses a bigger one rather than cutting it.
11. **Who may do what.** Reads are any key or session. Uploads, marks and sends are mutations: an `operate` key or an operator session. A key with a repository allowlist uploads only maps of repositories on its list (never an organization map) and marks and sends only parts in them. The page offers sending to an admin, as the world does; a mark is open to every operator.

## Data

Migration 124:

- `knowledge_maps`: one row per slug: the header fields, counts, `graph` (the parts and links as JSON **text**, served as stored with an ETag per upload, so a map of thousands of parts is never decoded and re-encoded per request) and `details` (JSONB, read one part at a time with `details -> id`).
- `knowledge_map_marks`: `(map_slug, node_id, marked_by)` → verdict, note, time. Clearing a mark deletes its row; the same mark again keeps its time.
- `knowledge_map_sends`: every send, with kind, repository, lead, job or error, who and when. The work view is `DISTINCT ON (node_id)` joined to `jobs`.

## The page

`/maps` lists the maps; `/maps/<slug>` draws one on a canvas at the scanner's layout (a canvas, not a node graph library: thousands of parts and links stay smooth), coloured by bloat, marks, agent work, kind or area, with search and filters. The open part (`?part=`) shows the evidence, everyone's marks, the newest work and the send controls with Run with. Nothing moves by itself; the page is embry0's usual interface.

## Not in this version

- A scanner inside embry0 (the format is its contract).
- Mark history: a mark is each person's current verdict; changes go to the log.
- Mapping a scanner's areas onto a repository's `.embry0/areas.yml`: a lead takes its area from its file's path, as every lead does.
