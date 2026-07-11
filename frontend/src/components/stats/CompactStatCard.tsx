import { clsx } from "clsx";

interface CompactStatCardProps {
  title: string;
  value: string;
  /** Hex color for the value text + accent dot. Defaults to muted. */
  color?: string;
  /** Optional small subtitle line beneath the value. */
  subtitle?: string;
  /** Optional pulse: indicates "alive" state (e.g. running jobs > 0). */
  pulse?: boolean;
  delay?: number;
  className?: string;
}

/**
 * Dense stat card variant for the Companion-style 6-up dashboard strip.
 * Smaller padding + smaller font than `StatCard` so six fit at md breakpoint.
 */
export function CompactStatCard({
  title,
  value,
  color = "#a1a1aa",
  subtitle,
  pulse = false,
  delay = 0,
  className,
}: CompactStatCardProps) {
  return (
    <div
      className={clsx(
        "embry0-card relative overflow-hidden px-3 py-2.5 animate-fade-up",
        className,
      )}
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="flex items-center gap-1.5">
        <span
          className={clsx(
            "inline-block h-1.5 w-1.5 rounded-full",
            pulse && "animate-pulse-glow",
          )}
          style={{ background: color }}
          aria-hidden
        />
        <p className="text-[10px] font-medium text-white/40 uppercase tracking-wider truncate">
          {title}
        </p>
      </div>
      <p
        className="text-2xl font-bold mt-0.5 tracking-tight tabular-nums"
        style={{ color }}
      >
        {value}
      </p>
      {subtitle && (
        <p className="text-[10px] text-white/30 mt-0.5 tabular-nums truncate">{subtitle}</p>
      )}
    </div>
  );
}
