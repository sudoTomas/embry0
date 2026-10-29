import { api } from "./client";
import type { RunWith } from "./runWith";

/**
 * Knowledge maps (spec 2026-10-27): a scanner's map of a codebase or an organization.
 *
 * Each map belongs to a forge owner (#180); `owner` null is a map of no owner, an admin's alone. An older backend
 * sends no `owner` at all, so it is optional. A slug names a map within its owner: every call takes the owner as
 * `MapOwner`, sent as `?owner=` when known, so a slug two owners share still reaches the right map.
 */
export interface MapSummary {
  slug: string;
  /** Absent from an older backend. */
  owner?: string | null;
  title: string;
  repo: string | null;
  commit: string | null;
  source: string;
  generated_at: string | null;
  uploaded_at: string | null;
  uploaded_by: string;
  nodes: number;
  edges: number;
}

export type BloatBand = "keep" | "look" | "bloat";

export interface MapNode {
  id: string;
  kind: string;
  area: string | null;
  label: string;
  sub: string | null;
  path: string | null;
  line: number | null;
  url: string | null;
  repo: string | null;
  x: number | null;
  y: number | null;
  r: number | null;
  signals: string[];
  /** How the part is removed when it is not a file (a table: a migration), in the scanner's words. */
  removal?: string | null;
  bloat?: { score: number; band: BloatBand };
  age?: { first?: string | null; last?: string | null; changes?: number | null; authors?: number | null };
  use?: { level?: number | null; n?: number | null; unit?: string | null };
}

export interface MapArea {
  id: string;
  label: string;
  summary: string | null;
  x: number | null;
  y: number | null;
  r: number | null;
}

export interface MapGraph {
  slug: string;
  /** Absent from an older backend. */
  owner?: string | null;
  title: string;
  repo: string | null;
  commit: string | null;
  source: string;
  generated_at: string | null;
  kinds: { id: string; label: string }[];
  areas: MapArea[];
  nodes: MapNode[];
  edges: { s: string; t: string; rel: string | null }[];
}

export type Verdict = "bloat" | "keep" | "unsure";
export type SendKind = "remove" | "fix" | "analysis";

export interface MarkCounts {
  bloat: number;
  keep: number;
  unsure: number;
  mine: Verdict | null;
}

export interface MarkView {
  by: string;
  verdict: Verdict;
  note: string;
  at: string | null;
}

export interface WorkView {
  kind: SendKind;
  repo: string;
  lead_id: string | null;
  job_id: string | null;
  job_url: string | null;
  status: string | null;
  pr_url: string | null;
  cost_usd: number | null;
  error: string | null;
  by: string | null;
  note: string;
  sent_at: string | null;
  finished_at: string | null;
}

interface Reason {
  text: string;
  w: number;
}

export interface PartDetail {
  narrative?: { does?: string; why?: string; useful?: string; against?: string; lean?: string; sure?: string; at?: string };
  bloat?: { score: number; band: BloatBand; against?: Reason[]; for?: Reason[]; risks?: string[] };
  use?: { label: string; n: number | null; unit?: string | null; text?: string | null; window?: string | null; source?: string | null }[];
  history?: {
    added?: { date?: string; author?: string; subject?: string; pr?: number | null } | null;
    last?: { date?: string } | null;
    changes?: number | null;
    prs?: { number: number; title: string; url: string | null }[];
    tickets?: string[];
  };
}

export interface PartView {
  map: { slug: string; owner?: string | null; title: string; repo: string | null };
  node: MapNode;
  repo: string | null;
  /** A file or folder in a repository: an agent can remove it. A whole system or repository cannot be. */
  removable: boolean;
  detail: PartDetail | null;
  marks: MarkView[];
  work: WorkView | null;
  lead: { id: string; state: string } | null;
}

export interface SendResult {
  node_id: string;
  kind: SendKind;
  lead_id: string | null;
  job_id: string | null;
  error: string | null;
}

/** One job a batch send started: all the parts of one repository and kind. */
export interface BatchJob {
  repo: string;
  kind: SendKind;
  parts: number;
  job_id: string | null;
  error: string | null;
}

/**
 * Which owner's map a call names: the owner, `""` for the map of no owner, or undefined to let the server pick the
 * one map of the slug the viewer sees (an address from before owners, or an older backend).
 */
export type MapOwner = string | undefined;

/** `?owner=` for a call, only when the owner is known: an older backend is never sent one. */
function ownerParams(owner: MapOwner): { owner?: string } {
  return owner === undefined ? {} : { owner };
}

/** The owner a map's page and its calls name it by: `""` for a map of no owner, undefined when the backend says none. */
export function ownerOf(map: { owner?: string | null }): MapOwner {
  return map.owner === undefined ? undefined : (map.owner ?? "");
}

/** A map's page: with `?owner=` when its owner is known, so the link keeps opening it after another owner's upload. */
export function mapPath(map: { slug: string; owner?: string | null }): string {
  const owner = ownerOf(map);
  const base = `/maps/${encodeURIComponent(map.slug)}`;
  return owner === undefined ? base : `${base}?owner=${encodeURIComponent(owner)}`;
}

export async function fetchMaps(): Promise<MapSummary[]> {
  const { data } = await api.get<{ maps: MapSummary[] }>("/maps");
  return data.maps;
}

export async function fetchMap(slug: string, owner?: MapOwner): Promise<MapGraph> {
  const { data } = await api.get<MapGraph>(`/maps/${encodeURIComponent(slug)}`, {
    params: ownerParams(owner),
    timeout: 120_000,
  });
  return data;
}

export async function fetchPart(slug: string, nodeId: string, owner?: MapOwner): Promise<PartView> {
  const { data } = await api.get<PartView>(`/maps/${encodeURIComponent(slug)}/parts/${encodeURIComponent(nodeId)}`, {
    params: ownerParams(owner),
  });
  return data;
}

export async function fetchMarks(slug: string, by: string, owner?: MapOwner): Promise<Record<string, MarkCounts>> {
  const { data } = await api.get<{ marks: Record<string, MarkCounts> }>(`/maps/${encodeURIComponent(slug)}/marks`, {
    params: { by, ...ownerParams(owner) },
  });
  return data.marks;
}

export async function fetchWork(slug: string, owner?: MapOwner): Promise<Record<string, WorkView>> {
  const { data } = await api.get<{ work: Record<string, WorkView> }>(`/maps/${encodeURIComponent(slug)}/work`, {
    params: ownerParams(owner),
  });
  return data.work;
}

export async function markPart(
  slug: string,
  body: { node_id: string; verdict: Verdict | "clear"; note?: string; by: string },
  owner?: MapOwner,
): Promise<{ node_id: string; marks: MarkView[]; lead_id: string | null }> {
  const { data } = await api.post(`/maps/${encodeURIComponent(slug)}/marks`, body, { params: ownerParams(owner) });
  return data;
}

export async function sendParts(
  slug: string,
  body: { node_ids: string[]; kind?: SendKind; note?: string; by: string; run_with?: RunWith; together?: boolean },
  owner?: MapOwner,
): Promise<{ sent: number; failed: number; results: SendResult[]; jobs?: BatchJob[] }> {
  const { data } = await api.post(`/maps/${encodeURIComponent(slug)}/send`, body, {
    params: ownerParams(owner),
    timeout: 120_000,
  });
  return data;
}

/** Admin: give a map to another owner, or to none (`null`); its marks, sends and leads go with it. */
export async function setMapOwner(
  slug: string,
  owner: MapOwner,
  to: string | null,
): Promise<{ slug: string; owner: string | null; changed: boolean }> {
  const { data } = await api.put(`/maps/${encodeURIComponent(slug)}/owner`, { owner: to }, { params: ownerParams(owner) });
  return data;
}
