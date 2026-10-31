import { api } from "./client";

/**
 * One kind of work on offer, as the backend's lead-kind catalogue
 * (embry0/services/lead_kinds.py), the one place its names live, serves it
 * from GET /lead-kinds. Its plain names only, the game on or off: the game's
 * words for a kind (verb, creature, leader, level) ride on each quest of GET /world.
 */
export interface LeadKind {
  id: string;
  /** One of it, as a badge says it. */
  label: string;
  /** Several of it, as a filter says them. */
  plural: string;
  /** What it is, in a phrase. */
  about: string;
  /** What brings it in. */
  found_by: string;
  /** Stored as a lead (GET /leads): found by a finder, the scout or a knowledge map. Absent on an older backend. */
  lead?: boolean;
}

/** Every kind, in the catalogue's order. A failure is the caller's to handle (each kind reads as its id), so it raises no toast. */
export async function fetchLeadKinds(): Promise<LeadKind[]> {
  const { data } = await api.get<{ kinds: LeadKind[] }>("/lead-kinds", { suppressErrorToast: true });
  return data.kinds;
}
