import type { Tier } from "./jobs";

export interface TriageRequest {
  title: string;
  body?: string;
  labels?: string[];
}

export interface TriageResponse {
  tier: Tier;
  confidence: number;
  reasons: string[];
}
