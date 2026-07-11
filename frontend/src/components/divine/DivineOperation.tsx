import type { Operation } from "./operations";

interface DivineOperationProps {
  operation: Operation;
  size?: number;
  className?: string;
  label?: string;
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

const CARDINAL_DOTS = (
  <>
    <circle cx="32" cy="10" r="2.4" fill="currentColor" />
    <circle cx="54" cy="32" r="2.4" fill="currentColor" />
    <circle cx="32" cy="54" r="2.4" fill="currentColor" />
    <circle cx="10" cy="32" r="2.4" fill="currentColor" />
  </>
);

const RING_OUTLINE = (
  <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" strokeWidth="1.4" opacity="0.5" />
);

const EQUATOR = (
  <line x1="14" y1="32" x2="50" y2="32" stroke="currentColor" strokeWidth="0.9" opacity="0.4" />
);

function CalcinateBody() {
  return (
    <>
      {RING_OUTLINE}
      {EQUATOR}
      <g className="divine-op-calcinate-struts">
        <path
          d="M 32 18 L 18 32 M 32 18 L 24 32 M 32 18 L 32 32 M 32 18 L 40 32 M 32 18 L 46 32"
          stroke="currentColor"
          strokeWidth="1"
          fill="none"
        />
      </g>
      {CARDINAL_DOTS}
    </>
  );
}

function DissolveBody() {
  return (
    <>
      <circle
        className="divine-op-dissolve-ring"
        cx="32"
        cy="32"
        r="22"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
      />
      {EQUATOR}
      {CARDINAL_DOTS}
    </>
  );
}

function SeparateBody() {
  return (
    <>
      {RING_OUTLINE}
      <g className="divine-op-separate-top">
        <path d="M 10 32 A 22 22 0 0 1 54 32" fill="none" stroke="currentColor" strokeWidth="1.6" />
      </g>
      <line x1="14" y1="32" x2="50" y2="32" stroke="currentColor" strokeWidth="1.2" />
      <g className="divine-op-separate-bottom">
        <path d="M 54 32 A 22 22 0 0 1 10 32" fill="none" stroke="currentColor" strokeWidth="1.6" />
      </g>
      {CARDINAL_DOTS}
    </>
  );
}

function ConjoinBody() {
  return (
    <>
      {RING_OUTLINE}
      <line x1="14" y1="32" x2="50" y2="32" stroke="currentColor" strokeWidth="0.9" opacity="0.5" />
      <g className="divine-op-conjoin-hemis">
        <path d="M 10 32 A 22 22 0 0 1 54 32 Z" fill="currentColor" />
        <path d="M 54 32 A 22 22 0 0 1 10 32 Z" fill="currentColor" />
      </g>
      {CARDINAL_DOTS}
    </>
  );
}

function FermentBody() {
  return (
    <>
      {RING_OUTLINE}
      {EQUATOR}
      <circle className="divine-op-ferment-core" cx="32" cy="32" r="6" fill="currentColor" />
      {CARDINAL_DOTS}
    </>
  );
}

function DistillBody() {
  return (
    <>
      {RING_OUTLINE}
      {EQUATOR}
      <circle
        className="divine-op-distill-1"
        cx="32"
        cy="32"
        r="4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1"
      />
      <circle
        className="divine-op-distill-2"
        cx="32"
        cy="32"
        r="4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1"
      />
      <circle
        className="divine-op-distill-3"
        cx="32"
        cy="32"
        r="4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1"
      />
      {CARDINAL_DOTS}
    </>
  );
}

function CoagulateBody() {
  return (
    <>
      <circle
        className="divine-op-coagulate-fill"
        cx="32"
        cy="32"
        r="22"
        fill="currentColor"
      />
      {/* Faint outline survives the fill so the ring still reads */}
      <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" strokeWidth="1.4" opacity="0.5" />
      {CARDINAL_DOTS}
    </>
  );
}

const BODIES: Record<Operation, () => React.ReactElement> = {
  calcinate: CalcinateBody,
  dissolve: DissolveBody,
  separate: SeparateBody,
  conjoin: ConjoinBody,
  ferment: FermentBody,
  distill: DistillBody,
  coagulate: CoagulateBody,
};

/**
 * Renders the geodesic mark with one of the seven alchemical operations'
 * specific motion applied. Component-level reduced-motion guard returns
 * null; CSS gating handles `body[data-divine="off"]`.
 */
export function DivineOperation({
  operation,
  size = 80,
  className,
  label,
}: DivineOperationProps) {
  if (prefersReducedMotion()) return null;
  const Body = BODIES[operation];
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      className={`divine-element text-primary ${className ?? ""}`}
      role={label ? "img" : "presentation"}
      aria-label={label}
    >
      <Body />
    </svg>
  );
}
