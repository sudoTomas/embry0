import { isAxiosError } from "axios";
import { api } from "./client";
import { repoPath } from "./repoPath";

export interface QaConfigSaveResult {
  repo: string;
  updated_at: string;
}

/**
 * The free check of a test setup (`POST /repos/{repo}/-/qa-config/check`): no
 * model, no forge call, no sandbox, and nothing is written. `ok` is false
 * when every test run would stop on the setup (`problems`); `warnings` are
 * worth knowing but do not stop a run. `summary` is null when the text could
 * not be read.
 */
export interface QaCheckResult {
  ok: boolean;
  problems: { where: string; plain: string; fix: string }[];
  warnings: {
    code:
      | "live_address"
      | "deployed_target"
      | "deployed_without_guardrails"
      | "needs_docker"
      | "testing_off"
      | "login_value_missing";
    plain: string;
    confirm: boolean;
  }[];
  summary: {
    when: "auto" | "always" | "never";
    when_plain: string;
    apps: {
      name: string;
      target: "managed" | "deployed";
      start: string | null;
      address: string;
      address_is_local: boolean;
      criteria: string[];
      guardrails: number;
      machine: string;
      machine_has_browser: boolean;
    }[];
  } | null;
}

/**
 * Fetch the stored qa.yaml for a repo as raw YAML text (RAV-1251: the
 * embry0 DB is the source of truth).
 *
 * Returns null on 404: no config is stored for the repo, and QA treats it as
 * unconfigured. The orchestrator reads no in-repo `.embry0/qa.yaml`.
 */
export async function fetchQaConfig(fullRepo: string): Promise<string | null> {
  try {
    const { data } = await api.get<string>(repoPath(fullRepo, "qa-config"), {
      responseType: "text",
      // Identity transform — without it axios tries JSON.parse on the YAML.
      transformResponse: [(d: string) => d],
    });
    return data;
  } catch (error) {
    if (isAxiosError(error) && error.response?.status === 404) return null;
    throw error;
  }
}

/**
 * Save qa.yaml v2 text to the store. The backend schema-validates the body
 * and rejects with 422 (flat string detail, often a multi-line Pydantic
 * report) — the caller renders that next to the editor, so the global
 * validation toast is suppressed.
 */
export async function saveQaConfig(
  fullRepo: string,
  yamlText: string,
): Promise<QaConfigSaveResult> {
  const { data } = await api.put<QaConfigSaveResult>(
    repoPath(fullRepo, "qa-config"),
    yamlText,
    {
      headers: { "Content-Type": "application/yaml" },
      suppressValidationToast: true,
    },
  );
  return data;
}

/**
 * Check qa.yaml v2 text without storing it. The body goes the way
 * saveQaConfig sends it; a setup the check cannot read comes back as a
 * problem in the result, not as a 422.
 */
export async function checkQaConfig(fullRepo: string, yamlText: string): Promise<QaCheckResult> {
  const { data } = await api.post<QaCheckResult>(
    repoPath(fullRepo, "qa-config/check"),
    yamlText,
    {
      headers: { "Content-Type": "application/yaml" },
      suppressValidationToast: true,
    },
  );
  return data;
}

/** Remove the stored qa.yaml (idempotent 204). QA then treats the repo as unconfigured. */
export async function deleteQaConfig(fullRepo: string): Promise<void> {
  await api.delete(repoPath(fullRepo, "qa-config"));
}
