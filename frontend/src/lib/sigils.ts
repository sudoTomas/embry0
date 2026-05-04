/**
 * Alchemical sigil SVG path data per pipeline stage.
 *
 * Each value is the inner content of an `<svg viewBox="0 0 24 24">`.
 * Stroke uses currentColor so the parent text color drives it; fill is
 * intentionally none everywhere except where a sigil's classical drawing
 * has filled glyphs (currently none).
 *
 * Stages mirror Athanor's existing pipeline-stage tokens
 * (cool → warm gradient: triage → explore → develop → validate → publish),
 * mapped onto the corresponding classical alchemical symbols:
 *
 * - triage   → ☿ Mercury     (the messenger; routes incoming work)
 * - explore  → ⚯ Antimony     (the lone wolf; investigates)
 * - develop  → 🜍 Sulphur      (the active principle; transforms code)
 * - validate → 🜔 Salt         (the fixer; tests + binds correctness)
 * - qa       → 🜈 Aqua Vitae   (the proving water; runs the app)
 * - publish  → ☉ Sol           (the gold; the work made manifest)
 *
 * SVGs are hand-drawn 24×24 simplifications of the classical glyphs —
 * legible at 12px, recognizable at 24px. Pure paths, no text.
 */

export type Stage =
  | "triage"
  | "explore"
  | "develop"
  | "validate"
  | "qa"
  | "publish";

export const STAGE_SIGILS: Record<Stage, string> = {
  // Mercury ☿: circle with cross below and crescent above
  triage: `
    <circle cx="12" cy="11" r="3.5" fill="none" stroke="currentColor" stroke-width="1.5"/>
    <path d="M8 8 Q12 4 16 8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    <line x1="12" y1="14.5" x2="12" y2="20" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    <line x1="9" y1="18" x2="15" y2="18" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
  `,
  // Antimony ⚯: circle on stem with cross
  explore: `
    <circle cx="12" cy="8" r="3.5" fill="none" stroke="currentColor" stroke-width="1.5"/>
    <line x1="12" y1="11.5" x2="12" y2="20" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    <line x1="9" y1="16" x2="15" y2="16" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
  `,
  // Sulphur 🜍: triangle on a cross
  develop: `
    <path d="M12 4 L18 13 L6 13 Z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
    <line x1="12" y1="13" x2="12" y2="20" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    <line x1="9" y1="17" x2="15" y2="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
  `,
  // Salt 🜔: circle bisected horizontally
  validate: `
    <circle cx="12" cy="12" r="7" fill="none" stroke="currentColor" stroke-width="1.5"/>
    <line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
  `,
  // Aqua Vitae 🜈: inverted triangle bisected
  qa: `
    <path d="M5 6 L19 6 L12 19 Z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
    <line x1="8.5" y1="12" x2="15.5" y2="12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
  `,
  // Sol ☉: circle with center dot
  publish: `
    <circle cx="12" cy="12" r="7" fill="none" stroke="currentColor" stroke-width="1.5"/>
    <circle cx="12" cy="12" r="1.5" fill="currentColor"/>
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
