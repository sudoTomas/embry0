interface FullGeodesicSphereProps {
  size?: number;
  className?: string;
}

/**
 * Canonical hero illustration for the patent layer. Full geodesic sphere
 * with N-pole strut triangulation, latitude bands, equator (3D ellipse),
 * and cardinal dots — the long-form rendering of the geodesic mark.
 *
 * Pure SVG, currentColor stroke, ~1.5KB inline. See
 * `docs/superpowers/specs/2026-05-04-patent-drawing-layer-design.md` §3.4.
 */
export function FullGeodesicSphere({ size = 240, className }: FullGeodesicSphereProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 200 200"
      className={`divine-element text-primary ${className ?? ""}`}
      aria-hidden="true"
    >
      {/* Outer sphere ring */}
      <circle cx="100" cy="100" r="78" fill="none" stroke="currentColor" strokeWidth="1.2" />
      {/* Equator (3D ellipse) */}
      <ellipse cx="100" cy="100" rx="78" ry="14" fill="none" stroke="currentColor" strokeWidth="0.8" opacity="0.75" />
      {/* Latitude lines: top */}
      <ellipse cx="100" cy="78" rx="74" ry="11" fill="none" stroke="currentColor" strokeWidth="0.6" opacity="0.5" />
      <ellipse cx="100" cy="58" rx="60" ry="8" fill="none" stroke="currentColor" strokeWidth="0.6" opacity="0.4" />
      {/* Latitude lines: bottom */}
      <ellipse cx="100" cy="122" rx="74" ry="11" fill="none" stroke="currentColor" strokeWidth="0.6" opacity="0.5" />
      <ellipse cx="100" cy="142" rx="60" ry="8" fill="none" stroke="currentColor" strokeWidth="0.6" opacity="0.4" />
      {/* Triangulation: top hemisphere — struts from N pole to equator */}
      <g stroke="currentColor" strokeWidth="0.55" fill="none" opacity="0.75">
        <line x1="100" y1="22" x2="40" y2="100" />
        <line x1="100" y1="22" x2="60" y2="100" />
        <line x1="100" y1="22" x2="80" y2="100" />
        <line x1="100" y1="22" x2="100" y2="100" />
        <line x1="100" y1="22" x2="120" y2="100" />
        <line x1="100" y1="22" x2="140" y2="100" />
        <line x1="100" y1="22" x2="160" y2="100" />
        {/* Triangulation crossings */}
        <line x1="60" y1="78" x2="80" y2="78" />
        <line x1="80" y1="78" x2="100" y2="78" />
        <line x1="100" y1="78" x2="120" y2="78" />
        <line x1="120" y1="78" x2="140" y2="78" />
        <line x1="60" y1="58" x2="80" y2="58" />
        <line x1="80" y1="58" x2="100" y2="58" />
        <line x1="100" y1="58" x2="120" y2="58" />
        <line x1="120" y1="58" x2="140" y2="58" />
      </g>
      {/* Triangulation: bottom hemisphere — mirror at lower opacity */}
      <g stroke="currentColor" strokeWidth="0.55" fill="none" opacity="0.55">
        <line x1="100" y1="178" x2="40" y2="100" />
        <line x1="100" y1="178" x2="60" y2="100" />
        <line x1="100" y1="178" x2="80" y2="100" />
        <line x1="100" y1="178" x2="100" y2="100" />
        <line x1="100" y1="178" x2="120" y2="100" />
        <line x1="100" y1="178" x2="140" y2="100" />
        <line x1="100" y1="178" x2="160" y2="100" />
        <line x1="60" y1="122" x2="80" y2="122" />
        <line x1="80" y1="122" x2="100" y2="122" />
        <line x1="100" y1="122" x2="120" y2="122" />
        <line x1="120" y1="122" x2="140" y2="122" />
      </g>
      {/* Cardinal dots */}
      <circle cx="100" cy="22" r="3" fill="currentColor" />
      <circle cx="178" cy="100" r="3" fill="currentColor" />
      <circle cx="100" cy="178" r="3" fill="currentColor" />
      <circle cx="22" cy="100" r="3" fill="currentColor" />
    </svg>
  );
}
