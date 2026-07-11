import { DivineOperation } from "./DivineOperation";
import type { Operation } from "./operations";

interface EmptyVesselGlyphProps {
  copy: string;
  subCopy?: string;
  size?: number;
  className?: string;
  /**
   * Optional alchemical operation. When set, the static minimal mark is
   * replaced by a `<DivineOperation>` rendering the operation's specific
   * motion. Caller-driven; default is undefined (static mark).
   */
  operation?: Operation;
}

/**
 * Standard empty-state hero. Minimal-density geodesic mark + hermetic
 * copy. Per divine rule #7: line count + length must match the operator
 * default so layout doesn't shift when divine layer is off.
 *
 * Optional `operation` prop swaps the static mark for an animated
 * `<DivineOperation>` (e.g. "calcinate" — the matter is being broken
 * down, the work begins).
 */
export function EmptyVesselGlyph({
  copy,
  subCopy,
  size = 56,
  className,
  operation,
}: EmptyVesselGlyphProps) {
  return (
    <div
      className={`divine-element flex flex-col items-center gap-3 text-center ${className ?? ""}`}
    >
      {operation ? (
        <DivineOperation operation={operation} size={size} className="opacity-60" />
      ) : (
        <svg
          width={size}
          height={size}
          viewBox="0 0 64 64"
          className="text-primary opacity-50"
          aria-hidden="true"
        >
          <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" strokeWidth="1.4" />
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
            strokeWidth="0.9"
            opacity="0.5"
          />
        </svg>
      )}
      <div className="text-sm max-w-[200px]">
        <p>{copy}</p>
        {subCopy && <p className="opacity-60">{subCopy}</p>}
      </div>
    </div>
  );
}
