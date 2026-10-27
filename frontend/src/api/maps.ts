import { api } from "./client";
import type { RunWith } from "./runWith";

/** Knowledge maps (spec 2026-10-27): a scanner's map of a codebase or an organization. */
export interface MapSummary {
  slug: string;
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
  map: { slug: string; title: string; repo: string | null };
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

export async function fetchMaps(): Promise<MapSummary[]> {
  const { data } = await api.get<{ maps: MapSummary[] }>("/maps");
  return data.maps;
}

export async function fetchMap(slug: string): Promise<MapGraph> {
  const { data } = await api.get<MapGraph>(`/maps/${encodeURIComponent(slug)}`, { timeout: 120_000 });
  return data;
}

export async function fetchPart(slug: string, nodeId: string): Promise<PartView> {
  const { data } = await api.get<PartView>(`/maps/${encodeURIComponent(slug)}/parts/${encodeURIComponent(nodeId)}`);
  return data;
}

export async function fetchMarks(slug: string, by: string): Promise<Record<string, MarkCounts>> {
  const { data } = await api.get<{ marks: Record<string, MarkCounts> }>(`/maps/${encodeURIComponent(slug)}/marks`, {
    params: { by },
  });
  return data.marks;
}

export async function fetchWork(slug: string): Promise<Record<string, WorkView>> {
  const { data } = await api.get<{ work: Record<string, WorkView> }>(`/maps/${encodeURIComponent(slug)}/work`);
  return data.work;
}

export async function markPart(
  slug: string,
  body: { node_id: string; verdict: Verdict | "clear"; note?: string; by: string },
): Promise<{ node_id: string; marks: MarkView[]; lead_id: string | null }> {
  const { data } = await api.post(`/maps/${encodeURIComponent(slug)}/marks`, body);
  return data;
}

export async function sendParts(
  slug: string,
  body: { node_ids: string[]; kind?: SendKind; note?: string; by: string; run_with?: RunWith },
): Promise<{ sent: number; failed: number; results: SendResult[] }> {
  const { data } = await api.post(`/maps/${encodeURIComponent(slug)}/send`, body, { timeout: 120_000 });
  return data;
}
