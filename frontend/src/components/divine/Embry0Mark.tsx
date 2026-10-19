interface Embry0MarkProps {
  className?: string;
  /**
   * One colour. The line, the dot and the word all take the text colour the
   * caller sets in `className`, through `currentColor`. Off by default: a gold
   * line, a cream dot, and the word in two tones.
   */
  mono?: boolean;
}

/**
 * embry0 masthead lockup: the mark, then the name.
 *
 * The mark is one line: a tall zero whose stroke curls inward at its base and
 * ends in a dot, so the zero and the life inside it are one unbroken line.
 * The two paths are the brand master (embry0-mark.svg) verbatim, not the
 * favicon's pixel-grid redraw: the redraw is hinted for 16 to 48 px tabs, and
 * here its larger spiral closes the gap above the zero's bottom wall.
 *
 * The viewBox crops the master's 64-unit box to its ink (x 18.4 to 45.6, y 6
 * to 58), and 16 px across makes the scale 10/17. The 3.4-unit line then draws
 * exactly 2 px wide and the zero's four vertical walls land on whole pixels
 * (0, 2, 14 and 16 px), at 1x and at 2x.
 *
 * The dot is cream, never gold: a gold dot fuses with the tail it touches.
 * The name follows in two tones: "embry" in the primary text colour and "0"
 * in the gold accent. The 6 px gap after the mark keeps the brand lockup's
 * ratio (a 9-unit gap after a 52-unit mark).
 *
 * The mark is identity, not atmosphere: it carries no `.divine-element`, so
 * `body[data-divine="off"]` leaves the lockup in place. Nothing in it animates.
 */
export function Embry0Mark({ className, mono = false }: Embry0MarkProps) {
  return (
    <div
      role="img"
      className={`flex items-center gap-1.5 select-none ${className ?? ""}`}
      aria-label="embry0"
    >
      <svg
        width="16"
        height="31"
        viewBox="18.4 6 27.2 52"
        aria-hidden="true"
        className={`shrink-0 ${mono ? "" : "text-primary"}`}
      >
        {/* the line: the zero, its inward curl and the tail */}
        <path
          fill="currentColor"
          d="M31.47 6.02c3.35-.21 6.88 1.12 9.39 3.31 2.78 2.42 4.48 5.86 4.71 9.54.09 1.49.02 3.49.02 5.02v17.2c0 1.94.12 4.22-.28 6.1-.48 2.37-1.59 4.56-3.22 6.36-2.52 2.81-5.79 4.23-9.53 4.44-3.61.1-6.84-.98-9.57-3.38-2.7-2.38-4.34-5.74-4.55-9.34-.09-1.51-.04-3.35-.04-4.89V23.47c0-2.07-.1-4.73.32-6.68.51-2.33 1.62-4.49 3.21-6.26 2.53-2.83 5.8-4.29 9.54-4.51zM34.59 46.02A2.55 2.55 0 0 0 37.08 42.71L37.35 42.63 37.47 43.03c.39 1.26.2 2.66-.41 3.81-.68 1.28-1.84 2.23-3.22 2.64-3.71 1.08-7.41-1.45-8.36-5.05-.58-2.19-.22-4.34.92-6.28 1.3-2.19 3.43-3.78 5.9-4.42 2.64-.66 5.43-.23 7.74 1.19.87.52 1.48 1.06 2.2 1.78-.03-3.79-.03-7.58-.02-11.36.01-2.18.17-6.27-.33-8.23-.49-1.85-1.47-3.53-2.85-4.86-1.92-1.87-4.5-2.9-7.18-2.86-2.73.06-5.32 1.2-7.2 3.17-1.35 1.42-2.27 3.18-2.67 5.1-.28 1.35-.2 3.16-.2 4.58V39.13c0 1.82-.05 3.89 0 5.69.09 2.7 1.25 5.26 3.24 7.1 1.98 1.84 4.61 2.82 7.31 2.73 2.74-.09 5.33-1.27 7.19-3.27 3.24-3.5 3.78-9.19.1-12.58-1.41-1.31-3.28-2-5.2-1.92-1.69.09-3.28.84-4.41 2.1-1.72 1.89-2.08 5.04-.04 6.83.81.7 1.86 1.05 2.93.98.49-.03 1.01-.14 1.45-.37L34.58 46.3z"
        />
        {/* the dot where the line ends */}
        <path fill="currentColor" className={mono ? undefined : "text-cream"} d="M31.81 43.47a2.84 2.84 0 1 0 5.68 0 2.84 2.84 0 1 0-5.68 0z" />
      </svg>
      <span aria-hidden="true" className="font-display text-lg font-extrabold leading-none tracking-tight">
        <span className={mono ? undefined : "text-fg"}>embry</span>
        <span className={mono ? undefined : "text-primary"}>0</span>
      </span>
    </div>
  );
}
