import { BUILTIN_IDENTITY, resolveAgentType } from "@/lib/agentIdentity";

/** Suffixes the executor appends to an agent's name when it records a stage; the first match is stripped. */
const STAGE_SUFFIXES = ["_asked_user", "_ask_user", "_retry_developer", "_complete", "_failed", "_retry", "_running", "_rerun", "_passed"];

/**
 * The agent type a jobs.current_stage value names, or null. The column holds
 * workflow stages (`developer_complete`, `qa_retry_developer`,
 * `output_finalized`), never agent types; only a stage whose stem is a
 * builtin agent names one.
 */
export function agentOfStage(stage: string | null | undefined): string | null {
  if (!stage) return null;
  const suffix = STAGE_SUFFIXES.find((s) => stage.endsWith(s));
  const stem = resolveAgentType(suffix ? stage.slice(0, -suffix.length) : stage);
  return Object.hasOwn(BUILTIN_IDENTITY, stem) ? stem : null;
}
