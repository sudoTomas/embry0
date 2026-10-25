# Watcher efficiency: stop paying for what was already read, and stop waiting on a yes that never comes

**Goal.** Most of what embry0 does on its own is the log watcher, and most of that is paid analyses that find nothing: on one stack, 74 of the 74 analyses in three days came back `no_issue`. Meanwhile the drafts it does file wait at `tracked` for a person, the oldest for weeks, and the context-audit schedule refuses its own jobs. Three changes, one branch.

## 1. The schedule is not throttled by the hourly limit

`embry0:context-audit` joins the actors exempt from the per-author hourly rate limit (`dispatch_actors.RATE_LIMIT_EXEMPT`). The limit counted the schedule's own burst of starts and refused jobs the gate had just admitted (29 such refusals in three days), and each refused repository then waited a full interval. The schedule keeps its own brakes: at most a few jobs per tick, one live job per repository, a hard per-job budget per organization. The daily and monthly spend caps still apply. ADR 0020 carries the amendment. The Linear reconciler stays limited, as does the new auto-accept actor below.

## 2. The novelty gate holds what an analysis found clean a moment ago

The watcher already skips a window with nothing new, spiking or regressed, or whose notable signatures all have an open case (#291, `WATCHER_SKIP_UNCHANGED_MODE`). Two things kept it from saving much:

- **Signatures fragmented.** Upper-case record ids such as `NF42610190220` passed every mask (the opaque-token rule needs lower case), so one recurring warning became a new signature per order number: on one stack 175 of the 218 signatures first seen in three days were that warning, and a new signature is always notable. A rule now masks an upper-case token of at least 8 characters with at least 5 digits; codes such as `E11000`, `SHA256`, `PGRST002`, `HTTP2` stay. Templates already stored keep their old ids; the next window records the merged ones (a one-time burst of "new").
- **A known warning that comes and goes is "regressed" on every return,** and was analysed each time.

Each analysis now records, per signature in its window, what it found and the signature's count there (`log_signatures.analysed_at`, `analysed_verdict`, `analysed_count`, migration 111): `no_issue`, or `proposed` when it proposed anything. A notable signature whose last analysis, within `skip_known_hours` (default 24, per stack; 0 off), found no issue is held, unless its count now exceeds `spike_factor` times what that analysis read. When every notable signature is held so or has an open case, the tick is `skipped_known`: no job, the window consumed like `no_change`. A memory that cannot be read holds nothing: the window is analysed.

The gate follows `skip_unchanged_mode` (`shadow` by default, per stack): in `shadow` the decision is logged and the analysis still runs. A stack turns it on with `{"settings": {"skip_unchanged_mode": "on"}}`.

## 3. Auto-accept of high-confidence drafts

Per stack, off by default (`auto_accept`). Each tick, after reconciling drafts a person cancelled, the stack's tracked drafts are taken oldest first; one is accepted through the path `POST /cases/{id}/accept` takes when it has waited `auto_accept_after_hours` (24), has severity at least `auto_accept_min_severity` (`high`) and a signature seen at least `auto_accept_min_occurrences` (10) times, and fewer than `auto_accept_max_per_day` (2) were auto-accepted in the last 24 hours (the jobs `embry0:auto-accept` dispatched). Left for a person: a retry after a `not_healed` verdict, and a draft an owner declined or snoozed as a proposal. Each acceptance writes a `case_auto_accepted` audit row as `embry0:auto-accept`; the actor is rate-limited and the spend caps apply. A failing acceptor never costs the tick.

## Tests

The exempt actor and the caps it keeps; the record-id mask and the codes it leaves; the per-signature verdict memory against Postgres; the gate holding a returning clean signature, analysing one that outgrew its clean count, analysing when the memory is unreadable or the hours are 0, and only logging in shadow; the verdict recorded after an analysis; the auto-accept bar, the oldest-first order, the day's cap, answered proposals; the tick asking only a stack that turned it on, after the reconcile; the app's acceptor going through the case route as its own actor with its audit row; the wiring.
