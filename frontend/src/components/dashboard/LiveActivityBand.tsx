import { Activity } from "lucide-react";

interface LiveActivityBandProps {
  running: number;
  queued: number;
  awaitingInput: number;
}

/**
 * Slim live-activity band — visible only when there's something actually
 * happening. Mirrors Companion's "progress-wrap" affordance but reads as a
 * status strip rather than a literal progress bar (embry0 jobs do not
 * report % completion in a way that would be honest to render).
 */
export function LiveActivityBand({ running, queued, awaitingInput }: LiveActivityBandProps) {
  const hasActivity = running > 0 || queued > 0 || awaitingInput > 0;
  if (!hasActivity) return null;

  const segments: { label: string; count: number; color: string }[] = [];
  if (running > 0) segments.push({ label: "running", count: running, color: "#22c55e" });
  if (queued > 0) segments.push({ label: "queued", count: queued, color: "#06b6d4" });
  if (awaitingInput > 0)
    segments.push({ label: "awaiting input", count: awaitingInput, color: "#f59e0b" });

  return (
    <div
      className="embry0-card animate-fade-up flex items-center gap-3 px-4 py-2.5"
      style={{ animationDelay: "60ms", borderColor: "rgba(34,197,94,0.15)" }}
    >
      <Activity size={14} className="text-success animate-pulse-glow shrink-0" />
      <span className="text-xs font-semibold uppercase tracking-wide text-success">Live</span>
      <span className="text-white/20">·</span>
      <ul className="flex items-center gap-3 text-xs text-white/70 tabular-nums">
        {segments.map((s) => (
          <li key={s.label} className="flex items-center gap-1.5">
            <span
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{ background: s.color }}
              aria-hidden
            />
            <span className="font-semibold" style={{ color: s.color }}>
              {s.count}
            </span>
            <span className="text-white/50">{s.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
