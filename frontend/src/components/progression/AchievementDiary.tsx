import type { AchievementEntry, AchievementUnlock, Progression } from "@/api/progression";
import { AchievementIcon } from "@/components/progression/achievementIcons";
import { STATE_FILTERS, type StateFilter } from "@/components/progression/diaryFilters";
import {
  COMPLETE_COLOR,
  COMPLETE_NAME,
  TIER_COLOR,
  TIER_NAME,
  TIER_ORDER,
  achievementColor,
  achievementTierName,
  readable,
} from "@/lib/tiers";
import { cn, formatDate } from "@/lib/utils";

const number = (n: number) => n.toLocaleString("en-US");

/** A small square-cornered label, the old game's interface chips: border and text in one colour. */
function Chip({ color, children, testId }: { color: string; children: React.ReactNode; testId?: string }) {
  return (
    <span
      data-testid={testId}
      className="inline-flex items-center gap-1 whitespace-nowrap border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider"
      style={{ borderColor: color, color: readable(color) }}
    >
      {children}
    </span>
  );
}

/** The tier ladder an achievement's next step is on: the colour of the tier it is working towards. */
function nextTier(entry: AchievementEntry): { name: string; color: string } | null {
  if (entry.next_threshold === null) return null;
  if (entry.kind === "objective") return { name: COMPLETE_NAME, color: COMPLETE_COLOR };
  const tier = TIER_ORDER[entry.tiers_reached];
  return { name: TIER_NAME[tier], color: TIER_COLOR[tier] };
}

function AchievementCard({ entry, categoryName }: { entry: AchievementEntry; categoryName: string }) {
  const hidden = entry.state === "secret";
  const unlocked = entry.state === "unlocked";
  const color = achievementColor(entry.tier);
  const next = nextTier(entry);
  // The bar runs from the tier reached to the next one, so each tier starts empty.
  const floor = entry.thresholds && entry.tiers_reached > 0 ? entry.thresholds[entry.tiers_reached - 1] : 0;
  const pct =
    entry.next_threshold === null
      ? 100
      : Math.max(0, Math.min(100, ((entry.value - floor) / Math.max(1, entry.next_threshold - floor)) * 100));
  // The bar wears the tier reached, as the legend says; before the first tier, the one it is working towards.
  const barColor = entry.tier ? color : (next?.color ?? color);
  return (
    <article
      className={cn("flex flex-col gap-3 border p-4", unlocked ? "bg-white/[0.035]" : "bg-white/[0.012]")}
      style={{ borderColor: unlocked ? `color-mix(in srgb, ${color} 55%, transparent)` : "var(--color-border)" }}
      data-testid="achievement"
      data-state={entry.state}
      data-category={entry.category}
    >
      <div className="flex items-start gap-3">
        <span
          className="flex h-14 w-14 shrink-0 items-center justify-center border"
          style={{
            borderColor: unlocked ? color : "var(--color-border)",
            background: unlocked ? `color-mix(in srgb, ${color} 16%, transparent)` : "rgba(255,255,255,0.03)",
            opacity: unlocked ? 1 : 0.6,
          }}
        >
          <AchievementIcon name={hidden ? null : entry.icon} color={unlocked ? color : "#77737f"} size={40} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className={cn("title-pixel text-sm leading-tight", hidden ? "text-white/40" : "text-white/90")}>
            {entry.name}
          </h3>
          <p className="mt-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">{categoryName}</p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Chip testId="state-chip" color={unlocked ? color : "#77737f"}>
            {entry.state}
          </Chip>
          {entry.tier ? (
            <Chip testId="tier-chip" color={color}>
              {achievementTierName(entry.tier)}
            </Chip>
          ) : next ? (
            <Chip testId="tier-chip" color={next.color}>
              target {next.name}
            </Chip>
          ) : null}
        </div>
      </div>

      <p className="text-xs text-white/70">
        {hidden ? "A secret. It shows what it is once embry0 sees its first sign." : entry.flavour}
      </p>

      {!hidden && (
        <details className="border border-border px-2 py-1 text-xs" data-testid="what-counts">
          <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-wider text-white/60">
            What counts
          </summary>
          <p className="mt-1 text-white/80">{entry.what_counts}</p>
          {entry.thresholds && (
            <p className="mt-1 font-mono text-[10px] text-white/50">
              {entry.kind === "objective"
                ? `Complete at ${number(entry.thresholds[0])}`
                : entry.thresholds.map((t, i) => `${TIER_NAME[TIER_ORDER[i]]} ${number(t)}`).join(" · ")}
            </p>
          )}
        </details>
      )}

      {!hidden && (
        <div className="mt-auto space-y-1.5">
          <p className="truncate text-[11px] text-muted-foreground" data-testid="evidence">
            <span className="mr-1.5 font-mono text-[10px] uppercase tracking-wider text-white/50">Evidence</span>
            {entry.evidence?.label ? (
              entry.evidence.url ? (
                <a className="underline hover:text-fg" href={entry.evidence.url} target="_blank" rel="noreferrer">
                  {entry.evidence.label}
                </a>
              ) : (
                entry.evidence.label
              )
            ) : (
              "nothing yet"
            )}
            {entry.evidence?.at && ` · ${formatDate(entry.evidence.at)}`}
          </p>
          <div className="flex items-center gap-2">
            <div
              className="h-1.5 flex-1 bg-white/10"
              role="progressbar"
              aria-label={next ? `Progress to ${next.name}` : "Every tier reached"}
              aria-valuenow={Math.round(pct)}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="h-full"
                style={{ width: `${pct}%`, backgroundColor: barColor, opacity: entry.tier ? 1 : 0.6 }}
              />
            </div>
            <span className="font-mono text-[11px] text-white/70" data-testid="achievement-progress">
              {entry.next_threshold !== null
                ? `${number(entry.value)} / ${number(entry.next_threshold)}`
                : entry.kind === "objective" && entry.value > 1
                  ? `done ×${number(entry.value)}`
                  : entry.kind === "objective"
                    ? "done"
                    : `${number(entry.value)} · every tier`}
            </span>
          </div>
        </div>
      )}
    </article>
  );
}

function Tile({ label, value, sub, color }: { label: string; value: string; sub: string; color?: string }) {
  return (
    <div className="border border-border bg-white/[0.02] p-3" data-testid="diary-tile">
      <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">{label}</p>
      <p className="title-page mt-1 truncate text-lg" style={color ? { color: readable(color) } : undefined}>
        {value}
      </p>
      <p className="mt-0.5 truncate text-[10px] uppercase tracking-wider text-muted-foreground">{sub}</p>
    </div>
  );
}

/**
 * The achievement diary (spec 2026-10-25), in the Hermes achievements
 * plugin's order: what it is, the totals, what the tiers mean, the filters,
 * what was unlocked lately, then one card per achievement.
 */
export function AchievementDiary({
  data,
  category,
  state,
  onCategory,
  onState,
}: {
  data: Progression;
  category: string;
  state: StateFilter;
  onCategory: (id: string) => void;
  onState: (state: StateFilter) => void;
}) {
  const entries = data.achievements;
  const categories = data.achievement_categories ?? [];
  const categoryName = new Map(categories.map((c) => [c.id, c.name]));
  const count = (s: AchievementEntry["state"]) => entries.filter((e) => e.state === s).length;
  const unlocks = data.recent.filter((r): r is AchievementUnlock => r.kind === "achievement");
  const latest = unlocks[0];
  const highestIndex = Math.max(
    -1,
    ...entries.map((e) => (e.tier && e.tier !== "complete" ? TIER_ORDER.indexOf(e.tier) : -1)),
  );
  const highest = highestIndex >= 0 ? TIER_ORDER[highestIndex] : null;
  const shown = entries.filter(
    (e) => (category === "all" || e.category === category) && (state === "all" || e.state === state),
  );
  // Recent unlocks: the newest tier of each achievement, at most six.
  const seen = new Set<string>();
  const recentChips = unlocks.filter((u) => !seen.has(u.id) && seen.add(u.id)).slice(0, 6);
  const iconOf = new Map(entries.map((e) => [e.id, e.icon]));

  return (
    <section className="space-y-4" data-testid="achievement-diary" aria-labelledby="diary-title">
      <div
        className="border border-border p-5"
        style={{
          background:
            "linear-gradient(135deg, color-mix(in srgb, var(--color-palette-gold) 10%, transparent), transparent 60%)",
        }}
      >
        <p className="font-mono text-[10px] uppercase tracking-[0.25em] text-muted-foreground">Achievement diary</p>
        <h1 id="diary-title" className="title-page mt-1 text-3xl font-bold text-white sm:text-4xl">
          <span className="normal-case">embry0</span> Achievements
        </h1>
        <p className="mt-2 max-w-3xl text-sm text-fg-secondary">
          Badges your agents earn from what actually happened: merges that landed, errors that stayed dead, issues
          finished, claims corrected. Discovered ones show what to chase next; secrets stay hidden until embry0 sees
          their first sign.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <Tile label="Unlocked" value={`${count("unlocked")} / ${entries.length}`} sub="earned at least one tier" />
        <Tile label="Discovered" value={number(count("discovered"))} sub="known, not earned yet" />
        <Tile label="Secrets" value={number(count("secret"))} sub="hidden until first sign" />
        <Tile
          label="Highest tier"
          value={highest ? TIER_NAME[highest] : "None yet"}
          sub="Bronze → Divine"
          color={highest ? TIER_COLOR[highest] : undefined}
        />
        <Tile
          label="Latest"
          value={latest ? latest.name : "Nothing yet"}
          sub={latest ? `${achievementTierName(latest.tier)} · ${formatDate(latest.at)}` : "go merge something"}
          color={latest ? achievementColor(latest.tier) : undefined}
        />
      </div>

      <div className="grid grid-cols-1 gap-2 lg:grid-cols-3">
        <div className="border border-border p-3 lg:col-span-2" data-testid="tier-legend">
          <h2 className="font-mono text-[11px] uppercase tracking-widest text-white/80">Tiers</h2>
          <ol className="mt-2 flex flex-wrap items-center gap-1.5">
            {TIER_ORDER.map((tier, i) => (
              <li key={tier} className="flex items-center gap-1.5">
                {i > 0 && (
                  <span aria-hidden="true" className="text-white/30">
                    →
                  </span>
                )}
                <Chip color={TIER_COLOR[tier]}>
                  <span aria-hidden="true" className="h-2 w-2" style={{ backgroundColor: TIER_COLOR[tier] }} />
                  {TIER_NAME[tier]}
                </Chip>
              </li>
            ))}
          </ol>
          <p className="mt-2 text-[11px] text-muted-foreground">
            Each tiered achievement climbs the metal ladder as its count grows; the colour on a card is the tier it
            reached. One-off objectives read{" "}
            <span style={{ color: readable(COMPLETE_COLOR) }}>{COMPLETE_NAME}</span> instead.
          </p>
        </div>
        <div className="border border-border p-3">
          <h2 className="font-mono text-[11px] uppercase tracking-widest text-white/80">Secret achievements</h2>
          <p className="mt-2 text-[11px] text-muted-foreground">
            A secret hides its name and what it counts. The first time embry0 sees a matching deed, the card turns into
            a normal one and shows its requirement.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1" role="group" aria-label="Category">
          {[{ id: "all", name: "All" }, ...categories].map((c) => (
            <button
              key={c.id}
              type="button"
              aria-pressed={category === c.id}
              onClick={() => onCategory(c.id)}
              className={cn(
                "border px-2 py-1 text-xs",
                category === c.id ? "border-white/60 bg-white/10 text-white" : "border-border text-white/60 hover:text-white",
              )}
            >
              {c.name}
            </button>
          ))}
        </div>
        <div className="flex gap-1" role="group" aria-label="State">
          {STATE_FILTERS.map((s) => (
            <button
              key={s}
              type="button"
              aria-pressed={state === s}
              onClick={() => onState(s)}
              className={cn(
                "border px-2 py-1 font-mono text-[11px] lowercase",
                state === s ? "border-white/60 bg-white/10 text-white" : "border-border text-white/60 hover:text-white",
              )}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {recentChips.length > 0 && (
        <div className="space-y-1.5" data-testid="recent-unlocks">
          <h2 className="title-page text-sm text-white/80">Recent unlocks</h2>
          <div className="flex flex-wrap gap-1.5">
            {recentChips.map((u) => {
              const color = achievementColor(u.tier);
              return (
                <span
                  key={u.id}
                  className="inline-flex items-center gap-1.5 border px-2 py-1 text-xs"
                  style={{ borderColor: color, color: readable(color) }}
                  title={`${achievementTierName(u.tier)} · ${formatDate(u.at)}`}
                >
                  <AchievementIcon name={iconOf.get(u.id) ?? null} color={color} size={14} />
                  {u.name}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {shown.length === 0 ? (
        <p className="text-sm text-muted-foreground">No achievement matches these filters.</p>
      ) : (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {shown.map((entry) => (
            <AchievementCard key={entry.id} entry={entry} categoryName={categoryName.get(entry.category) ?? entry.category} />
          ))}
        </div>
      )}
    </section>
  );
}
