interface AthanorMarkProps {
  className?: string;
}

/**
 * Athanor masthead — geodesic identity mark + ATHANOR wordmark.
 *
 * The mark is the mid-density render of the spec's Full Geodesic Sphere:
 * outer ring, four cardinal dots (N/E/S/W), equator line, and two simplified
 * hemisphere triangles. See `docs/superpowers/specs/2026-05-04-geodesic-identity-design.md`
 * for the full primitive grammar.
 *
 * Stroke uses currentColor so the parent's text color drives it; with the
 * gold token swap, this resolves to #d4af37 by default.
 *
 * Rendering target: the TopBar left edge.
 */
export function AthanorMark({ className }: AthanorMarkProps) {
  return (
    <div
      className={`flex items-center gap-2 select-none divine-element ${className ?? ""}`}
      aria-label="Athanor"
    >
      <svg
        width="24"
        height="24"
        viewBox="0 0 64 64"
        aria-hidden="true"
        className="athanor-mark text-primary"
      >
        <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" strokeWidth="2.2" />
        <circle cx="32" cy="10" r="2.4" fill="currentColor" />
        <circle cx="54" cy="32" r="2.4" fill="currentColor" />
        <circle cx="32" cy="54" r="2.4" fill="currentColor" />
        <circle cx="10" cy="32" r="2.4" fill="currentColor" />
        <line
          x1="14"
          y1="32"
          x2="50"
          y2="32"
          stroke="currentColor"
          strokeWidth="1.6"
          opacity="0.7"
        />
        <path
          d="M 32 18 L 22 32 L 32 32 L 42 32 Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
        />
        <path
          d="M 32 46 L 22 32 L 32 32 L 42 32 Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          opacity="0.7"
        />
      </svg>
      <span className="font-display text-[13px] font-semibold tracking-[0.18em] text-white/85">
        ATHANOR
      </span>
    </div>
  );
}
