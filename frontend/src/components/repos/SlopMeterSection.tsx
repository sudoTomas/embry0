import { useState } from "react";
import { Gauge } from "lucide-react";
import type { SlopMeterReading, SlopMeterWeek } from "@/api/slopMeter";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { Select } from "@/components/ui/Select";
import { useSlopMeter } from "@/hooks/useSlopMeter";
import { formatDateTime } from "@/lib/utils";

const LEVEL_TONE: Record<number, BadgeTone> = { 1: "success", 2: "info", 3: "warning", 4: "error", 5: "error" };

/** The three lines of the weekly chart, in the theme's palette. */
const SERIES: { key: "cost" | "capacity" | "slop"; label: string; colour: string }[] = [
  { key: "cost", label: "Attention cost", colour: "var(--color-palette-amber)" },
  { key: "capacity", label: "Human capacity", colour: "var(--color-palette-sky)" },
  { key: "slop", label: "Slop", colour: "var(--color-palette-rose)" },
];

const WIDTH = 600;
const HEIGHT = 140;

/** A week-by-week line of cost, capacity and slop in weighted lines, drawn as inline SVG. */
function WeeklyChart({ weeks }: { weeks: SlopMeterWeek[] }) {
  const top = Math.max(1, ...weeks.flatMap((w) => [w.cost, w.capacity, w.slop]));
  const x = (i: number) => (weeks.length < 2 ? WIDTH / 2 : (i / (weeks.length - 1)) * WIDTH);
  const y = (v: number) => HEIGHT - (v / top) * HEIGHT;
  const first = weeks[0]?.week;
  const last = weeks[weeks.length - 1]?.week;
  return (
    <figure className="space-y-1" data-testid="slop-meter-chart">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        className="h-36 w-full rounded-md border border-border bg-canvas"
        role="img"
        aria-label={`Weekly attention cost, human capacity and slop from ${first} to ${last}`}
      >
        {SERIES.map((s) => (
          <polyline
            key={s.key}
            data-series={s.key}
            fill="none"
            stroke={s.colour}
            strokeWidth={1.5}
            vectorEffect="non-scaling-stroke"
            points={weeks.map((w, i) => `${x(i).toFixed(1)},${y(w[s.key]).toFixed(1)}`).join(" ")}
          />
        ))}
      </svg>
      <figcaption className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-muted">
        {SERIES.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-4" style={{ backgroundColor: s.colour }} />
            {s.label}
          </span>
        ))}
        <span className="ml-auto font-mono">
          {first} – {last}, weighted lines a week
        </span>
      </figcaption>
    </figure>
  );
}

function Reading({ meter, scope }: { meter: SlopMeterReading; scope: string }) {
  return (
    <div className="space-y-1" data-testid="slop-meter-reading" data-level={meter.level}>
      <p className="flex flex-wrap items-center gap-2">
        <span className="text-lg font-semibold">{meter.name}</span>
        <Badge tone={LEVEL_TONE[meter.level] ?? "neutral"}>score {meter.score.toFixed(2)}</Badge>
        {meter.provisional && <Badge tone="neutral">provisional</Badge>}
      </p>
      <p className="text-sm text-fg-secondary">
        About {Math.round(meter.score * 100)}% of {scope}'s weighted lines landed beyond what its human contributors could
        attend to.
        {meter.provisional && " A large commit is not judged yet; its lines are left out until it is."}
      </p>
    </div>
  );
}

/**
 * The Slop-O-Meter (spec 2026-10-26): the repository's score and level, an
 * area's, and their weekly cost, capacity and slop. After Slop-O-Meter's
 * published slop score algorithm.
 */
export function SlopMeterSection({ repo }: { repo: string }) {
  const [area, setArea] = useState("");
  const meter = useSlopMeter(repo, area);
  const data = meter.data;
  const reading = area ? data?.areas.find((a) => a.area === area)?.meter : data?.meter;

  return (
    <section className="space-y-3" data-testid="slop-meter">
      <div className="flex flex-wrap items-center gap-2">
        <Gauge className="h-4 w-4 text-fg-muted" />
        <h2 className="text-section font-semibold">Slop-O-Meter</h2>
        {data && data.areas.length > 0 && (
          <Select
            aria-label="Area"
            className="ml-auto h-8 w-auto"
            value={area}
            onChange={(e) => setArea(e.target.value)}
            data-testid="slop-meter-area"
          >
            <option value="">The whole repository</option>
            {data.areas.map((a) => (
              <option key={a.area} value={a.area}>
                {a.area}
                {a.meter ? ` · ${a.meter.name}` : ""}
              </option>
            ))}
          </Select>
        )}
      </div>
      {meter.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : meter.isError || !data ? (
        <p className="text-sm text-muted-foreground">The Slop-O-Meter did not load.</p>
      ) : !reading ? (
        <p className="text-sm text-muted-foreground" data-testid="slop-meter-pending">
          Not scored yet: {data.reading.commits_read.toLocaleString("en-US")} of{" "}
          {data.reading.commits_total.toLocaleString("en-US")} commits read.
          {data.reading.error && ` The last reading failed: ${data.reading.error}`}
        </p>
      ) : (
        <>
          <Reading meter={reading} scope={area || "this repository"} />
          {data.weeks.length > 0 && <WeeklyChart weeks={data.weeks} />}
          <p className="text-xs text-fg-muted">
            {reading.computed_at && `Scored ${formatDateTime(reading.computed_at)}, from ${data.reading.commits_total.toLocaleString("en-US")} commits. `}
            After{" "}
            <a
              className="underline hover:text-fg"
              href="https://github.com/slop-o-meter/slop-o-meter"
              target="_blank"
              rel="noreferrer"
            >
              Slop-O-Meter
            </a>
            's published slop score algorithm.
          </p>
        </>
      )}
    </section>
  );
}
