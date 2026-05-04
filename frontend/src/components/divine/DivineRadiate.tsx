interface DivineRadiateProps {
  size?: number;
  className?: string;
  label?: string;
}

const STRUT_PATHS = [
  "M 32 18 L 18 32",
  "M 32 18 L 24 32",
  "M 32 18 L 32 32",
  "M 32 18 L 40 32",
  "M 32 18 L 46 32",
] as const;

/**
 * Loading state — the geodesic mark with north-pole struts pulsing.
 *
 * - One animated property only (opacity).
 * - Animation gated by `prefers-reduced-motion: no-preference` AND
 *   `body:not([data-divine="off"])` — see frontend/src/index.css.
 * - Per spec §3.5 + divine rule #5: NEVER use this on latency-sensitive
 *   surfaces (job feeds, log tails, error toasts).
 */
export function DivineRadiate({ size = 80, className, label }: DivineRadiateProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      className={`divine-element divine-radiate text-primary ${className ?? ""}`}
      role={label ? "img" : "presentation"}
      aria-label={label}
    >
      <g opacity="0.25">
        <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" strokeWidth="1.4" />
        <line x1="14" y1="32" x2="50" y2="32" stroke="currentColor" strokeWidth="0.9" />
        <circle cx="32" cy="10" r="2.4" fill="currentColor" />
        <circle cx="54" cy="32" r="2.4" fill="currentColor" />
        <circle cx="32" cy="54" r="2.4" fill="currentColor" />
        <circle cx="10" cy="32" r="2.4" fill="currentColor" />
      </g>
      <g className="divine-radiate-struts">
        {STRUT_PATHS.map((d, i) => (
          <path
            key={i}
            data-strut="north-pole"
            d={d}
            fill="none"
            stroke="currentColor"
            strokeWidth="1"
          />
        ))}
        <circle cx="32" cy="18" r="1.6" fill="currentColor" />
      </g>
    </svg>
  );
}
