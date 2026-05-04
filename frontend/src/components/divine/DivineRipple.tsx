interface DivineRippleProps {
  size?: number;
  className?: string;
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * One-shot expanding ring used for event acknowledgments. Mount with a
 * counter key (`<DivineRipple key={rippleKey} />`); incrementing the key
 * remounts and retriggers the 2.5s animation.
 *
 * Returns null when the user prefers reduced motion. The CSS keyframe is
 * also gated by `body:not([data-divine="off"])` so the escape hatch
 * suppresses animation even when this component renders.
 *
 * Compound-property exception per divine rule #3 (animates r + opacity,
 * justified as a one-shot — see `divine/CLAUDE.md` rule #3).
 *
 * See `docs/superpowers/specs/2026-05-04-divine-animations-design.md` §3.4.
 */
export function DivineRipple({ size = 120, className }: DivineRippleProps) {
  if (prefersReducedMotion()) return null;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      className={`divine-element text-primary pointer-events-none ${className ?? ""}`}
      aria-hidden="true"
    >
      <circle
        className="divine-ripple-circle"
        cx="32"
        cy="32"
        r="2.4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
      />
    </svg>
  );
}
