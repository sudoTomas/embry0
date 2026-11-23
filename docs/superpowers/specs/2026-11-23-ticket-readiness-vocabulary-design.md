# Ticket readiness in each organization's words

Status: decided (2026-11-23); pull request 1 builds it. Refs #168, #159. Line numbers are at `67b9580e`.

**Why.** The backlog intake (spec 2026-10-25) offers a ticket nobody sent to embry0 when it reads as ready: a 0 to 100 score from the ticket alone, at least `leads.offer_min`. The thresholds became settings with the `leads` section (#168, spec 2026-10-28), but the words the score reads stayed in the code (`embry0/services/leads/backlog.py:32-126`): English labels such as `bug`, `chore` and `epic`, the points of each signal, the phrases that say what done looks like, the title that makes a ticket an epic and the estimates that read as small or large. Organizations label and write their tickets their own way (`kind/bug`, `type::bug`, `P2`, another language), so a deployment serving several of them reads all but one of them wrong, and the only remedy is a code change. Epic #159 says an operator's decision is a document in the database with a schema, a 422 naming each problem and a write path through the API, the CLI, MCP and the dashboard.

**What changes.** The vocabulary becomes keys of the `leads` section, each an organization's layer may set, with the code's values as defaults, so a deployment that writes none scores every ticket as it did. The loop builds one reading of tickets for each repository from its policy and hands it to both backlog finders. The readiness table in `docs/leads.md` is generated from the defaults.

## 1. What exists today

- `readiness()` (`backlog.py:93-126`): +5, +20 or +25 for a description (blank, from 80 and from 400 characters), +20 for a file path, +10 for error text, +15 for a done phrase, +10 for a ready label, +10 small and -10 large (by label, or an estimate at most 2 or at least 5), -30 for a not-ready label or else an epic by its title, -10 for a title of three words or fewer; kept within 0 to 100.
- The label sets (`:43-79`): ready, easy, large, not ready, and urgent and high (`:81-82`), which set a lead's severity when the tracker gives no priority (`severity_of`, `:138`). `effort_of` (`:129`) reads the same size labels and estimates.
- The done pattern (`:39-42`) and the epic title pattern (`:80`), both `re` patterns with `IGNORECASE`.
- The loop passes only `offer_min` to `linear_leads` and `find_github_tickets` (`loop.py:218-234`).
- `docs/leads.md` describes the score by hand and says the vocabulary moves later (#168).

## 2. Decisions

- **D1.** The one-time imports of the old `LEADS_*`, `SCOUT_*` and `PROPOSALS_*` variables are removed before 0.1.0 is tagged, with a one-release warning while a variable is still set. A later pull request; not before the `agent_questions` import of #179 has been deployed once.
- **D2.** Flat keys in `leads`, not a nested `readiness` object and not a section of their own: a layer replaces a key whole, so a nested object would have to be restated to change one label, and the vocabulary sits beside `offer_min` and `ask_min`, which read the same score.
- **D3.** Each label role is replaced whole by an organization's layer, and the roles it does not name are inherited.
- **D4.** Organization layer only. Which repositories a background feature reads is the repository scope rule's (#165), and the section has no repository keys.
- **D5.** Done phrases are literal, case-insensitive phrases matched at word boundaries, in any language.
- **D6.** A change reaches leads at the next backlog intake (`backlog_interval_hours`, 24 hours by default) or at once by `POST /api/v1/leads/run` with `"backlog": true`. Nothing else is rescored when the document changes.

## 3. The keys

| Key | Default | What it is |
|---|---|---|
| `readiness_labels` | the six label sets of `backlog.py` | role to labels; roles `ready`, `small`, `large`, `not_ready`, `urgent`, `high` |
| `readiness_weights` | the points of `readiness()` | signal to points; signals `description_short`, `description`, `description_full`, `paths`, `error`, `done`, `ready_label`, `small`, `large`, `not_ready`, `short_title` |
| `done_phrases` | the done pattern, written as phrases | phrases that say what done looks like |
| `epic_title_words` | `[epic]` | words that make an unlabelled ticket an epic by its title |
| `small_estimate_max` | `2` | an estimate at most this reads as small |
| `large_estimate_min` | `5` | an estimate at least this reads as large |

- Every key is an organization key. `readiness_labels` and `readiness_weights` join `ask_severities` in `merged_keys`: the deployment's document merges each over its default and an organization's layer over the deployment's, a role or signal it names replacing that entry (D3). The two lists and the two estimates are replaced whole, like every other key.
- Nothing is imported from the environment: the vocabulary was code, so every deployment already scores with the defaults.
- File paths and error text stay in the code. They are code artifacts, not language.
- The description cut-offs (80 and 400 characters) and the short title (three words) stay constants: they measure the ticket, not its words.
- Two estimates that overlap are not refused: a ticket whose estimate reads as both small and large takes both signals and the effort of a large one, which is what the code did with labels of both roles.

## 4. Validation

A write names every problem of every key at once in its 422 and never repeats a value. Each vocabulary key is checked whole before its type, so a value of the wrong shape inside it (a weight that is not a whole number, a role's labels that are not a list, a phrase that is not text) is one problem among the key's others rather than the only one reported, and a 422's place never holds a role or signal the operator wrote. Each problem names the role or signal it belongs to, which is a catalogue word, or says only what kind of value is wrong:

- a value that is not a mapping (the two mappings) or a list (the two lists);
- a role or signal outside the catalogue (the message lists the catalogue);
- a weight that is not a whole number (a boolean is not a number), or outside -100 to 100;
- a role whose labels are not a list;
- for each role's labels, the phrases and the title words: more than 64 labels a role, 100 phrases or 32 words; one that is not text; a label of more than 64 characters, a phrase of more than 80 or a word of more than 40; an empty one; one that is not one line of plain text (a control, format or separator character, the categories `refuse_unprintable` names); and the same one twice, compared as they are matched (below);
- an estimate below 0 or above 1000.

Labels are stored as written. A key the YAML reads as a number (`7: [x]`) is refused by the document's loader before any section sees it; the loader now places that problem at the mapping that holds the key, once, instead of under the key it would otherwise repeat (`embry0/settings/document.py`, for every section).

## 5. Matching

**Folding.** Labels, phrases, title words and the ticket's own text are compared in Unicode's canonical caseless form: decomposed, case-folded, then composed (NFC). So `STRASSE` reads `straße`, and an accent written as one character reads the same as a letter followed by a combining mark, in a label, a phrase or a description.

**Labels** are a set intersection of folded, trimmed names.

**Word characters.** A word character is a letter or a digit of any script (`str.isalnum`, which is `re`'s `\w` without `_`) or a combining mark (Unicode category M: an accent written as a mark, an Indic vowel sign or virama). `\w` leaves the marks out, so `re`'s `\b` finds a boundary inside a word written with them: `स्वीकृ` would read inside `स्वीकृति`. `_` is punctuation: Markdown emphasis (`__Acceptance criteria__`, `_Done when_`) must not hide a phrase. A script written without spaces (Chinese, Japanese, Thai) has no boundaries between its words, so a whole run of its letters is one word and a phrase in it reads only where a space or punctuation sets it off. The set is read from every code point once per process, when the module is imported (about a tenth of a second).

**Done phrases.** Building a pattern of escaped alternatives, as the brief proposed, keeps ticket text from mattering, but not the phrases: `re` tries every alternative at every position, so a hundred phrases of 80 characters that each nearly match cost about a hundred times eighty steps a character, and RE2 (`embry0/services/linear_regex.py`) refuses such a program as too large once `\W` boundaries are written out for Unicode. So no pattern is built from what an operator writes. The folded text and each folded phrase are split on runs of word characters by one fixed pattern and joined again with a mark (NUL, which the section refuses in a phrase; a NUL in the text is read as a space first) between the parts, which puts a mark at each edge of each run and nowhere else. A phrase then occurs at word boundaries exactly where its marked form is a substring of the marked text: a mark the phrase holds next to a word character can only meet a mark of the text, because the text holds a mark between any non-word character and a word character. Where a phrase starts or ends with a non-word character (`- [ ]`), it holds no mark there and reads anywhere, as before. `str.__contains__` takes time about linear in the text whatever the phrase. Marking costs about 10 ms for 64 KiB of prose and 25 ms for 64 KiB of one-letter words, the worst case (the code before read such a description in about 8 ms); a hundred phrases of 80 characters that each nearly match add a few milliseconds.

**What the defaults read differently.** The pattern before found some of its words inside longer ones, and counted `_` as a letter after `should`. A probe scoring 927,420 generated tickets (every default phrase, near-misses, cut-off lengths, labels, estimates, epic titles) with the code on main and with the defaults differs in eight phrases and nothing else: the done signal alone, with every other reason, score part and effort the same.

- No longer done, a phrase inside a longer word: `acceptance criterias`, `nonacceptance criteria`, `undone whenever`, `Expected behaviorally`, `expected resultant`, `steps to reproduced`, `mustshould`.
- Now done, a phrase set off by underscores: `should_be`, where the old `should\b` saw no boundary before the underscore.

The defaults keep every form the pattern read as a whole word (`expected behavior`, `expected behaviors`, `expected behaviour`, `expected behaviours`, `expected result`, `expected results`). The golden set holds no ticket of the eight, and tests pin both directions.

**Epic titles.** The pattern before, with the alternation of the escaped, folded words where it said `epic`, matched on the folded title. The run of spaces after a scope moved inside its group, so one run of spaces between the word and the colon is tried once instead of split between two `\s*`. An empty list reads no title as an epic.

## 6. The loop

`LeadsLoop._tickets` takes the repository's policy (its organization's layer over the deployment's document) and builds `Readiness.of(policy)` once for the repository, passing it with `offer_min` to `linear_leads` and to `find_github_tickets`. The reading is a required argument of both finders and of `ticket_lead`: a finder cannot run with a vocabulary nobody chose. A lead that now scores below `offer_min` is not in the result of a complete pass, so the existing `mark_gone` closes it.

## 7. Documentation

`python -m embry0.services.leads.readiness_reference --write docs/leads.md` writes the readiness table between its markers from the defaults: each signal with its points and when it counts, each role with its labels, the done phrases and the epic title words. A test fails when the committed table differs, and another shows the check failing when a default changes. `embry0.settings.reference.splice` and `committed_block` take the markers, so the two generated blocks share one implementation. The settings reference and the example document are regenerated as usual.

## 8. Testing

- **Golden.** A fixed set of 47 tickets (descriptions on both sides of each length cut-off and one of nothing but spaces, paths, errors, every default label role, epic titles in brackets, with a scope and with a dash, estimates, priorities, labels in other cases and with spaces, done phrases inside Markdown emphasis, and a ticket in another language) with the score, reasons, effort and severity the code on main gave each, recorded by running that code over the set. The default section must read each the same.
- **An organization's words.** Its own labels, weights, phrases, title words and estimates decide its readings, through the settings documents as the loop reads them; a role it names is replaced whole and the others inherited; a signal it weighs at nothing gives no reason.
- **The loop.** One organization naming its own ready label is offered its tickets from Linear and from GitHub at a threshold the default words do not reach, another organization is not, and weighing that label at nothing closes both leads on the next intake. This test fails on the tree before this change with its own message.
- **Phrases.** Word boundaries in Latin, Cyrillic, Devanagari and CJK text, underscores as punctuation, accents written either way, case folding (`STRASSE` reads `straße`), literal regex metacharacters, and a hundred adversarial phrases on a 64 KiB description well inside a second.
- **Validation.** Every bound refused naming its key, all problems at once, nothing echoed, through the section and through `PUT /api/v1/config/settings`.

## 9. Pull requests

1. This change: the keys, the reading, the loop, validation, the generated table and this spec.
2. The brief writer's cadence (`PROPOSAL_BRIEF_INTERVAL_SECONDS`, `PROPOSAL_BRIEFS_PER_TICK`) becomes constants, with a `settings_env_retired` warning while either variable is set, as the scout's tick did.
3. The one-time imports of D1 are removed, after #179's import has been deployed once.
