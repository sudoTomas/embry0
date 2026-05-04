/**
 * Alchemical sigil SVG path data per pipeline stage.
 *
 * Each value is the inner content of an `<svg viewBox="0 0 64 64">`.
 * Stroke uses currentColor so the parent text color drives it; fills
 * also use currentColor for hemisphere wedges (with opacity).
 *
 * The four in-scope stages of the current pipeline (triage, develop,
 * validate, qa) render the geodesic identity mark with the cardinal
 * hemisphere matching their pipeline position lit:
 *
 *   - triage   → north hemisphere lit (top filled)
 *   - develop  → east hemisphere lit  (right filled)  ≡ "developer"
 *   - validate → south hemisphere lit (bottom filled) ≡ "review"
 *   - qa       → west hemisphere lit  (left filled)
 *
 * The legacy stages explore and publish keep their classical glyphs
 * (Antimony ⚯ and Sol ☉) — they sit outside the 4-cardinal mapping;
 * full reconciliation is deferred per spec §6.
 *
 * See `docs/superpowers/specs/2026-05-04-geodesic-identity-design.md`.
 */

export type Stage =
  | "triage"
  | "explore"
  | "develop"
  | "validate"
  | "qa"
  | "publish";

/**
 * Maps the four in-scope pipeline stages to their cardinal positions on
 * the geodesic mark. See spec §3.2.
 *
 * Stage-name aliases (spec §6 deferred):
 *   developer ≡ develop
 *   review    ≡ validate
 */
export const CARDINAL_HEMISPHERES = {
  triage: "north",
  develop: "east",
  validate: "south",
  qa: "west",
} as const;

export type CardinalStage = keyof typeof CARDINAL_HEMISPHERES;

const CARDINAL_DOTS = `
    <circle cx="32" cy="10" r="2.4" fill="currentColor"/>
    <circle cx="54" cy="32" r="2.4" fill="currentColor"/>
    <circle cx="32" cy="54" r="2.4" fill="currentColor"/>
    <circle cx="10" cy="32" r="2.4" fill="currentColor"/>
`;

export const STAGE_SIGILS: Record<Stage, string> = {
  // Triage — north hemisphere lit (top half filled)
  triage: `
    <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" stroke-width="2"/>
    <line x1="14" y1="32" x2="50" y2="32" stroke="currentColor" stroke-width="1.4" opacity="0.5"/>
    <path data-hemisphere="north" d="M 10 32 A 22 22 0 0 1 54 32 Z" fill="currentColor" opacity="0.7"/>
    ${CARDINAL_DOTS}
  `,
  // Antimony ⚯ — legacy classical glyph (preserved per spec §6)
  explore: `
    <circle cx="32" cy="22" r="9" fill="none" stroke="currentColor" stroke-width="2"/>
    <line x1="32" y1="31" x2="32" y2="54" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    <line x1="24" y1="44" x2="40" y2="44" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
  `,
  // Develop — east hemisphere lit (right half filled) — alias: developer
  develop: `
    <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" stroke-width="2"/>
    <line x1="32" y1="14" x2="32" y2="50" stroke="currentColor" stroke-width="1.4" opacity="0.5"/>
    <path data-hemisphere="east" d="M 32 10 A 22 22 0 0 1 32 54 Z" fill="currentColor" opacity="0.7"/>
    ${CARDINAL_DOTS}
  `,
  // Validate — south hemisphere lit (bottom half filled) — alias: review
  validate: `
    <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" stroke-width="2"/>
    <line x1="14" y1="32" x2="50" y2="32" stroke="currentColor" stroke-width="1.4" opacity="0.5"/>
    <path data-hemisphere="south" d="M 54 32 A 22 22 0 0 1 10 32 Z" fill="currentColor" opacity="0.7"/>
    ${CARDINAL_DOTS}
  `,
  // QA — west hemisphere lit (left half filled)
  qa: `
    <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" stroke-width="2"/>
    <line x1="32" y1="14" x2="32" y2="50" stroke="currentColor" stroke-width="1.4" opacity="0.5"/>
    <path data-hemisphere="west" d="M 32 54 A 22 22 0 0 1 32 10 Z" fill="currentColor" opacity="0.7"/>
    ${CARDINAL_DOTS}
  `,
  // Sol ☉ — legacy classical glyph (preserved per spec §6)
  publish: `
    <circle cx="32" cy="32" r="18" fill="none" stroke="currentColor" stroke-width="2"/>
    <circle cx="32" cy="32" r="3" fill="currentColor"/>
  `,
};

/**
 * Map an agent category (returned by getAgentCategory()) to a stage.
 * Categories outside this map get no sigil — the renderer returns null.
 */
export function categoryToStage(category: string | undefined | null): Stage | null {
  if (!category) return null;
  const lower = category.toLowerCase();
  if (lower in STAGE_SIGILS) return lower as Stage;
  // Common aliases used in Athanor's existing taxonomy
  if (lower === "reviewer" || lower === "validator") return "validate";
  if (lower === "developer") return "develop";
  if (lower === "explorer") return "explore";
  if (lower === "output") return "publish";
  return null;
}
