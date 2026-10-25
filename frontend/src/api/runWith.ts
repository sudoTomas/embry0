import { api } from "./client";

/** Run with (spec 2026-10-25): the model and the stored key a person chose for one job.
 * Both empty is the usual run: each agent's own model, the key its bindings pick. */
export interface RunWith {
  /** Every agent of the job runs this model. */
  model?: string;
  /** This stored LLM key serves its provider for the job. */
  credential_id?: string;
}

/** A stored LLM key a job on the repository may use. */
export interface RunWithKey {
  id: string;
  label: string;
  provider: string;
  /** The most specific binding that reaches the repository. */
  scope: "repo" | "owner" | "global";
  status: string | null;
}

export async function fetchRunWithKeys(repo?: string | null): Promise<RunWithKey[]> {
  const { data } = await api.get<{ keys: RunWithKey[] }>("/run-with/keys", { params: repo ? { repo } : {} });
  return data.keys;
}

/** The choice as a request body field: nothing at all for the usual run. */
export function runWithBody(choice: RunWith | null | undefined): { run_with?: RunWith } {
  if (!choice || (!choice.model && !choice.credential_id)) return {};
  return {
    run_with: {
      ...(choice.model ? { model: choice.model } : {}),
      ...(choice.credential_id ? { credential_id: choice.credential_id } : {}),
    },
  };
}
