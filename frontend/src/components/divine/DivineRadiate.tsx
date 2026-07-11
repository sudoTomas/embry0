import { useEffect, useState } from "react";
import { DivineOperation } from "./DivineOperation";
import type { Operation } from "./operations";

interface DivineRadiateProps {
  size?: number;
  className?: string;
  label?: string;
  /**
   * Pin the loader to a single operation. Renders that operation's
   * animation indefinitely.
   */
  operation?: Operation;
  /**
   * Cycle through these operations in order, advancing every
   * `cycleSeconds`. Defaults to the canonical seven if both this and
   * `operation` are omitted (legacy strut breath remains for callers
   * that pass neither).
   */
  operations?: readonly Operation[];
  /**
   * Per-operation hold time in seconds. Default 4. Ignored unless
   * `operations` is set.
   */
  cycleSeconds?: number;
}

const STRUT_PATHS = [
  "M 32 18 L 18 32",
  "M 32 18 L 24 32",
  "M 32 18 L 32 32",
  "M 32 18 L 40 32",
  "M 32 18 L 46 32",
] as const;

/**
 * Loading state — the geodesic mark with motion. Three modes:
 *
 *  1. Default (no operation/operations props) — north-pole strut breath
 *     (legacy v1 behavior). One animated property only (opacity).
 *  2. `operation` set — pin to that operation's animation indefinitely.
 *  3. `operations` set — cycle through them every `cycleSeconds` (default 4).
 *
 * Animation gated by `prefers-reduced-motion: no-preference` AND
 * `body:not([data-divine="off"])` — see frontend/src/index.css.
 *
 * Per divine rule #5: NEVER use this on latency-sensitive surfaces
 * (job feeds, log tails, error toasts).
 */
export function DivineRadiate({
  size = 80,
  className,
  label,
  operation,
  operations,
  cycleSeconds = 4,
}: DivineRadiateProps) {
  // Cycling state — only used when `operations` prop is set and `operation` is not.
  const [cycleIndex, setCycleIndex] = useState(0);

  useEffect(() => {
    if (operation || !operations || operations.length <= 1) return;
    const ms = Math.max(500, cycleSeconds * 1000);
    const timer = setInterval(() => {
      setCycleIndex((i) => (i + 1) % operations.length);
    }, ms);
    return () => clearInterval(timer);
  }, [operation, operations, cycleSeconds]);

  // Reset cycle to 0 when operations array changes
  useEffect(() => {
    setCycleIndex(0);
  }, [operations]);

  // Pinned-operation mode wins over cycling
  const activeOperation = operation
    ? operation
    : operations && operations.length > 0
      ? operations[cycleIndex % operations.length]
      : null;

  if (activeOperation) {
    return (
      <DivineOperation
        operation={activeOperation}
        size={size}
        className={className}
        label={label}
      />
    );
  }

  // Default v1 strut-breath behavior — backward compatible
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      className={`divine-element divine-radiate text-primary ${className ?? ""}`}
      role={label ? "img" : "presentation"}
      aria-label={label}
    >
      <g opacity="0.25">
        <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" strokeWidth="1.4" />
        <line x1="14" y1="32" x2="50" y2="32" stroke="currentColor" strokeWidth="0.9" />
        <circle cx="32" cy="10" r="2.4" fill="currentColor" />
        <circle cx="54" cy="32" r="2.4" fill="currentColor" />
        <circle cx="32" cy="54" r="2.4" fill="currentColor" />
        <circle cx="10" cy="32" r="2.4" fill="currentColor" />
      </g>
      <g className="divine-radiate-struts">
        {STRUT_PATHS.map((d, i) => (
          <path
            key={i}
            data-strut="north-pole"
            d={d}
            fill="none"
            stroke="currentColor"
            strokeWidth="1"
          />
        ))}
        <circle cx="32" cy="18" r="1.6" fill="currentColor" />
      </g>
    </svg>
  );
}
