# Progression content pack: the game's content as data, original by default

**Goal.** Everything the progression layer shows is code today: the skills and what trains them, the experience curve and rates, the tiers, the rares, fifty achievements, four quests, the repo diaries and the chat-box wording. This spec moves all of it into one **content pack**, a versioned document whose schema embry0 ships in code and whose values a deployment holds in its database, the split ADR 0014 made for standards. embry0 ships a neutral default pack, the original theme of #201, as one file in the tree; that file becomes the only copy, and the dashboard reads every label from the API. An operator can rename, retune, translate, remove or add content, set the timezone and holidays, preview what the history reads as under a draft, and activate it. A change re-prices the whole history.

In the same change the service stops re-reading all history every minute. It drops three event branches nothing reads, and keeps experience as a per-day rollup per character and skill, rebuilt when the pack changes.

Issue #176, part of epic #159. Line numbers in sections 1 to 8 are at `e1e0567d`, before PR 1.

**Status.** The four open decisions were answered on 2026-10-28, each as recommended (see [Decisions](#decisions)). PR 1 is built to this spec; section 11 records what it settled and what PR 2 and PR 3 build on. Section 12 records what PR 2 settled.

**Non-goals.**

- **No new mechanics.** A pack fills a vocabulary the code defines: metrics, quest kinds, diary task kinds, action categories, outcome sources and art. It cannot add a metric or a quest's logic, and it never carries SQL.
- **No new art.** A pack chooses among the sprites the frontend draws. It cannot add a shape.
- **No translation of page chrome.** The pack holds the game's own words: names, descriptions, messages and a few nouns. Headings, buttons and empty states stay in the frontend.
- **Who an outcome credits stays in code.** The developer for a merge, the Slopmaster for a cleanup, `qa` for a QA job and `context-auditor` for an audit are decided in SQL (`embry0/storage/repositories/progression.py:26,35-67`). That is template behaviour, and #167 moves it onto template rows.
- **The world's own content stays out.** Lead kinds, creatures and verbs (`embry0/services/world.py`) are #166's.
- **Turning the game off is #177.**

## Words

| Word | Meaning |
|---|---|
| content pack (pack) | The document: every skill, rate, tier, rare, achievement, quest, diary task and message the progression layer uses |
| shipped default | The pack in the tree, `embry0/services/progression_pack_default.yaml`. A deployment uses it until an operator activates another, and a reset returns to it |
| vocabulary | What a pack can name, defined in code: metrics and their parameters, quest kinds, diary task kinds, action categories, outcome sources, art |
| action category | What one tool call was, independent of any skill: `shell:test-runner`, `edit:tests`. Stored on traces; the pack maps each category to a skill |
| rollup | Experience per day, character and skill for the days that can no longer change, priced under one pack |

## 1. Inventory

### Where the content lives

| Content | Backend | SQL | Frontend |
|---|---|---|---|
| 23 skills: id, name, group | `STATS`, `embry0/services/progression.py:78-112` | — | group names `SkillSheet.tsx:9-14`; `SkillGroup` union `api/progression.ts:7`; mastery cloak colours keyed by the 23 ids `roster/appearance/palette.ts:117-141`, the cloak items `items.ts:744-753` |
| What each tool call trains | the classifier emits skill ids, `embry0/execution/skill_actions.py:104-228` | each trace stores `kind:skill` keys in `traces.skill_actions` (migration 107, `embry0/storage/migrations/runner.py:3054-3063`) | — |
| Curve and maximum level | `progression.py:35-57`; character level `:260-268`; the world's lead level cap of 126 `services/world.py:47` | — | `99` in `SkillSheet.tsx:31` |
| Rates | `ACTION_XP :114-125`, `RUN_OUTCOME :127-137`, `OUTCOME_XP :139-148`, `XP_PER_STALE_CLAIM :153-154`; a cleanup's XP `services/slop.py:296-304` | — | — |
| Tiers | `TIERS` and `tier_for_level`, `progression.py:60-75`; the achievement ladder `TIER_NAMES`, `services/achievements.py:34`; `COMPLETE :36` | — | `TIER_COLOR`, `TIER_NAME`, `TIER_ORDER` `lib/tiers.ts:5-20`; `COMPLETE_NAME`, `COMPLETE_COLOR :23-24`; `Tier` union `api/progression.ts:4` |
| Gear | — | — | the seven art steps `items.ts:714-715`; the three ladders `outfit.ts:38-73`; each avatar's style `outfit.ts:76-86` |
| Rares | `RARE_DAYS`, `NEW_YEAR_RARE` `progression.py:218-222`; earned in `compute` `:451-459` | — | wear order `outfit.ts:19`; names `chatbox.ts:31-35`; art in `items.ts` |
| Achievements: 8 categories, 50 entries | `CATEGORIES :46-55`, presets `:77-79`, `CATALOGUE :81-599`; growth thresholds `:602-607` | thresholds as literals `repositories/progression.py:203-206, 213-214, 216-218, 223-225, 268-269, 272-274, 296-297, 301-303, 305-325` | icons `achievementIconData.ts` |
| Quests (4) | `services/quests.py:235-352`; the capstone's total level `progression.py:150-151`; its trim `:318-321` | — | "Deeds" and "deed points" `QuestJournal.tsx:21` |
| Repo diaries | tier names and quiet days `quests.py:21-24`; tasks `:104-146` | — | `DIARY_TIER_NAME` `chatbox.ts:28`; `DiaryTierName` union |
| Chat-box wording | — | — | `chatbox.ts:38-50` |
| Timezone (UTC) | `progression.py:400-401, 451-458` | `AT TIME ZONE 'UTC'` `repositories/progression.py:213, 307, 310, 313, 316` | — |
| Built-in agent names in rules | review agents `progression.py:129-130`, `quests.py:134, 295`; `qa` `quests.py:106-107, 297` | `repositories/progression.py:217-218, 274` | — |

### Data repeated in more than one place

1. **The tier ladder, four times:** `progression.py:63-71`, `achievements.py:34`, `lib/tiers.ts:15-20` and the `Tier` union (`api/progression.ts:4`), plus `docs/progression.md`, `docs/api.md:247` and the glossary.
2. **Holidays, three times, and they disagree:**
   - the rares, `progression.py:219-222`;
   - the `holiday_merge` SQL, `repositories/progression.py:316`;
   - the achievement's text, `achievements.py:559`.

   The SQL counts 1 January as a holiday, but the new-year rare is the first run of a new year, not a merge on 1 January. The maintainer kept both as they are: two rules, apart on purpose. The shipped pack says so in a comment beside the Holiday Event.
3. **Rare ids and order, three times:** `progression.py:218-222`, `outfit.ts:19`, `chatbox.ts:31-35`.
4. **Skill ids, three times:** `STATS`, the keys of `MASTERY_CLOAK` (`palette.ts:117-141`), and every trace's stored keys, which the classifier writes as skill ids.
5. **Total level 1,000, three times:** `progression.py:151`, `achievements.py:605`, and the requirement text `quests.py:331`.
6. **The maximum level:** 99 in `progression.py:35` and `SkillSheet.tsx:31`. The derived 126 appears in `world.py:47` and in a growth threshold (`achievements.py:488`).
7. **Diary tier names, twice:** `quests.py:22` and `chatbox.ts:28`.
8. **The review agents, four times:** see the last row of the table above.
9. **Quest reward texts restate the structured reward:** for example `quests.py:254` against `:261`.

### Verified dead: the `healed`, `watched` and `scheduled` event branches

`_EVENTS_SQL` selects these three sources with a NULL character (`repositories/progression.py:72-84`), and nothing reads them:

- `compute` skips every event without a character (`progression.py:460-461`). `price` returns nothing for one (`:166-167`), and both rare checks require one (`:451, :455`).
- `ProgressionRepository.events()` has one caller, `ProgressionService.read` (`progression.py:509`). The world's `quest_xp` builds its own events (`services/world.py:433-450`).
- The `healed` achievement metric reads `healing_cases` in the facts query (`repositories/progression.py:184-187, 221`), not these rows.
- Only the `scheduled` branch uses the `$1` bind and `gate_refusal_sql` (`:22, :84, :107`). Both go too.
- The only thing that pins the three branches is `tests/unit/storage/test_progression_repo.py:106-112`, which this change updates.

### Why the service re-reads all history every minute

- `OutfitProvider` is mounted app-wide (`AppLayout.tsx:28`) and polls `/progression` every 60 s (`hooks/useProgression.ts:5-13`).
- The guild polls `/world` every 5 s (`hooks/useWorld.ts:20,25`).
- Both call `ProgressionService.read`, whose cache lives 60 s (`progression.py:490-524`; `api/v1/world.py:168`).

Each recompute reads:

- every successful trace twice: once with its two JSON columns (`repositories/progression.py:30-32`), and once joined to its job for the journal (`:370-375`);
- every job (`:361-368`);
- the 43-branch facts union (`:175-327`).

`traces` has no index on `created_at` or `agent_type`. It has only `(job_id)`, `(job_id, agent_type)` and `(job_id, result)` (`runner.py:133, 560-563`).

## 2. The pack

### Shape

YAML, or the same structure as JSON. An excerpt of the shipped default:

```yaml
version: 1
timezone: UTC

curve:            # level L starts at floor(sum_{n<L} floor(n + points * 2^(n / doubling)) / divisor)
  max_level: 99
  points: 300
  doubling: 7
  divisor: 4

groups:
  - {id: build, name: Build}
  - {id: verify, name: Verify}
  # ...

skills:
  - id: testing
    name: Testing
    group: verify
    trained_by: [shell:test-runner, edit:tests]
    cloak: {base: "#e8e2d0", trim: "#9e2b2b"}
  # ... 22 more

rates:
  actions: {edit: 1400, shell: 600, read: 1000, search: 800, browser: 1000, log: 400, web: 2000, agent: 2000, plan: 2000}
  outcomes:
    merged:    {skill: coding, xp: 60000}
    audited:   {skill: documentation, xp: 40000, per_stale_claim: 1000}
    cleaned:   {skill: coding, xp: 20000, per_line_removed: 300, max_lines: 1000}
    # opened, qa_passed, asked, comeback, resumed
  runs:
    - {agent: review, skill: reviewing, xp: 30000}
    # pr-reviewer, triage, research, ops, onboarding, design-auditor, blast-radius

tiers:            # gear and achievements climb the same ladder
  - {id: bronze, name: Bronze, from_level: 1, color: "#9c6b2f", gear: bronze}
  # silver 15, gold 30, platinum 45, diamond 60, obsidian 75
  - {id: divine, name: Divine, from_level: 90, color: "#d4af37", gear: divine}

cloaks:
  quest: {base: "#2f4a8f", trim: "#ece6d4"}   # every quest done

rares:            # in wear order: a character wears the first one it holds
  - {id: golden-paper-crown, name: golden paper crown, art: golden-paper-crown, when: {first_run_of_year: true}}
  - {id: paper-crown, name: paper crown, art: paper-crown, when: {merge_on: "12-25"}}
  - {id: harvest-mask, name: harvest mask, art: harvest-mask, when: {merge_on: "10-31"}}

achievement_categories:
  - {id: shipping, name: Shipping, blurb: "Pull requests that merged, and how cleanly they got there."}
  # ...

achievements:
  - id: merged_for_a_penny
    name: Merged for a Penny
    category: shipping
    what_counts: Merged pull requests whose job spent under one US dollar
    flavour: Cheapest upgrade in the game.
    icon: coins
    metric: merged_under_cost
    params: {max_usd: 1}
    thresholds: [1, 3, 10, 25, 50, 100, 200]
  # ... 49 more

quests:
  - id: divine_warden
    kind: capstone
    name: Divine Warden
    points: 5
    reward: 5 quest points; a Divine trim on every cape
    steps: ["Merge {merges} pull requests", "Complete a repo diary at Elite"]
    requirement: "A character at total level {total_level}"
    params: {merges: 100, diary_tier: elite, total_level: 1000}
    cloak_trim: true
  # first_steps, try_again, gatekeeper

diaries:
  tiers:
    - id: elite
      name: Elite
      tasks:
        - {kind: merges, params: {count: 50}, text: "Merge {count} pull requests"}
        - {kind: quiet_stretch, params: {days: 30}, text: "Go {days} days without a failed job"}
        - {kind: all_above, text: Complete every task above}
    # easy, medium, hard

messages:
  level: "{character} reached {skill} level {level}."
  agent_level: "{character} is now level {level}."
  rare: "{character} found a {rare}."
  quest: "Quest done: {name}."
  diary: "The {tier} tasks of the {repo} diary are done."
  achievement_tier: "{name} reached {tier}."
  achievement_complete: "Achievement done: {name}."

labels:
  complete: Complete
  quest_journal: Deeds
  journal_points: deed points
  quest_point: point
  quest_points: points
  # gear, mastery_cloak, trimmed_cloak, quest_cloak
```

### Sections and their rules

| Section | Holds | Rules (each failure is a 422 naming the key) |
|---|---|---|
| `version` | the schema the document is written in | optional; absent means this release's, a newer one is a 422 |
| `timezone` | the game's calendar | an IANA name `zoneinfo` knows; default `UTC` |
| `curve` | `max_level`, `points`, `doubling`, `divisor` | `max_level` 2 to 200; the table it makes rises strictly |
| `groups[]` | where skill tiles sit on a sheet | 1 to 8, unique ids |
| `skills[]` | id, name, group, `trained_by` (action categories), cloak colours | 1 to 64. Each group exists. A category trains at most one skill. A category no skill names earns nothing, reported as a warning, not a 422. Colours are `#rrggbb` |
| `rates` | XP by action kind; by outcome source; by agent for a finished run | kinds and sources from the vocabulary; skills exist; XP 0 to 10,000,000. An agent is free text, since custom agents are rows; one not defined now is a warning |
| `tiers[]` | id, name, `from_level`, colour, the gear art step | 1 to 12. The first starts at 1; `from_level` rises; at most `max_level`; `gear` is a shipped art step |
| `cloaks` | the quest cloak's colours | `#rrggbb` |
| `rares[]` | id, name, optional art, when it drops | `when` is `{merge_on: MM-DD}` or `{first_run_of_year: true}`; `art` is a shipped rare. The order is the wear order |
| `achievement_categories[]` | id, name, blurb | unique ids |
| `achievements[]` | id, name, category, what counts, flavour, icon, secret, metric, params, thresholds | The metric is in the vocabulary, and its params validate against that metric's model. Thresholds: one value (an objective) or one per tier, rising. The icon is a shipped icon. Placeholders in `what_counts` name params |
| `quests[]` | id, kind, name, points, reward text, step texts, requirement, params, XP rewards, `cloak_trim` | The kind is in the vocabulary. The step count is the kind's. Params validate. Reward skills exist. `cloak_trim` is allowed on `capstone` only |
| `diaries.tiers[]` | id, name, tasks of a kind with params and text | 1 to 8 tiers, 1 to 10 tasks each. `all_above` is not allowed in the first tier. Text placeholders name params |
| `messages` | one template per chat-box event | the seven keys, each using only its kind's placeholders (below) |
| `labels` | the game's nouns the progression components print | a fixed key set, all present |

Limits: ids match `[a-z0-9][a-z0-9_-]{0,39}` and are unique within their section. Names are at most 80 characters, texts at most 300, and the document at most 256 KiB.

### Achievements: a metric plus parameters, bound in SQL

The facts query is built from the active pack. Each metric is a SQL fragment in code, keyed by the metric's id. A pack's params reach it only as positional bind values, never as text. Fragments are emitted only for metrics the pack uses, and two achievements with the same metric and params share one fragment.

The shared `merged` and `healed` CTEs stay (`repositories/progression.py:176-187`). Each fragment's rows carry the fragment's index, an integer the code generates. Python fans the rows out to the achievements that use the fragment. Achievement ids stay as they are. Metric ids that take no parameter keep their names, and so do `healed`, `reviews` and `resumed`, which gain parameters; the rest get capability names. The metrics that become parameterised, with the shipped default's values:

| Metric | Parameters (default) | Today | Achievement |
|---|---|---|---|
| `merged_under_cost` | `max_usd: 1` | `bargain`, `:203-204` | Merged for a Penny |
| `merged_within` | `hours: 24` | `speedrun`, `:206` | Speedrun Any% |
| `merges_in_a_day` | `count: 3` (the day in the pack's timezone) | `hot_streak`, `:213-214` | Hot Streak |
| `merged_reviewed_and_tested` | `review_agents: [review]`, `qa_agents: [qa]` | `full_party`, `:216-218` | Full Raid Party |
| `healed` | `severity` (none, or `high`), `min_seen` (1, or 10) | `healed`, `boss_healed`, `superior_healed`, `:221-225` | Hunt Complete, Boss Hunter, Elite Hunter |
| `design_score` | `min_score: 90` | `design_ninety`, `:268-269` | Fashion Inspector |
| `reviews` | `agents: [review, pr-reviewer]` | `:272-274` | Guardian of the Gate |
| `resumed` | `min_resumes` (1, or 2) | `resumed`, `lag_survivor`, `:293-297` | Back from the Respawn, Lag Survivor |
| `merged_on_attempt` | `min_attempts: 3` | `iron_will`, `:301-303` | Iron Will |
| `merged_at_local_time` | `weekdays`, `from_hour`, `to_hour` | `weekend_merge` (6, 7; 0 to 24), `midnight_merge` (every day; 0 to 4), `friday_night` (5; 20 to 24), `:305-313` | AFK Weekend, Night Shift, Friday Night Merge |
| `merged_on_dates` | `dates: ["12-25", "10-31", "01-01"]` | `holiday_merge`, `:315-316` | Holiday Event |
| `merged_running_over` | `hours: 2` | `long_grind`, `:318-319` | Long Grind |
| `fixed_recurring` | `min_seen: 99` | `ninety_nine`, `:324-325` | 99 Problems |
| `skills_at_level` | `level: 92` | `level_92`, `achievements.py:607, 659-661` | Halfway by Experience |
| `characters_at_total` | `total: 1000` | `thousand_club`, `:605, 662-663` | Thousand Club |

The growth metrics (`best_total`, `capes`, `best_level`, `rares`, `skills_at_level` and `characters_at_total`) are computed in Python from the level-ups, as today (`achievements.py:644-670`). Their baselines come from the pack: the number of skills is the starting total level, replacing `len(STATS)` at `:602, 646` and `progression.py:473`.

### Quests and diaries: kinds with parameters

The quest logic stays in `quests.py`. A pack instantiates it:

| Kind | Steps | Params | Today |
|---|---|---|---|
| `onboard_to_merge` | 3: onboard a repository, finish a job after it, merge after it | — | First Steps, `quests.py:235-262` |
| `merge_after_failure` | 2: a job on an issue fails, a later one merges | — | Try, Try Again, `:265-287` |
| `review_qa_merge` | 3: review, QA, merge on one issue | `review_agents`, `qa_agents` | Gatekeeper, `:290-317` |
| `capstone` | 2: merges, a diary tier completed; plus a total-level requirement | `merges`, `diary_tier`, `total_level` | Divine Warden, `:320-333` |

A quest's XP rewards, `xp: [{agent, skill, xp}]`, land at its completion and feed the levels, as today (`quests.py:336-342`).

`cloak_trim` replaces the check on the quest id `divine_warden` (`progression.py:318-321`). The trim is art: it is drawn in the Divine metal, as before. The API's `account.divine_trim` becomes `account.cloak_trim`, and `divine_trim` stays as an alias for one release.

The diary task kinds are `triage` (`agents: [triage]`), `finish_job`, `run_qa` (`agents: [qa]`), `merges` (`count`), `pass_qa` (`agents: [qa]`), `audit`, `clean_audit`, `reviews` (`count`, `agents: [review, pr-reviewer]`), `quiet_stretch` (`days`) and `all_above`. These are today's tasks (`quests.py:113-145`) with their numbers and agent names as params.

The template names the kinds read (`onboard`, `qa`, `context-audit`) stay in code: they are template identity, which belongs to #167.

### Messages and labels

| Message | Placeholders |
|---|---|
| `level` | `{character}`, `{skill}`, `{level}` |
| `agent_level` | `{character}`, `{level}` |
| `rare` | `{character}`, `{rare}` |
| `quest` | `{name}` |
| `diary` | `{tier}`, `{repo}` |
| `achievement_tier` | `{name}`, `{tier}` |
| `achievement_complete` | `{name}` |

The server validates the placeholders. The client fills them, because `{character}` is the agent's display name, which the dashboard already resolves (`chatbox.ts:38-50` takes `characterName`).

**One template grammar** (`embry0/services/progression_templates.py`), for every text a pack holds, whoever fills it:

- `{name}` is a value, where `name` is an identifier the text may use;
- `{name:,}` is a number with thousands separators (`{total_level:,}` reads `1,000`);
- `{{` and `}}` are literal braces.

Anything else in braces is refused when the pack is checked, each as a problem naming the key. That covers a conversion (`{name!r}`), any other format spec (`{name:>9}`), a nested or numbered field, an index or an attribute, and a lone brace. So an admin's text can neither raise nor grow on a read. The server renders with that module's `render`, never `str.format`, and the dashboard's `fill` (`frontend/src/lib/gamePack.ts`) implements the same grammar. `docs/contracts/progression.json` carries cases both are tested against. A list parameter cannot be written into a text.

`labels` is a fixed set of nouns:

| Key | Default |
|---|---|
| `complete` | Complete |
| `quest_journal` | Deeds |
| `journal_points` | deed points |
| `quest_point`, `quest_points` | point, points |
| `gear` | `{tier} gear` |
| `mastery_cloak` | `{skill} cape` |
| `trimmed_cloak` | `{skill} cape (trimmed)` |
| `quest_cloak` | quest cloak |

These are the nouns the progression components print today (`QuestJournal.tsx:21`, `SkillSheet.tsx:58-59, 74`, `AgentFormPage.tsx:367`, `lib/tiers.ts:23`).

### Validation

The pack is a set of pydantic models with `extra="forbid"`, followed by cross-reference checks: groups, skills, categories, tiers, metrics and their params, kinds, art, placeholders. Every problem is collected, not just the first.

The 422 has the settings document's shape (#160), FastAPI's own list, which the dashboard already renders: `{"detail": [{"loc": ["achievements", 3, "thresholds"], "type": "thresholds", "msg": "3 values; an objective has one, a tiered achievement one per tier (7)"}]}`. Each problem's `loc` is its path from the document's root; its value is never echoed. In PR 1 the problems come from `progression_pack.parse` as `Problem(loc, type, msg)`, and `python -m embry0.services.progression_pack --check <file>` prints them as `achievements[3].thresholds: ...`. Warnings (an unmapped category, an agent not defined now) come back with a successful save and a dry run, and never block.

### Schema versioning

`version` starts at 1, as in the settings document (#160).

- **A write may leave `version` out**, which means this release's. A newer one gets a 422 on `version` that names the version this release reads. An older one, written by hand, gets the same 422 and a pointer to `GET /progression/pack/default`.
- **A stored pack of an older version is upgraded at startup** by a code migrator chain, and saved as a new version with source `upgrade`. Its text is regenerated from the document, so its comments are lost, and the version's reason says so.
- **The shipped default always carries the release's version.**

### Module boundaries

- **`embry0/services/progression_vocabulary.py`.** A leaf module: it imports pydantic and the classifier's category list (`embry0/execution/skill_actions.py`, itself standard library only), nothing from the progression services, so nothing forms a cycle. It holds the outcome sources, action kinds, quest and task kind names with their parameter models, metric ids with their parameter models, and the art vocabulary.
- **`embry0/services/progression_pack.py`.** The schema models, `load(text)`, `validate(...)`, `default_pack()`, `pack_hash(pack)`, and `Rules`, which is precomputed from a pack: the XP table, the category-to-skill map, the tier lookup and the rare calendar.
- **The services take a `Rules` argument and import no storage:** `progression.py` (`compute`, `price`, `Character`), `achievements.py`, `quests.py` and `world.quest_xp`.
- **`embry0/storage/repositories/progression.py`.** It holds one SQL fragment per metric, keyed by the vocabulary's ids. A test pins the two key sets equal.
- **`embry0/storage/repositories/progression_packs.py`** (PR 2) and **`embry0/services/progression_rollup.py`** (PR 3).

## 3. Storage and lifecycle

### What a character belongs to

A character is an agent type, and agent types are deployment-wide: `agent_definitions` has no owner column (`runner.py:206, 2845-2848`). Its experience comes from the runs of jobs on any repository. Some jobs have no repository at all; the journal filters on `j.repo IS NOT NULL`, `repositories/progression.py:366`. The account is the deployment (`progression.py:7-9`).

So one pack per deployment fits the model as it stands. A pack per organization would need an account per organization, with a character's experience split by the owner of each job's repository. The maintainer chose one pack per deployment (decision 1).

### Table (migration 130)

```sql
CREATE TABLE IF NOT EXISTS progression_packs (
    version         INTEGER PRIMARY KEY CHECK (version >= 1),
    status          TEXT NOT NULL CHECK (status IN ('draft', 'active', 'superseded')),
    schema_version  INTEGER NOT NULL,                 -- the document's `version`
    follows_default BOOLEAN NOT NULL DEFAULT FALSE,   -- reset: whatever default the running release ships
    text            TEXT,                             -- as written, comments kept; NULL when follows_default
    document        JSONB CHECK (document IS NULL OR jsonb_typeof(document) = 'object'),
    based_on        TEXT,                             -- pack_hash of the shipped default it was edited from
    source          TEXT NOT NULL CHECK (source IN ('person', 'file', 'restore', 'reset', 'upgrade')),
    reason          TEXT NOT NULL DEFAULT '',
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at    TIMESTAMPTZ,
    CHECK (follows_default OR (text IS NOT NULL AND document IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS progression_packs_one_active ON progression_packs ((true)) WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS progression_packs_one_draft  ON progression_packs ((true)) WHERE status = 'draft';
```

With no row, the deployment follows the shipped default. A release that changes the default therefore reaches a deployment nobody customised, as code constants do today.

The versions are the revision history, as for standards (`runner.py:2896-2953`). The newest 100 superseded versions are kept, the same limit as `repo_config_revisions` (`repositories/repo_config_revisions.py:22`). A per-organization answer to decision 1 adds an `owner` column to the key.

### Lifecycle

| Step | What it does |
|---|---|
| save a draft | Validates and stores the one draft (an upsert). It changes nothing anyone sees |
| preview | Computes `GET /progression`'s answer under the draft from the raw rows, uncached. It adds `changes`: each character's level and total level, and the count of achievements unlocked, before and after |
| activate | The draft becomes the active version and the old one is superseded, in one transaction |
| one-shot put | Validates and activates in one call, for the CLI and MCP; `?dry_run=1` answers the problems, the warnings and a diff, and saves nothing |
| restore | A superseded version's document becomes a new active version, with source `restore`: this is undo |
| reset | A new active version with `follows_default`, source `reset`, as `POST /agents/{type}/reset` restores a seed (`api/v1/agents.py:176-183`) |

The maintainer chose the stored draft, the preview and the activate step, with the one-shot PUT beside them for the CLI and MCP (decision 3), and a customised pack as a whole document (decision 4). Revision sources follow the settings document's (#160), with `reset` added: `person`, `file` (#162's `apply`), `restore`, `reset`, `upgrade`. Writes take the admin tier and an unrestricted key, as for the settings document.

The dashboard shows when the shipped default has moved on since a customised pack was based on it (`based_on` against the current default's hash), with the diff between the two defaults.

### API

| Route | Tier | Does |
|---|---|---|
| `GET /api/v1/progression/pack` | read | The active pack: `version`, `follows_default`, `schema_version`, `hash`, `text`, `document`, `based_on`, `default_hash` |
| `GET /api/v1/progression/pack/default` | read | The shipped default's text |
| `GET /api/v1/progression/pack/vocabulary` | read | The JSON Schema, plus every metric, kind, category and art id with its parameters, for editors |
| `PUT /api/v1/progression/pack` (`?dry_run=1`) | admin | A YAML or JSON body; validate, then activate (or only report) |
| `GET`, `PUT`, `DELETE /api/v1/progression/pack/draft` | read, admin, admin | The one draft |
| `GET /api/v1/progression/preview` | admin | The progression under the draft, with `changes`. One at a time |
| `POST /api/v1/progression/pack/draft/activate` | admin | The draft goes live |
| `GET /api/v1/progression/pack/versions` | read | Versions, newest first, without their text |
| `POST /api/v1/progression/pack/versions/{version}/restore` | admin | Undo to that version |
| `POST /api/v1/progression/pack/reset` | admin | Follow the shipped default |
| `POST /api/v1/progression/rebuild` | admin | Rebuild the rollup now (PR 3), for example after a merge backfill |

Admin tier, like writing a standard (`api/v1/standards.py:178`): a pack decides what every page shows and re-prices the history.

Audit actions:

- `progression.pack_activated`, with the version, the previous version and the source;
- `progression.pack_draft_saved`;
- `progression.pack_reset`.

### MCP, CLI and dashboard

- **MCP:**
  - `progression_pack_get`;
  - `progression_pack_set(text, dry_run=False, confirm=False)`, the one-shot put;
  - `progression_pack_reset(confirm=False)`.

  The writes require `confirm=true`, like `standard_set` (`embry0/mcp_server/server.py:1125-1146`).
- **CLI:** `embry0 progression-pack` with `check <file>`, `diff <file>`, `apply <file>` and `export [--default]`.
  - `check` validates offline with the same validator, prints the same messages and exits 1 on a problem.
  - `diff`, `apply` and `export` go through the API like `embry0 onboard` (`cli/__main__.py:1008-1031`). `apply` shows the diff first.
  - These are #162's verbs, so the settings file workflow and the pack read alike.
- **Dashboard:** Settings gets a Game content tab, for admins. It is a YAML editor in the style of `QaConfigEditor`, with these actions:
  - **Check** (a dry run: problems, warnings and the diff);
  - **Save draft**;
  - **Preview** (the `changes` table);
  - **Activate**;
  - **Reset to default**;
  - **History**, with restore.

## 4. The shipped default as the single source

- **The file.** `embry0/services/progression_pack_default.yaml` is loaded with `importlib.resources`. The wheel packages all of `embry0/` (`pyproject.toml:86-87`), and the orchestrator image copies it (`infra/Dockerfile.orchestrator:51`). The file is commented, so an operator can read and copy it (`export --default`). The name-denylist check covers it like the rest of the tree.
- **It reproduces today exactly.** A golden test pins it. PR 1's first commit records `Progression.to_dict()` for a seeded history from the code as it stands (see Testing). Under the default pack, the refactor must give the same value for every field that exists today; the new fields are tested on their own.
- **Code removed.** `TIERS`, `STATS`, `ACTION_XP`, `RUN_OUTCOME`, `OUTCOME_XP`, `DIVINE_WARDEN_TOTAL`, `XP_PER_STALE_CLAIM`, `RARE_DAYS`, `NEW_YEAR_RARE`, `TIER_NAMES`, `COMPLETE`, `CATEGORIES`, `CATALOGUE`, `THOUSAND`, `HALF_OF_MAX`, `DIARY_TIERS`, `QUIET_DAYS`, the quest literals, `CLEANED_XP` and its siblings, and the threshold literals in the facts SQL.
- **Frontend copies removed:**
  - `TIER_COLOR`, `TIER_NAME`, `TIER_ORDER` and `COMPLETE_NAME` (`lib/tiers.ts`, which is gone);
  - `RARE_NAMES`, `DIARY_TIER_NAME` and the message wording (`chatbox.ts`);
  - `GROUPS` (`SkillSheet.tsx`);
  - the `MASTERY_CLOAK` and `QUEST_CLOAK` colours (`palette.ts:117-145`);
  - `RARES` (`outfit.ts`);
  - "Deeds" (`QuestJournal.tsx`);
  - the closed unions `Tier`, `SkillGroup` and `DiaryTierName`, which become `string`.

  `readable()`, the parchment of a complete objective and the slate of a tierless entry stay, in `lib/gamePack.ts`; they are presentation.
- **`GET /progression` gains:**
  - `pack: {hash, version, timezone}` (PR 2 adds the stored revision and whether it follows the default);
  - `groups`, `tiers`, `rares`, `cloaks`, `diary_tiers`, `messages` and `labels`;
  - `curve: {max_level, character_max_level}`;
  - `skills[].cloak`;
  - `characters[].gear`, the art step of its tier.

  These are a few kilobytes on a once-a-minute poll. The guild page and the agent form read tier names through the same app-wide query, not `/world`.
- **The world's lead cap** (`services/world.py:47`) stays at 126. It is how menacing a lead looks on the map, not a character's level, so PR 1 leaves it.
- **Docs:**
  - `docs/progression.md` gets a "Changing the content" section;
  - `docs/api.md` gets the routes and the new fields;
  - `docs/configuration.md` gets a "Runtime configuration (API)" entry;
  - the glossary gets **Content pack** and **Action category**, and **Tier** is reworded as "the pack's ladder".

## 5. Art and equipment

The ladders are art: shapes drawn over a colour key (`items.ts`), composed per combat style (`outfit.ts:54-73`). Item names never reach a person. The only gear words on screen are "`<Tier>` gear", "`<skill>` cape", "(trimmed)" and "quest cloak" (`SkillSheet.tsx:58-59, 74`; `AgentFormPage.tsx:367`), and those become labels. So the pack has **no equipment section**: a ladder section would describe sprites the frontend cannot draw.

| A pack can change, without new art | How |
|---|---|
| tier names, count, order and starting levels | `tiers[]` |
| which art step each tier wears, including skipping or repeating one | `tiers[].gear`, one of the seven shipped steps |
| tier colours (chips, bars, the cloak trim) | `tiers[].color` |
| each skill's mastery cloak | `skills[].cloak`, two colours over the one cloak shape |
| the quest cloak's colours | `cloaks.quest` |
| which art a rare wears, or none | `rares[].art` |
| an achievement's icon | `achievements[].icon`, one of the shipped icons |

A pack cannot add a shape, rename an item or recolour a gear step. The finery rule (cape and amulet from the fourth step up, `outfit.ts:38-40`) and the tool retooling (`outfit.ts:89-94`, `retooled`) follow the art step, not the tier.

**Mastery cloaks from data.** `MASTERY_CLOAKS` is built at module load for the 23 fixed ids today (`items.ts:744-753`). It becomes a cloak item composed at runtime from the pack's colours. `outfitKey` (`outfit.ts:110-112`) gains the cloak colours, so the sprite cache stays correct.

**A name with no sprite.** The art vocabulary lives in `progression_vocabulary.py` and is exported to `docs/contracts/progression.json`. Both sides pin it, as `identity.json` is pinned (`tests/unit/agents/test_identity.py:18-22`, `frontend/src/lib/__tests__/agentIdentity.contract.test.ts`).

| Field | Missing or unknown |
|---|---|
| `tiers[].gear` | Required; an unknown step is a 422 |
| `rares[].art` | Optional. A rare without art is earned, named and listed, but not worn; an unknown art id is a 422 |
| `skills[].cloak` | Optional; without it the skill wears a neutral slate cloak |
| `achievements[].icon` | An unknown icon is a 422 |

The frontend still falls back if a mismatched build ever sends one: the first step for an unknown gear step, no head item for an unknown rare, and the star for an unknown icon (as `achievementIcons.tsx:23` does already).

## 6. Action categories on traces

**Where the events come from.**

1. The sandbox's runner streams `tool_call` events with `tool_name` and `tool_input`.
2. The orchestrator's agent node counts them as they pass (`embry0/orchestration/nodes/agent.py:1100-1112`), with `count_action` from `embry0/execution/skill_actions.py`.
3. It stores the counts when the trace is finished (`agent.py:1179`, through `repositories/traces.py:94-134`). The trace row itself was written as `running` when the run started (`agent.py:1062-1069`).

`skill_actions.py` is not baked into any sandbox image: `infra/Dockerfile.sandbox:67-70` copies only `events.py`, `agent_runner.py`, `auth_provider.py` and `docker_client.py` from `embry0/execution/`. So a classifier change takes effect with an orchestrator deploy, and **no image rebuild is needed**.

**What changes.**

- **The keys.** The classifier emits `kind:category` instead of `kind:skill`. The categories name what the call was, never a skill:

  | Kind | Categories |
  |---|---|
  | `edit` | `docs`, `tests`, `sql`, `styles`, `infra`, `source` |
  | `shell` | `vcs`, `test-runner`, `linter`, `security-scanner`, `database-client`, `build-tool`, `ops-tool`, `plain` |
  | `read` | `files` |
  | `search` | `code-search` |
  | `web` | `web` |
  | `agent` | `subagent` |
  | `plan` | `todo` |
  | `browser` | `browser` |
  | `log` | `log-query` |

- **No new column.** The keys go in the same column, `traces.skill_actions`; no migration changes.
- **The categories mirror today's distinctions one to one.** Under the default pack every run therefore earns exactly what it earns today. A later, finer split (say, `shell:package-install`) is additive: it affects new traces only, and an older pack that does not name it earns nothing for it, which is reported as a warning.
- **History without rewriting rows.** Traces written since migration 107 hold the 21 old `kind:skill` keys. A fixed table in code (`LEGACY_ACTION_KEYS`) maps each one to its category at read time, inside `run_actions` (`progression.py:157-161`), which `world.quest_xp` shares. A test pins that the old and new key sets are disjoint and that every old key maps. Traces from before 107 keep only tool names and read through `actions_from_tools` as today (`skill_actions.py:238-245`), as `shell:plain` and `edit:source`.
- **Re-pricing.** A trace never holds a skill again, so a pack that renames, merges, removes or remaps skills re-prices every run back to migration 107. Before 107, as today, only the tool name is known.

## 7. Performance and timezone

### Dead branches

`healed`, `watched` and `scheduled` leave `_EVENTS_SQL`, along with its `$1` bind and the `gate_refusal_sql` import (section 1). The golden test shows `/progression` unchanged.

### The rollup (migration 131)

```sql
CREATE TABLE IF NOT EXISTS progression_rollups (
    pack_hash   TEXT PRIMARY KEY,           -- the effective pack it was priced under
    timezone    TEXT NOT NULL,
    through_day DATE,                       -- the newest closed day folded in
    state       JSONB NOT NULL,             -- what the fold carries: each character's own level, the last run year
    rewards     JSONB NOT NULL,             -- the quest rewards folded in: [{quest, completed_at}]
    built_at    TIMESTAMPTZ NOT NULL,
    rebuilt_at  TIMESTAMPTZ NOT NULL        -- the last full rebuild
);
CREATE TABLE IF NOT EXISTS progression_daily (
    pack_hash  TEXT NOT NULL REFERENCES progression_rollups ON DELETE CASCADE,
    day        DATE NOT NULL,
    agent_type TEXT NOT NULL,
    skill      TEXT NOT NULL,
    xp         BIGINT NOT NULL,
    PRIMARY KEY (pack_hash, day, agent_type, skill)
);
CREATE TABLE IF NOT EXISTS progression_milestones (
    pack_hash  TEXT NOT NULL REFERENCES progression_rollups ON DELETE CASCADE,
    at         TIMESTAMPTZ NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('level', 'agent_level', 'rare')),
    agent_type TEXT NOT NULL,
    skill      TEXT,
    level      INTEGER,
    rare       TEXT
);
CREATE INDEX IF NOT EXISTS idx_traces_created_at ON traces (created_at);
CREATE INDEX IF NOT EXISTS idx_traces_success_agent
    ON traces (agent_type, created_at) WHERE result = 'success' AND error_code IS NULL;
```

**Closed days.** A day in the pack's timezone is closed once it ended 48 hours ago. A run keeps its start time (above), and a run paused on a person's answer or an account wait can finish much later. A merge carries the forge's time (`delivery_state_at`) and arrives with the webhook or the sweep. 48 hours covers the ordinary case; the nightly rebuild covers the rest.

**A read** happens at most once a minute, as now. It does this:

1. Read the active pack's hash.
2. If no rollup exists for that hash, **build** it. A build folds every closed day from the raw rows in time order. It costs what every read costs today, and happens once per pack.
3. If newer days have closed since `through_day`, **advance**: fold those days' raw events and append their rows.
4. **Rebuild** in full if `rebuilt_at` is more than 24 hours old, or if the quest rewards due at or before the boundary differ from the ones in `rewards`. Quest XP feeds the levels (`progression.py:511-512`), and a late merge can complete a quest at a past time.
5. Fold the **tail**, the raw events after the boundary, on top of the boundary state: the summed `progression_daily` plus `state`.
6. The level-ups and drops are the stored milestones plus the tail's.

**Exact, not rounded to a day.** Builds and advances fold raw events, so the milestones keep their exact times. Level-up times, rare drops, the growth achievements' unlock times and the capstone's requirement time (`first_total`, `progression.py:467-477`) are all the same as a full recompute's. The equivalence test pins this.

**What a read costs in steady state:**

- the rollup sums (characters × skills rows);
- the milestones, bounded by characters × skills × the maximum level;
- about three days of raw events, through `idx_traces_created_at`;
- the journal's runs, which are narrowed to the agents the pack's quest and diary kinds name (`agent_type = ANY($1)`, through `idx_traces_success_agent`, which also serves the facts' `reviews` fragment). Today the journal reads every successful run (`repositories/progression.py:370-375`).

The facts query keeps its shape. It runs only the fragments the pack uses.

**Re-pricing.** Activating a pack, a reset, a restore, a timezone change and a release that changes a followed default all change the hash. Step 2 then builds a new rollup, and the old rollup is deleted with its rows. A preview never builds one: it computes from the raw rows.

**Staleness, bounded and visible.** An event that lands more than 48 hours after its own time counts toward XP at the next nightly rebuild, so within 24 hours. Achievements, quests and diaries are always fresh, because they never read the rollup. `GET /progression` reports `rollup: {through_day, built_at, rebuilt_at}`, and `POST /progression/rebuild` forces a rebuild.

**Concurrency.** A build or advance runs under a Postgres advisory lock and writes in one transaction. A second process waits for it, then reads the result.

### Timezone

`timezone` is the game's calendar: an IANA name that defaults to `UTC`, so nothing moves until an operator sets one. It decides:

- the rollup's day;
- the rare dates and the new year (`progression.py:451-458`);
- the day a hot streak counts in;
- the weekday and hour rules;
- the holiday dates (`repositories/progression.py:213, 305-316`, bound as `AT TIME ZONE $n`).

Durations do not depend on it: `merged_within`, `merged_running_over`, `quiet_stretch`. PR 1 plumbs the timezone through with UTC and keeps every text as it is. PR 2, which lets an operator set it, removes "(UTC)" from the default's `what_counts` texts (`achievements.py:526, 537, 548, 559`), and the achievements page shows the pack's timezone once, next to the oddities.

## 8. Relation to #160

#160 designs one settings document with a section per feature, for operator policy. The pack is **its own document**, not a section of it (decision 2, as recommended):

- **A different lifecycle.**
  - A pack is edited at length (a translation is hundreds of lines).
  - It is previewed before it goes live, because it re-prices history.
  - It is reset to a shipped default that moves with each release.

  A setting is a value: #160's `?dry_run=1` diff is enough for it, and it has no seed to follow.
- **Separate undo.** In a shared revision history, undoing a policy change would roll back a content edit made after it, and the other way round.
- **Separate cost.** Activating a pack rebuilds the rollup. Saving a setting should not.
- **No blocking.** #160 is not built. PR 1 needs neither, and PR 2 need not wait for it.

Shared on purpose, so the two read alike:

- YAML text kept with its comments, beside the parsed document;
- the 422 problems list, each problem naming its key;
- `?dry_run=1`;
- the revision sources (`person`, `restore`, `reset`, `upgrade`);
- #162's CLI verbs;
- MCP `confirm=true`.

Policy *about* the game, such as #177's `ui.game` on or off, belongs in #160's document, not in the pack. If #160 grows a deployment timezone, the pack's `timezone` can accept `inherit`.

## 9. Pull requests

PR 1 changes nothing a person sees. PR 2 and PR 3 each depend only on PR 1 and can land in either order; migration numbers are renumbered at merge if PR 3 goes first. Commit subjects follow the commit standard.

1. **`feat(progression): read game content from the shipped pack`.** No migration. The largest PR, but mechanical, and proven by the golden test.
   - The vocabulary and pack modules, the default YAML and the validator; the services and the facts SQL read a `Rules` built from the pack, with params as bind values and the timezone plumbed through (UTC).
   - Action categories in the classifier, with the legacy key table.
   - The dead branches dropped.
   - `GET /progression`'s new fields.
   - The frontend reads labels, cloaks, rares and messages from the API, and its copies are deleted.
   - The art contract.
   - Docs and glossary.
2. **`feat(progression): let an operator change the content pack`.** Migration 130.
   - The pack repository and versions;
   - the routes, audit actions, MCP tools, CLI and the dashboard tab;
   - the startup upgrade;
   - "(UTC)" out of the default's texts, and the timezone shown on the achievements page;
   - docs, and an ADR that records the content-pack split (the next free number at merge).
3. **`perf(progression): keep experience as a daily rollup`.** Migration 131.
   - The rollup tables and the two trace indexes;
   - build, advance and nightly rebuild, and `POST /progression/rebuild`;
   - the narrowed journal query;
   - the equivalence and cost tests.

## 10. Testing

Every new test is shown failing on the tree before its PR, with its own assertion message. Where a test imports a module that does not exist yet, the evidence is an assertion-level variant through a surface that does exist: `GET /progression` with a pack fixture, or `compute` with a `Rules`.

A characterization test passes before and after by design. Each one is shown able to fail instead: change one rate in the default pack and watch the golden test fail.

**PR 1**

- **Golden.** A seeded history, written as Postgres fixtures, runs through the real queries. It covers:
  - runs with `kind:skill` keys (the only kind the old code writes) and runs with only `tools_called`;
  - merges on a holiday and at night;
  - a slop cleanup, a context audit with stale claims, a comeback and a resumed job;
  - healing cases and proposals;
  - enough work to level skills to 99.

  Under the default pack, `Progression.to_dict()` equals what the code as it stands recorded, field for field, and so does `world.quest_xp`. A second test rewrites every run to the new category keys and expects the same result (`test_the_same_history_on_todays_category_keys_reads_the_same`).
- **Boundaries.** The golden history sits far from every threshold, so the repository test adds rows just inside and just outside each shipped threshold an operator can now set. It also adds one case on a non-UTC clock.
- **Validation.** A table of bad packs, each giving a 422 that names its key:
  - an unknown key or a wrong type;
  - thresholds whose count is not the tier count, or that do not rise;
  - a first tier not at 1;
  - an unknown metric, param, kind, category, skill or art;
  - a category mapped twice;
  - a bad timezone;
  - an unknown placeholder;
  - duplicate ids;
  - a step count that is not its kind's;
  - `all_above` in the first tier;
  - the wrong schema version.

  The default pack passes, and its YAML round-trips.
- **Params reach SQL.**
  - `merged_under_cost {max_usd: 5}` counts a $3 merge, and the default does not.
  - Two achievements on one metric with different params count apart.
  - The facts SQL text contains no pack value.
- **Timezone.**
  - A merge at 23:30 UTC on 24 December is a Christmas merge in `Europe/Berlin`, and not in UTC.
  - A hot streak's day moves with the zone.
  - The new-year rare follows the zone.
- **Categories.**
  - The classifier table, re-pointed at categories.
  - The legacy table covers every key the old classifier can emit, enumerated from the old code's outputs.
  - The old and new key sets are disjoint.
  - A pack that maps `shell:test-runner` to a renamed skill re-prices a pre-change trace.
- **The dead branches.** The events query returns none of them, and the golden test holds.
- **Frontend.**
  - A fixture pack with renamed tiers, a fifth group and a new skill: the pages show its names, the new skill wears its cloak colours, and the chat box uses its templates.
  - An unknown gear step, rare art or icon falls back.
  - The art contract is pinned on both sides.

**PR 2**

- **Migration 130.** It is the last entry, a hand-over from 127's test (or from whichever is newest at merge). There is one active version and one draft at most.
- **Lifecycle.**
  - Draft, preview and activate;
  - restore makes a new version;
  - reset follows the default;
  - versions are trimmed to 100.
- **Following the default.** With no row, a changed shipped default is what `GET /progression` serves.
- **API.**
  - The 422 shape;
  - `dry_run` saves nothing;
  - writes are admin-only;
  - each write leaves an audit row.
- **MCP and CLI.**
  - MCP refuses a write without `confirm`.
  - `check` exits 1 with the API's messages.
  - `apply` of an unchanged file makes no version.
- **Startup upgrade.** A stored older schema becomes a new version with source `upgrade`.

**PR 3**

- **Equivalence.** For generated histories, the rollup plus the tail equals a full recompute: XP per character and skill, level-ups with their exact times, drops, and achievement unlocks. This is checked after a build, after an advance across several days, and across a pack activation.
- **Late events.**
  - An event inside the 48-hour window counts at once.
  - One older than the window counts after the nightly rebuild, and the rollup times show the staleness.
  - A quest reward that moves into the past forces a rebuild.
- **Cost.** A steady-state read issues no query for traces older than the boundary, shown with a spy on the repository. A benchmark outside the unit suite, on 100,000 seeded traces, reports the read time before and after in the PR.
- **Concurrency.** Two readers build once.

Each PR also runs the full unit suite against its own Postgres database, plus `ruff check` and `ruff format --check`. A PR that touches the frontend also runs `npm run lint`, `npx tsc -b` and `npm run test`.

## Decisions taken in this spec

These are cheap to reverse, so they were decided here:

- One tier ladder for gear and achievements. A tiered achievement has one threshold per tier.
- Achievement ids stay. Metric ids keep their names where nothing about them changes in kind; the merged or generalised ones get capability names (the table in section 2).
- Messages are rendered on the client, and the server validates their placeholders, in one grammar for both sides.
- `labels` is a fixed key set, and page chrome is not in the pack.
- `account.cloak_trim` is the capstone's trim; `divine_trim` stays beside it for one release.
- The world's lead cap stays at 126 (section 4).
- Art references are validated (a 422), and the frontend falls back anyway.
- Mastery cloaks come from pack colours. There is no equipment section.
- Action categories go in the existing `traces.skill_actions` column, with a read-time legacy table and no data rewrite.
- A stored pack is upgraded at startup; a write must carry the current schema version.
- Pack writes are admin-tier; reads are read-tier.
- The rollup is stored, with a 48-hour settle window, a nightly full rebuild and exact milestones. It is keyed by the effective pack's hash, so a release that changes a followed default rebuilds it.
- The timezone is in the pack and defaults to UTC.
- Who an outcome credits stays in SQL until #167.

## Decisions

Answered by the maintainer on 2026-10-28, each as recommended:

1. **One pack per deployment.** Agent definitions and the account are deployment-wide, and jobs without a repository have no organization. A per-organization pack would need an account per organization; if #161 ever asks for it, `progression_packs` gains an `owner` column.
2. **The pack is its own document**, following the settings document's conventions (#160): YAML text kept with its comments beside the parsed document, the 422 list naming every key, `?dry_run=1`, the revision sources, and #162's CLI verbs. It does not import from the settings code: PR 1 does not depend on #160, and PR 2 copies its conventions, not its modules.
3. **A stored draft, a preview and an activate step**, plus a one-shot PUT for the CLI and MCP.
4. **A customised pack is a whole document**, and the dashboard shows when the shipped default has moved on since it was based on it.

The Holiday Event and the new-year rare keep their two different rules (section 1).

## 11. What PR 1 settled

PR 1 reads everything from the shipped pack, with no visible change and no migration. Under the shipped pack `/progression` answers a seeded history field for field as before (`tests/unit/services/test_progression_golden.py`, recorded from the code before PR 1), and the outfits contact sheet regenerates byte for byte. One look differs: the Divine tier's colour is now the hex its CSS variable held (`#d4af37`), so its achievement icon takes the same 18% lift every other tier's icon takes.

**Interfaces PR 2 and PR 3 build on.**

- **The schema and loader: `embry0/services/progression_pack.py`.**
  - `load_text(text)` reads YAML or JSON (aliases refused, 256 KiB at most).
  - `parse(data)` and `parse_text(text)` return a `Pack` or raise `PackInvalid`, whose `problems` are `Problem(loc, type, msg)` with `to_dict()` in the 422 shape.
  - `cross_check(pack)` and `warnings(pack)` are the two passes after the shape.
  - `default_text()` and `default_pack()` are the shipped default; `pack_hash(pack)` is a pack's identity, the same for the same values whatever the comments.
  - `contract()` and `--write-contract` regenerate `docs/contracts/progression.json`.
- **The vocabulary: `embry0/services/progression_vocabulary.py`.** `METRICS`, `QUEST_KINDS`, `TASK_KINDS`, `OUTCOME_SOURCES`, `ACTION_KINDS`, `ACTION_KEYS`, `GEAR_STEPS`, `RARE_ART`, `ACHIEVEMENT_ICONS`, `MESSAGE_PLACEHOLDERS` and `LABEL_PLACEHOLDERS`; PR 2's `GET /progression/pack/vocabulary` serves them.
- **Where the active pack is read.** `ProgressionService(repository, *, packs: PackSource, ...)`. `PackSource` is a protocol with one method, `async def active(self) -> Pack`. `embry0/api/app.py` passes `ShippedPack()`, which always answers `default_pack()`; PR 2 replaces it with a stored source, and the argument is required, so a source is never defaulted by accident. On each recompute the service asks the source, and a pack whose hash differs from the cached one is recomputed at once, whatever the time to live. `ProgressionService.rules()` answers the active `Rules` for callers that price outside a recompute (the quest scroll, `api/v1/world.py`).
- **How pricing consumes it.** `rules_for(pack)` builds `Rules` once per hash. Every service takes `rules=` as a required keyword:
  - `price(event, rules=)` and `compute(events, agent_types, rewards, rules=)` in `services/progression.py`;
  - `first_total(level_ups, total, rules=)`;
  - `evaluate(facts, ..., rules=)` and `metric_steps(..., rules=)` in `services/achievements.py`;
  - `evaluate_diaries(journal, rules=)`, `quest_rewards(journal, rules=)` and `evaluate_quests(journal, diaries, total_reached, rules=)` in `services/quests.py`, where `total_reached(n)` answers when a character first reached total level n;
  - `quest_xp(job, runs, rules=)` in `services/world.py`.

  `Rules` holds the lookups: `skill_for_action` (category to skill), `action_xp`, `outcomes`, `run_outcomes`, `xp_for_level` and `level_for_xp`, `tier_for_level`, `character_level`, the rare calendar, `local(at)` on the pack's timezone, `achievements` (each with its `fact_key`), `fact_queries` and `view()`, which is the pack's part of `GET /progression`.
- **The facts query.** `ProgressionRepository.facts(queries, timezone)` runs `rules.fact_queries`; `facts_sql(queries, timezone)` builds it from `FRAGMENTS`, binding every parameter and the timezone. A fact's `metric` is its query's key, so two achievements that count one metric with different parameters count apart.

**Settled in review.** A review of PR 1 found validator gaps that PR 2's editing would have made live. They are fixed in PR 1, and PR 2 and PR 3 build on the result.

- **Texts.** `embry0/services/progression_templates.py` is the one grammar and the one formatter: `parse`, `fields`, `render`, `TemplateError` and the shared `CASES`. The validator uses it, and every server render goes through `render`; nothing calls `str.format` on a pack's text. The dashboard's `fill` matches it. `progression_vocabulary.placeholders` is gone. PR 2's editor and its 422 rely on this module and build no grammar of their own.
- **Bounds.** A pack cannot make a read fail or swell:
  - every parameter Postgres reads as `::int` is bounded to int32 (`healed.min_seen` at most 1,000,000), and a test tries each one;
  - the curve's `doubling` and `divisor` are at least 1, and its top stays under 2^53 - 1 XP;
  - an overflow, a document deeper than 32 levels and a parser recursion are each a `Problem`, not an exception;
  - a date must exist in some year (29 February does, 30 February does not).
- **Problems never echo the value written.** A message names the location and what is allowed, as the settings document's do. The `Problem(loc, type, msg)` shape is unchanged.
- **The facts query** binds every parameter of a metric. `rules.fact_queries` carries each metric's full `model_dump()`, defaults included, while `fact_key` still leaves them out. `facts_sql` raises `KeyError` on a parameter that is missing, rather than comparing with NULL and counting nothing.
- **Explicit dispatch.**
  - Quests dispatch on `QUEST_KINDS[kind].capstone`: `quests._KINDS` holds every other kind's evaluator.
  - Diary tasks dispatch through `quests._TASKS`.
  - The metrics with no fragment are `achievements.DERIVED_METRICS`.
  - Tests pin each of these to the vocabulary, and pin every fragment's placeholders to its metric's parameters.
- **The timezone.** The pack's timezone is checked with Python's timezone database, and Postgres applies it from its own. If the facts query fails under the pack's timezone, the service logs `progression_facts_timezone_fallback` and reads the facts on UTC's calendar, rather than failing `/progression`; a failure under UTC is raised. **PR 2 must also check a saved timezone against Postgres** (`SELECT now() AT TIME ZONE $1`), so the fallback stays a safety net.
- **The dashboard against an older backend.** `Character.gear` and `Account.cloak_trim` are optional. Without the pack fields:
  - a tier or diary tier reads as its id written as words ("Gold");
  - a label reads as its key written as words, after its values ("Gold gear");
  - the skill groups come from the skills;
  - the outfit wears the tier's gear;
  - the chat box shows nothing and marks nothing seen.

**What PR 3's rollup needs to know.**

- **The key is `rules.hash`.** It changes with any value of the pack (rates, skills, the category map, the timezone, a tier's level, a rare's date), and not with comments. The rollup's day is `rules.local(at).date()`.
- **The price of one event is `price(event, rules=rules)`.** The fold is `compute()`'s loop: events and quest rewards sorted by time, `gain()` per (skill, XP), level-ups and character level-ups recorded as they cross. The rares are decided in the same loop from `rules.rare_on_date` and `rules.rare_first_run_of_year` on the pack's calendar, so the rollup must fold them too, with `last_run_year` carried across days.
- **Events come from `ProgressionRepository.events()`** with nine sources (`run`, `merged`, `opened`, `cleaned`, `comeback`, `resumed`, `qa_passed`, `audited`, `asked`; the three dead ones are gone). A run's event time is `traces.created_at`, the start of the run, written when it starts and finished later. Its keys may be categories or older skill keys; `run_actions()` translates them.
- **Quest rewards feed the levels:** `quest_rewards(journal, rules=)`, each at its quest's completion, which a late merge can move into the past.
- **The growth achievements and the capstone read the level-ups,** so stored milestones must keep their exact times; `test_progression_golden` and an equivalence test are the guards.
- **`Character` and `Progression` take `rules=`;** `Progression.rules` is the pack it was computed under.

## 12. What PR 2 settled

PR 2 stores the pack and lets an admin change it, as section 3 describes. Where it is more specific:

- **Versions are never reused.** `progression_packs.version` is an identity column. Saving a draft replaces the old draft row with a new one under the next number, so an `If-Match` naming a replaced draft never matches the new one. A version that follows the default holds no text (a second CHECK).
- **The active version is always the newest, and a draft knows what it was saved over.** Activating a draft copies it into a new active row under the next number and deletes the draft, so the history and the `ETag` only rise. Every row records `parent_version`, the active version it was written over. A draft whose parent is no longer the active version is a 409 at activation, so it cannot undo a newer activation unseen; the dashboard previews it again, and saving it again over the pack in effect clears it.
- **Reads after a database error.** The stored source keeps the last pack it read. With none read yet (just after a boot), it raises like any other database read, rather than serve the shipped default to a deployment that customised its pack.
- **Shared with the settings document.** One module, `embry0/text_documents.py`, refuses aliases, merge keys, keys that are not text and keys written twice for both documents (the pack's `load_text` included, so the shipped default and both `--check` commands agree with every write), and a JSON body that names a key twice. One, `embry0/api/document_requests.py`, parses `If-Match` and reads a body with a cap, refusing one past 256 KiB as it streams, `Content-Length` or not. Who changed either document is shown as `GET /audit` shows actors (`embry0/api/actor_visibility.py`).
- **The defaults a pack was based on are kept.** `progression_pack_defaults (hash, text)` keeps the text of each shipped default a stored pack was based on, written when the pack is saved, so a later release can show the diff between the two defaults (`GET /progression/pack/default/diff`). `based_on` is this release's default when the text is the default or the write says `?rebase=1`; otherwise it stays what the active customised pack was based on. A rebase is how an operator clears the notice after taking in the new default.
- **`If-Match` per route.** The draft's version for the draft routes and the activation; the active version for the one-shot `PUT`, restore and reset. `"0"` means nothing stored.
- **Two more routes and one more audit action.** `GET /progression/pack/versions/{version}` serves one version's text for the history, and `GET /progression/pack/default/diff` the diff above. Discarding a draft writes `progression.pack_draft_deleted`.
- **More refusals on a write.** A key written twice and a YAML merge key are 422s (`structure_problems` in `progression_pack_store.py`), as for the settings document. So is a `timezone` Postgres does not know: Python's `zoneinfo` and Postgres read different tz databases (Python knows `localtime`, Postgres does not), and the facts query binds the timezone.
- **A refused stored pack falls back.** A stored version the running release refuses is never served. The shipped default is, the orchestrator logs `progression_pack_stored_invalid` once per version, and `GET /progression/pack` lists the problems in `fallback`. A database error keeps the last pack read.
- **The upgrade chain is empty.** `UPGRADES` maps a schema version to its step. It holds none while the schema is at version 1; the startup upgrade is tested with an injected step.
- **The preview** answers `GET /progression`'s shape under the draft, plus `changes` (`characters[]` with `before` and `after` of `level`, `total_level` and `xp`; the account's totals; `achievements_unlocked`). One runs at a time; another gets a 429.
- **`GET /progression`'s `pack`** gains `revision`, the stored version in effect (0 while nothing is stored), and `follows_default`.
- **The UTC fallback is narrow and visible.** PR 1 read the facts again on UTC after any failure under the pack's zone. Now only Postgres's refusal of the zone (SQLSTATE 22023, `invalid time zone`) falls back, and any other failure, such as a statement timeout, fails the read. `pack.timezone_applied` names the calendar the facts were read on, and the Game content tab and the Achievements page say when it is not the pack's.
- **The timezone on the Achievements page** is named once, in the note on secret achievements, rather than beside a category: a pack can rename or drop the oddities category, and every oddity in the shipped pack is secret.
