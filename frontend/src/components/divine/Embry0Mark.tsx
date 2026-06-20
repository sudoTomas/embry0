interface Embry0MarkProps {
  className?: string;
}

/**
 * embry0 masthead — cell-division identity mark reworked as "e0".
 * Two overlapping cells (the dividing embryo): the lowercase "e" bowl and the
 * "0" ring, with a nucleus dot at the division point. Accent (#4dffd1) via
 * text-primary. Mirrors the embry0.ai motif while reading as e0.
 */
export function Embry0Mark({ className }: Embry0MarkProps) {
  return (
    <div
      className={`flex items-center gap-2 select-none ${className ?? ""}`}
      aria-label="embry0"
    >
      <svg width="24" height="24" viewBox="0 0 64 64" aria-hidden="true" className="text-primary">
        <g stroke="currentColor" strokeWidth="2.5" fill="none" strokeLinecap="round">
          {/* "e" cell (left): bowl + crossbar with an opening at lower-right */}
          <path d="M 31 31 H 13 A 11 11 0 1 0 28 40.5" />
          {/* "0" cell (right): tall ring */}
          <ellipse cx="41" cy="32" rx="12" ry="15" />
        </g>
        {/* nucleus at the point of division */}
        <circle cx="30.5" cy="32" r="2.3" fill="currentColor" />
      </svg>
      <span className="font-display text-[13px] font-semibold tracking-[0.18em] lowercase text-white/85">
        embry0
      </span>
    </div>
  );
}
