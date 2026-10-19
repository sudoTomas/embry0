interface Embry0MarkProps {
  className?: string;
}

/**
 * embry0 masthead lockup — the cell-division mark reworked as "e0", in a tile
 * like the family's logo tiles, beside the name in two tones: "embry" in the
 * primary text colour and "0" in the gold accent (as the sibling products set
 * their own name in white and their accent). Two overlapping cells (the
 * dividing embryo): the lowercase "e" bowl and the "0" ring, with a nucleus dot
 * at the division point. Mirrors the embry0.ai motif while reading as e0.
 */
export function Embry0Mark({ className }: Embry0MarkProps) {
  return (
    <div
      role="img"
      className={`flex items-center gap-3 select-none ${className ?? ""}`}
      aria-label="embry0"
    >
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border-strong bg-page">
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
      </span>
      <span aria-hidden="true" className="font-display text-lg font-extrabold leading-none tracking-tight">
        <span className="text-fg">embry</span>
        <span className="text-primary">0</span>
      </span>
    </div>
  );
}
