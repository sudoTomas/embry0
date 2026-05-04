/**
 * The seven canonical alchemical operations of the Magnum Opus.
 * Each operation has a distinct visual character in Athanor's
 * geodesic vocabulary — see
 * `docs/superpowers/specs/2026-05-04-divine-operations-design.md`
 * and `frontend/src/components/divine/CLAUDE.md`.
 */

export type Operation =
  | "calcinate"
  | "dissolve"
  | "separate"
  | "conjoin"
  | "ferment"
  | "distill"
  | "coagulate";

export const OPERATIONS: readonly Operation[] = [
  "calcinate",
  "dissolve",
  "separate",
  "conjoin",
  "ferment",
  "distill",
  "coagulate",
] as const;

export const OPERATION_ELEMENT: Record<Operation, string> = {
  calcinate: "fire",
  dissolve: "water",
  separate: "air",
  conjoin: "fire+water",
  ferment: "earth",
  distill: "aether",
  coagulate: "stone",
};

/**
 * Roman numeral position in the canonical seven-step sequence.
 * Used in patent-style decorative chrome and operation glyph captions.
 */
export const OPERATION_NUMERAL: Record<Operation, string> = {
  calcinate: "I",
  dissolve: "II",
  separate: "III",
  conjoin: "IV",
  ferment: "V",
  distill: "VI",
  coagulate: "VII",
};
