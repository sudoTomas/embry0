import type { ReactElement } from "react";
import { OPERATION_ELEMENT, OPERATION_NUMERAL, type Operation } from "./operations";

interface OperationGlyphProps {
  operation: Operation;
  size?: number;
  className?: string;
  titled?: boolean;
}

const CARDINAL_DOTS = (
  <>
    <circle cx="32" cy="10" r="2.4" fill="currentColor" />
    <circle cx="54" cy="32" r="2.4" fill="currentColor" />
    <circle cx="32" cy="54" r="2.4" fill="currentColor" />
    <circle cx="10" cy="32" r="2.4" fill="currentColor" />
  </>
);

const RING = (
  <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" strokeWidth="1.4" opacity="0.7" />
);

const EQUATOR = (
  <line x1="14" y1="32" x2="50" y2="32" stroke="currentColor" strokeWidth="0.9" opacity="0.5" />
);

/**
 * Static still-frame variants of the seven operations. Each frame
 * captures the operation's signature gesture at a representative
 * moment without motion. Used as decorative chrome (panel headers,
 * agent-detail headers, splash sub-figures).
 *
 * Each variant is hand-tuned from its corresponding `<DivineOperation>`
 * animation — see `2026-05-04-divine-operations-design.md`.
 */
const FRAMES: Record<Operation, ReactElement> = {
  calcinate: (
    <>
      {RING}
      {EQUATOR}
      <path
        d="M 32 18 L 18 32 M 32 18 L 24 32 M 32 18 L 32 32 M 32 18 L 40 32 M 32 18 L 46 32"
        stroke="currentColor"
        strokeWidth="1"
        fill="none"
        opacity="0.85"
      />
      {CARDINAL_DOTS}
    </>
  ),
  dissolve: (
    <>
      {/* Two concentric outlines suggesting the moment of dissipation */}
      <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" strokeWidth="1.4" opacity="0.85" />
      <circle cx="32" cy="32" r="26" fill="none" stroke="currentColor" strokeWidth="0.7" opacity="0.35" />
      {EQUATOR}
      {CARDINAL_DOTS}
    </>
  ),
  separate: (
    <>
      {RING}
      <path
        d="M 10 30 A 22 22 0 0 1 54 30"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <line x1="14" y1="32" x2="50" y2="32" stroke="currentColor" strokeWidth="1.2" />
      <path
        d="M 54 34 A 22 22 0 0 1 10 34"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      {CARDINAL_DOTS}
    </>
  ),
  conjoin: (
    <>
      {RING}
      <line x1="14" y1="32" x2="50" y2="32" stroke="currentColor" strokeWidth="0.9" opacity="0.5" />
      <path d="M 10 32 A 22 22 0 0 1 54 32 Z" fill="currentColor" opacity="0.7" />
      <path d="M 54 32 A 22 22 0 0 1 10 32 Z" fill="currentColor" opacity="0.7" />
      {CARDINAL_DOTS}
    </>
  ),
  ferment: (
    <>
      {RING}
      {EQUATOR}
      <circle cx="32" cy="32" r="6" fill="currentColor" opacity="0.85" />
      {CARDINAL_DOTS}
    </>
  ),
  distill: (
    <>
      {RING}
      {EQUATOR}
      <circle cx="32" cy="32" r="6" fill="none" stroke="currentColor" strokeWidth="1" opacity="0.85" />
      <circle cx="32" cy="32" r="11" fill="none" stroke="currentColor" strokeWidth="1" opacity="0.55" />
      <circle cx="32" cy="32" r="16" fill="none" stroke="currentColor" strokeWidth="1" opacity="0.3" />
      {CARDINAL_DOTS}
    </>
  ),
  coagulate: (
    <>
      <circle cx="32" cy="32" r="22" fill="currentColor" opacity="0.92" />
      <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" strokeWidth="1.4" opacity="0.5" />
      {CARDINAL_DOTS}
    </>
  ),
};

/**
 * Static glyph form of an alchemical operation — the still-frame of
 * its `<DivineOperation>` animation. Used as decorative chrome where
 * a panel, page, or section "belongs to" one operation.
 *
 * Renders unchanged regardless of `prefers-reduced-motion` (no motion
 * to suppress) and regardless of `body[data-divine="off"]` is overridden
 * by the divine-element CSS rule which hides it — the layer's escape
 * hatch still applies.
 *
 * The seven-operation vocabulary is frozen — see `divine/CLAUDE.md`.
 */
export function OperationGlyph({
  operation,
  size = 32,
  className,
  titled = false,
}: OperationGlyphProps) {
  const titleText = `${OPERATION_NUMERAL[operation]}. ${operation} (${OPERATION_ELEMENT[operation]})`;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      className={`divine-element text-primary ${className ?? ""}`}
      role={titled ? "img" : "presentation"}
      aria-label={titled ? titleText : undefined}
    >
      {titled && <title>{titleText}</title>}
      {FRAMES[operation]}
    </svg>
  );
}
