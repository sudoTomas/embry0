interface AthanorMarkProps {
  className?: string;
}

/**
 * Athanor masthead — the alchemical vessel + ATHANOR wordmark.
 *
 * The vessel breathes via a 4s opacity cycle (`@keyframes athanor-pulse`).
 * Pulse is suppressed by `prefers-reduced-motion: reduce` AND by the
 * `[data-divine="off"]` body attribute (the divine-layer escape hatch).
 *
 * Rendering target: the TopBar left edge, replacing a bare `<span>Athanor</span>`.
 */
export function AthanorMark({ className }: AthanorMarkProps) {
  return (
    <div
      className={`flex items-center gap-2 select-none divine-element ${className ?? ""}`}
      aria-label="Athanor"
    >
      {/* Alchemical vessel — long-necked retort. Stroke uses the gold primary token. */}
      <svg
        width="20"
        height="20"
        viewBox="0 0 24 24"
        aria-hidden="true"
        className="athanor-vessel text-primary"
      >
        {/* Round body */}
        <circle cx="9" cy="16" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
        {/* Long curving neck */}
        <path
          d="M11.5 11.5 Q14 8 17 5.5 L20 4"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
        />
        {/* Inner flame — slightly off-center, faint */}
        <path
          d="M9 18 Q10 15 9 13 Q8 15 9 18 Z"
          fill="currentColor"
          opacity="0.45"
        />
        {/* Base line */}
        <line
          x1="3"
          y1="22"
          x2="15"
          y2="22"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
          opacity="0.6"
        />
      </svg>
      <span
        className="font-display text-[13px] font-semibold tracking-[0.18em] text-white/85"
      >
        ATHANOR
      </span>
    </div>
  );
}
