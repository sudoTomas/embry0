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
 * These are the brand set's pixel-grid paths (the same drawing the browser tab
 * shows as /favicon.svg). At the header's 32 px their 2 px line and every wall
 * land on whole pixels, where the master's paths blur; the brand set uses this
 * drawing from 16 to 48 px. The dot is cream, never gold: a gold dot fuses
 * with the tail it touches.
 *
 * The name follows in two tones: "embry" in the primary text colour and "0"
 * in the gold accent.
 *
 * The mark is identity, not atmosphere: it carries no `.divine-element`, so
 * `body[data-divine="off"]` leaves the lockup in place. Nothing in it animates.
 */
export function Embry0Mark({ className, mono = false }: Embry0MarkProps) {
  return (
    <div
      role="img"
      className={`flex items-center gap-2.5 select-none ${className ?? ""}`}
      aria-label="embry0"
    >
      <svg
        width="16"
        height="32"
        viewBox="16 0 32 64"
        aria-hidden="true"
        className={`shrink-0 ${mono ? "" : "text-primary"}`}
      >
        {/* the line: the zero, its inward curl and the tail */}
        <path
          fill="currentColor"
          d="M48 16V48A16 16 0 0 1 16 48V16A16 16 0 0 1 48 16ZM44 39.06V16A12 12 0 0 0 20 16V48A12 12 0 0 0 44 48A8 8 0 0 0 36 40A8 8 0 0 0 28 48A4 4 0 0 0 32 52A2 2 0 0 0 33.73 49L37.2 47A6 6 0 0 1 32 56A8 8 0 0 1 24 48A12 12 0 0 1 36 36A12 12 0 0 1 44 39.06Z"
        />
        {/* the dot where the line ends */}
        <path
          fill="currentColor"
          className={mono ? undefined : "text-cream"}
          d="M31.75 48a4.25 4.25 0 1 0 8.5 0 4.25 4.25 0 1 0-8.5 0z"
        />
      </svg>
      <span aria-hidden="true" className="font-display text-lg font-extrabold leading-none tracking-tight">
        <span className={mono ? undefined : "text-fg"}>embry</span>
        <span className={mono ? undefined : "text-primary"}>0</span>
      </span>
    </div>
  );
}
