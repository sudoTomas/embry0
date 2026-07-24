/**
 * The seven canonical alchemical operations of the Magnum Opus.
 * Each operation has a distinct visual character in embry0's
 * geodesic vocabulary — see `frontend/src/components/divine/CLAUDE.md`.
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
 * Stable, prefix-disambiguated identifiers for the seven operations.
 * The slug (e.g. "calcinate") is the human-facing handle; the ID is the
 * durable reference used in pipeline metadata, audit logs, PR templates,
 * and any cross-document citation. Slugs may evolve (Latin vs anglicized
 * naming has been debated); IDs do not.
 *
 * Format: OP## — two-digit zero-padded sequence number.
 */
export const OPERATION_ID: Record<Operation, string> = {
  calcinate: "OP01",
  dissolve: "OP02",
  separate: "OP03",
  conjoin: "OP04",
  ferment: "OP05",
  distill: "OP06",
  coagulate: "OP07",
};

/**
 * Reverse lookup — resolve a stable ID back to its operation slug.
 * Use when reading audit logs or pipeline metadata that stores the ID.
 */
export const OPERATION_BY_ID: Record<string, Operation> = Object.fromEntries(
  (Object.entries(OPERATION_ID) as [Operation, string][]).map(([op, id]) => [id, op]),
);

/**
 * Last revision date per operation in ISO-8601 (YYYY-MM-DD).
 * Bump when the operation's semantics change — not on cosmetic edits.
 * The date pairs with the ID to give consumers a "what version of OP04
 * was this pipeline built against?" check.
 */
export const OPERATION_REV: Record<Operation, string> = {
  calcinate: "2026-05-04",
  dissolve: "2026-05-04",
  separate: "2026-05-04",
  conjoin: "2026-05-04",
  ferment: "2026-05-04",
  distill: "2026-05-04",
  coagulate: "2026-05-04",
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

/**
 * Maps an agent type to its primary alchemical operation. Captures the
 * spirit of what each agent DOES in the magnum opus:
 *
 *   triage              → calcinate  (breaks down the matter)
 *   developer / code-gen → ferment    (transforms code through decay+rebirth)
 *   docs-writer         → ferment
 *   explorer / fronted  → separate   (sorts pure from impure)
 *   reviewer / security → distill    (refines the work)
 *   review              → distill    (alias)
 *   validator / lint    → conjoin    (joins claim + proof)
 *   test-runner / type  → conjoin
 *   visual-validator    → conjoin
 *   qa                  → conjoin    (proves the union)
 *   output / publish    → coagulate  (seals the gold)
 *   research            → separate   (sorts signal from noise)
 *   analysis            → distill    (refines raw material to findings)
 *   ops                 → ferment    (transforms the workspace)
 *
 * Anything outside the map (custom agents) returns undefined and the
 * caller renders no glyph.
 */
export function agentTypeToOperation(agentType: string | undefined | null): Operation | undefined {
  if (!agentType) return undefined;
  const t = agentType.toLowerCase();
  if (t === "triage") return "calcinate";
  if (t === "developer" || t === "code-gen" || t === "docs-writer" || t === "ops") return "ferment";
  if (t === "explorer" || t === "frontend-explorer" || t === "research") return "separate";
  if (t === "reviewer" || t === "security-reviewer" || t === "review" || t === "analysis") return "distill";
  if (
    t === "validator" ||
    t === "lint-checker" ||
    t === "type-checker" ||
    t === "test-runner" ||
    t === "visual-validator" ||
    t === "qa"
  ) {
    return "conjoin";
  }
  if (t === "output" || t === "publish") return "coagulate";
  return undefined;
}

/**
 * Decide whether a job's status transition should fire a completion flare.
 *
 * Returns true iff:
 *   - both `prev` and `curr` are present (we have a real transition, not a
 *     first-mount of an already-complete job, which would be noise),
 *   - `prev !== curr` (status actually flipped this render), and
 *   - `curr` is a terminal-success state (`completed` or `pr_merged`).
 *
 * Failure terminals (`failed` / `cancelled` / `expired` / `pr_closed`) are
 * deliberately not success — divine layer rule #5 keeps flourishes off
 * operator-critical paths.
 */
export function shouldFireCompletionFlare(
  prev: string | null | undefined,
  curr: string | null | undefined,
): boolean {
  if (!prev || !curr) return false;
  if (prev === curr) return false;
  return curr === "completed" || curr === "pr_merged";
}

/**
 * Pick the operation for a job header based on its current pipeline state.
 *
 * The glyph reflects WHERE in the magnum opus the job is right now:
 * - Active agent → the operation of that agent (calcinate during triage,
 *   ferment during developer, distill during review, conjoin during QA…).
 * - No active agent but completed history → the LAST completed agent's op
 *   (so a job whose final agent published reads as `coagulate`).
 * - Empty pipeline (no agents observed yet) → `ferment` per
 *   `OPERATION_FOR_ROUTE` (the route's static answer).
 *
 * Inputs are loose `{ agent: string; status: string }` so the helper does not
 * leak hook-internal types into the divine layer.
 */
export function jobToOperation(
  agents: ReadonlyArray<{ agent: string; status: string }>,
): Operation {
  const active = agents.find((a) => a.status === "running");
  if (active) {
    const op = agentTypeToOperation(active.agent);
    if (op) return op;
  }
  const completed = agents.filter((a) => a.status === "completed" || a.status === "failed");
  if (completed.length > 0) {
    const last = completed[completed.length - 1];
    const op = agentTypeToOperation(last.agent);
    if (op) return op;
  }
  return "ferment";
}
