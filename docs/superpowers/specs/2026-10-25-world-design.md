# The world: embry0 as a game you play to make your code better

**Goal.** Today embry0 works out of sight: jobs start, finish, and a person finds out later from a list. The world makes the work visible and playable. Open embry0 and you stand in a living map of your codebases: the potential work embry0 found stands in it as creatures and quest givers, your agents are characters who wait in their guild hall, you send them out, you watch them travel, work and fight, and they come back with what they found: a pull request as loot, experience as it lands, a level as it is reached. An AFK switch lets them go out on their own.

The world becomes the home screen. Every list and setting stays one click away; nothing the world shows is new state, only a new way to see and act on what embry0 already holds.

**What matters most** (the user, the same day): "that I can actually interact with the embry0 dashboard as if it were my place where I'm managing my characters and I'm sending them on quests and I'm seeing their results and I'm evaluating their work and I'm making sure they're doing it." So phase 1 is that management loop, end to end, in this order of importance; the map's spectacle comes second:

1. **Manage the characters.** The guild hall is the management screen: each character's level, earned gear, best skills, what it is doing now, and its quest log (every job it ran in: the quest, the place, the outcome, the pull request, its share of the cost and time, the player's rating), with its totals (quests, how many it won, average cost and time, ratings).
2. **Send them on quests.** The quest board (the owner proposals now; the leads later), filtered by region, area, kind and level; the party's leader and the Run with choice; the verb button is the proposal's accept, recorded as the signed-in player. **Write a quest**: a task the player writes for one character on one repository (a job on that character's template).
3. **See their results.** A quest that comes back opens as its scroll: "Quest complete!" or "Quest failed", what it did (its report), the loot (the pull request with files and lines changed, the checks' verdict, the area), the experience each character earned by skill, the cost and the time; a failure says why in plain words.
4. **Evaluate their work.** On the scroll: **Merge** (through the quest's merge approval when it has one), **Send back with notes** (a follow-up job on the pull request's own branch, the same character's session resumed, the notes as its task), **Reject** (optionally closing the pull request with the note), and a **rating** (good, needs work, bad, with a note). Ratings show in the character's quest log and totals; they feed no experience yet.
5. **Make sure they are doing it.** Click a character that is out to watch its party live (the step, the tool calls, the last words), answer its question in its speech bubble, or Recall it; a party silent for 20 minutes is flagged stuck, and a character with no quest in two weeks is flagged idle.

The **inbox** of quests that came back and wait for a judgement sits at the top of the screen, like loot not yet picked up: that is how the player finds out, instead of it just happening.

**Non-goals.** No multiplayer, no chat between people, no new kind of job. Nothing in the world changes code by itself: a dispatch is an owner proposal's accept (spec 2026-10-24) with its Run with choice (spec 2026-10-25), and AFK mode is that same accept, made by a rule.

## Places

**The overworld.** One top-down tile map at a 32 px base tile, pannable and zoomable, at phone width too.
- Each registered repository with owners, a watched stack, or jobs in the last 90 days is a **region**; the rest of the organization's repositories are unexplored land at the map's edge, named but fogged.
- Regions are laid out once from the repository list (deterministic: the same repositories give the same map), sized by recent activity, joined by paths to the guild hall at the centre.
- A monorepo's areas (`.embry0/areas.yml`) are **towns** inside its region, each a small cluster with a signpost bearing the area's name.

**The guild hall.** The home base, a building on the overworld; entering it shows its interior.
- One station per character: the developer's anvil, the reviewer's lectern, QA's alchemy table, the analyst's map table, the specialists' desks.
- An idle character stands at its station, working animation off; a busy one's station is empty.
- The **quest board** pins the owner proposals waiting for an answer. The **loot chest** holds the pull requests brought back in the last days, merged ones as gold.

## Things in the world

Every lead (today the owner proposals: watcher cases, open issues, context-audit findings; soon the `leads` table's backlog tickets and finders) stands at a deterministic spot (a hash of its id) inside its region, or inside its area's town.

| Lead kind | In the world | Interaction |
|---|---|---|
| watcher case (a production error that keeps happening) | a creature, more menacing with severity and occurrences | Attack |
| failing CI on the default branch | a stone golem blocking the region's road | Attack |
| security or dependency alert | a wyrm in its lair | Attack |
| flaky test | a blinking imp | Attack |
| unanswered review comment | a ghost with a question mark | Talk |
| backlog ticket, open issue | a quest giver with a `!` | Start quest |
| context-audit claim | a crumbling signpost | Repair |

Each lead has a **level** (1–126, like a character's): its difficulty estimate, from severity, occurrences and the lead's effort guess. A lead nobody answers grows no older; one answered "no" leaves the world; a snoozed one sleeps (greyed, a `z`).

## Characters

The agent types are the characters, drawn by the roster's composed sprites wearing what they earned (spec 2026-10-24). Their walking and working frames are generated from the same parts in code, so the gear a character wears in the world is always the gear it has earned.

A character is in one of these states, read from the jobs it is on:

| State | From | On screen |
|---|---|---|
| idle | no live job | at its station in the hall |
| travelling | the job started, its first agent has not | walks the path from the hall to the lead |
| working | an agent run of the job is live | the work animation at the lead; tool calls tick; a failing check shows a hitsplat |
| waiting for you | the job is `awaiting_input` | a speech bubble: the question, click to answer |
| returning | the job finished | walks home carrying its loot |
| defeated | the job failed | falls, respawns at the hall; the chatbox says why |

A job's agents travel as a party: the lead agent first, the others behind it. A job that came from no lead (a label on a ticket, a schedule) walks to its region's centre.

## Feedback

- **XP drops**: the skill's icon and the amount float up over the character as its experience lands.
- **Level up**: fireworks over the character, a chatbox line ("You have advanced a Debugging level: 43").
- **Achievements**: an unlock banner, the diary entry one click away.
- **Loot**: a returning character drops its pull request into the chest; merged, it turns to gold.
- **The chatbox** narrates every change in plain words with the place: "Your developer sets off for a Null reference (level 32) in acme/web · Billing."

## Acting

- **Click a lead**: its panel says what it is, where, the evidence, what sending someone starts, and roughly what it costs; it picks the party and has the Run with control. The button reads Attack, Talk, Start quest or Repair.
- **Right-click a lead** (long-press on a phone): Attack, Examine, Snooze, Not this (with a reason): the owner proposal's answers.
- **Click a character**: its sheet, what it is doing and where, and Recall (cancel its job).
- A dispatch is `POST /proposals/{id}/accept` with `run_with`, recorded as the signed-in person; a person who may not answer sees the panel without its buttons.

## AFK mode (phase 3)

Per region, off by default: a switch on the region's banner lets idle characters take leads there by themselves, within limits the region shows (kinds, highest level, jobs per day) and within the deployment's budget. An AFK dispatch is the same accept, made by a fixed service actor and marked AFK in the chatbox and the audit log.

## Data (as built)

- `GET /api/v1/world` (read): `characters` (each with `state`, the `job_id` it is drawn at, `quests_out`, `stats`, `idle_too_long`, and whether the player can write it a quest), `parties` (the jobs out now and those back in the last three minutes: leader, members, the one working, the lead it came from, `stuck`, a defeat's reason), `quests` (the board, each with `level`, `verb`, `creature`, `leader`), `inbox` (finished quests a person sent, or that brought back a pull request, with no evaluation yet, newest 14 days), `loot` (pull requests of the last week), `recent` (progression's levels and unlocks), `regions` and `frontier`. The quest board, the regions and the totals are kept a minute; the parties are read on every call.
- `GET /api/v1/world/characters/{agent_type}`: the character, its totals and its quest log.
- `GET /api/v1/world/quests/{job_id}`: the scroll.
- `PUT /api/v1/world/quests/{job_id}/evaluation`, `POST .../send-back`, `POST .../reject`: the judgement, stored in `job_ratings` (migration 112, one row per job) with an audit row (`quest.evaluated`, `quest.sent_back`, `quest.rejected`).
- A party's lead is the owner proposal whose accept started its job (`proposal_decisions.job_id`); no new column.
- Who a job is drawn as: the agent working now, else the last that worked other than triage, else its template's agent; the inbox and the scroll draw a finished quest as the agent whose runs cost the most.

## Data (as first planned)

- `GET /api/v1/world` (read): `regions` (repository, areas, explored, recent activity, layout seed), `leads` (id, kind, repository, area, title, level, interaction, state, url), `characters` (agent type, state, job id, lead id, repository, since), and `events` since a cursor (loot, XP, levels, achievements) for the effects.
- A job dispatched from a lead records the proposal id (`pipeline_config.lead_id`), so its party walks to that lead.
- Live: the client reads the world every few seconds and, for each live job, follows its event stream (`/ws/jobs/{id}/events`) for the fine motion: agent starts and ends, tool calls, questions.

## Rendering (as built)

**Instrument outside, game inside.** Everything around the pictures is embry0's normal interface: the page header, the review inbox, the agents' cards, the leads and their filters, the agent panel, New task, the lead dialog, the job report and the live panel are built from `components/ui` (Card, Button, Badge, Select, Dialog, Tabs) on the theme's tokens and fonts, in embry0's operator words (agents, jobs, leads, review, start, cancel). Kinds, levels, states and outcomes are Badges in the existing status tones. The pixel face appears only where the theme allows it: `.title-pixel` for the page title, `.sprite-nameplate` for the names over sprites, `.hud` for levels and XP numbers. The game's words (quest, loot, XP drops, attack, defeated) stay inside the pictures, the creatures' captions on the leads and the activity box. `world.css` holds only what the scenes need: the speech bubble, the XP drop and the hitsplat, and holding the art still under reduced motion.

**Closing.** Every dialog and panel is the app's Dialog: a Close button, Escape and a click outside close it, and focus goes back to what opened it. The activity box folds to a tab and closes; the page header opens it again; the choice is kept per browser (`localStorage`), and a phone starts it folded. Each of its lines copies as a debug block: the time (UTC), the line, the job's title, the agent, repository and area, the job id, a failure's `error_code` and exact `error_message`, and `/jobs/<id>`.

The hall's inside and the overworld are drawn from the art, with no canvas engine: the terrain is one `<canvas>` painted once, and everything that stands on it (objects, creatures, characters, effects) is an HTML element, so every one of them stays a button with a label. The counts are small (a few hundred sprites at most), and the characters are already crisp SVG composed from their parts.

- **The art** is described by `frontend/public/world/manifest.json`, whose schema is `frontend/public/world/README.md`: corner tilesets, atlases, objects with an anchor and a category, creatures with their frames and the lead kinds they stand for, and effects. The page reads it once; a missing manifest leaves the hall as its cards and the map as flat grass with small pixel glyphs for creatures.
- **Terrain**: a grid of corners, one mask per terrain (water, forest, rock, dirt, cobbles over grass). A tile is chosen from its four corners, `8·NW + 4·NE + 2·SW + 1·SE`, in the tileset that pairs that terrain with grass; tiles are drawn at the art's size and shown with `image-rendering: pixelated`.
- **The overworld** (`src/lib/worldTiles.ts`): the guild hall in the centre cell; each region, busiest first, on the next cell of a square spiral, as a cobbled square sized by its activity with a house per area (a house and a well when it has none), a banner and a signpost; a one-tile dirt path from the hall's door to every square; the cells no region takes grown over with forest (and trees), rock (and a boulder) or a pond; scenery on the grass. The same regions always give the same map. A lead stands on a grass spot of its region's cell that its id picks. Drag to look around; 1× or 2×.
- **Depth**: an element is placed by its anchor (where its feet are) and drawn in front by that point's height on the map.
- **Animation**: a creature's or an effect's frames step with CSS `steps()` keyframes generated from the manifest's frame rectangles. A party walks from the hall's door to its quest (and back) as a CSS transition, stepping on a two-frame walk composed from its own parts, each frame lifting one leg. Reduced motion stops every loop.
- **The hall's inside**: the hall tileset's floor walled on every side; each character standing at its station (the developer at the anvil, the readers at lecterns, QA and Kintsugi at the alchemy table, the analysts at the map table, everyone else at a desk), its station empty while it is out, with a `?` when it waits for you; torches, bookshelves, the quest board (a click goes to the board), and the loot chest: closed, open once a pull request came back, gold and sparkling once one merged (a click opens that quest's scroll). As many stations to a row as fit, never more than three rows; past that it scrolls sideways. It is drawn at 1×, or 2× when the room is wide.
- **Effects**: an XP drop with the amount over a character's station when its experience rises between two reads, level-up fireworks with a line in the chat box, a flashing hitsplat over a creature a party is fighting and over a defeated party, a sparkle over a party coming back with a pull request.

## Art (phase 2, built)

Original pixel art made with PixelLab, constrained by embry0's palette and a style image from the roster so it does not drift to generic fantasy; no traced or copied game art and no trademarked names. Where each image came from is in `frontend/public/world/SOURCES.md`.

- Terrain: corner tilesets of grass with dirt, water, rock, cobbles and forest, and the hall's floor with its wall.
- Objects: trees, bushes, flowers, rocks, a fence, signposts, houses, a well, a banner; the guild hall's outside; the stations, the quest board, torches, bookshelves and the chest closed, open and full of gold.
- Creatures, each a five-frame idle loop: `case_bug` (watcher case), `ci_golem` (failing CI), `alert_wyrm` (security or dependency alert), `flaky_imp` (flaky test), `review_ghost` (unanswered review comment), `quest_giver` (open issue, backlog ticket), `audit_signpost` (context-audit finding). The renderer draws hits itself.
- Effects: XP drop, level-up, loot sparkle, hitsplat.

## Phases

1. **The management loop** (built): the API, the guild as the home screen, the hall with every character and its quest log, the quest board and Write a quest, the scroll and the judgement (merge, send back, reject, rate), watching a party live, answering it, recalling it; a first map with parties at their quests; XP drops and the chat box.
2. **Art** (built): the PixelLab set behind the manifest, the overworld as a tile map, the hall's inside, the creatures and effects, walking frames composed from each character's parts.
3. **AFK mode**.
4. **Polish**: fog of war, a minimap, sound (off by default), seasonal touches.
