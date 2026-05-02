import { api } from "./client";

export interface QAReadyCheckResult {
  url: string;
  status: number;
  duration_ms: number;
}

export interface QABootResult {
  command: string;
  duration_ms: number;
  ready_checks: QAReadyCheckResult[];
}

export interface QASeedResult {
  ran: boolean;
  command?: string;
  duration_ms?: number;
  exit_code?: number;
  note?: string;
}

export interface QAE2EResult {
  ran: boolean;
  command?: string;
  exit_code?: number;
  tests?: Record<string, number>;
  junit_artifact?: string;
}

export interface QAAcceptanceResult {
  criterion: string;
  status: "passed" | "failed" | "inconclusive";
  evidence: string[];
  notes?: string;
  console_errors: string[];
  network_failures: { url: string; status: number }[];
  log_excerpts: { service: string; lines: string }[];
}

export interface QAAnomaly {
  category: "console_error" | "network_error" | "unexpected_state" | "crash";
  detail: string;
  evidence_paths: string[];
  ts?: string;
}

export interface QAResult {
  schema_version: 1;
  job_id: string;
  attempt_n: number;
  phase_reached: "boot" | "seed" | "e2e" | "exploratory" | "report";
  overall: "passed" | "failed" | "inconclusive";
  boot: QABootResult;
  seed?: QASeedResult;
  e2e?: QAE2EResult;
  acceptance_results: QAAcceptanceResult[];
  anomalies: QAAnomaly[];
}

export interface QAAttemptListEntry {
  attempt_n: number;
  has_result_json: boolean;
  screenshots_count: number;
}

export async function listQaAttempts(jobId: string): Promise<QAAttemptListEntry[]> {
  const { data } = await api.get<{ attempts: QAAttemptListEntry[] }>(
    `/jobs/${jobId}/qa/attempts`,
  );
  return data.attempts;
}

export async function getQaResult(jobId: string, attemptN: number): Promise<QAResult> {
  const { data } = await api.get<QAResult>(
    `/jobs/${jobId}/qa/attempts/${attemptN}/result`,
  );
  return data;
}

export function artifactUrl(jobId: string, path: string): string {
  // The browser follows the 302 to a presigned GET; safe for <img>, <a>, etc.
  return `/api/v1/jobs/${jobId}/artifacts/${path}`;
}

export function latestScreenshotUrl(jobId: string, cacheBuster: number = 0): string {
  return `/api/v1/jobs/${jobId}/artifacts/screenshots/latest?_t=${cacheBuster}`;
}

export function logStreamUrl(jobId: string, service: string): string {
  return `/api/v1/jobs/${jobId}/artifacts/logs/${encodeURIComponent(service)}?follow=true`;
}
