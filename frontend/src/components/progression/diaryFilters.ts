/** The achievement diary's state filter (spec 2026-10-25): every entry, or those in one state. */
export type StateFilter = "all" | "unlocked" | "discovered" | "secret";
export const STATE_FILTERS: readonly StateFilter[] = ["all", "unlocked", "discovered", "secret"];
