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

/**
 * Canonical assignment of one alchemical operation per app surface.
 * Surfaces where no assignment makes mythic sense are simply absent —
 * callers fall back to undefined and render no operation glyph.
 *
 * Reasoning per assignment:
 *   - issues:      calcinate (the matter is being broken down)
 *   - jobs:        ferment (work transforming, decay+rebirth)
 *   - pipelines:   conjoin (bringing pieces together as a whole)
 *   - agents:      distill (each agent is a refinement of capability)
 *   - sandboxes:   ferment (the sealed vessel where the work happens)
 *   - templates:   coagulate (a sealed canonical form)
 *   - environments:separate (sorting variables into pure/impure realms)
 *   - settings:    separate (configuring divisions of behavior)
 *   - dashboard:   conjoin (the sphere where everything meets)
 */
export const OPERATION_FOR_ROUTE: Partial<Record<string, Operation>> = {
  "/": "conjoin",
  "/issues": "calcinate",
  "/jobs": "ferment",
  "/pipelines": "conjoin",
  "/agents": "distill",
  "/sandboxes": "ferment",
  "/templates": "coagulate",
  "/environments": "separate",
  "/settings": "separate",
};
