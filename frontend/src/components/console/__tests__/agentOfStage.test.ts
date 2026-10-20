import { describe, expect, it } from "vitest";
import { agentOfStage } from "../agentOfStage";

describe("agentOfStage", () => {
  // Every row is a value the executor writes to jobs.current_stage.
  it.each([
    ["developer_complete", "developer"],
    ["developer_retry", "developer"],
    ["review_complete", "review"],
    ["qa_running", "qa"],
    ["qa_rerun", "qa"],
    ["qa_ask_user", "qa"],
    ["qa_retry_developer", "qa"],
    ["triage_asked_user", "triage"],
    // verify has no agent row, so it names no agent
    ["verify_complete", null],
    ["verify_failed", null],
    ["init", null],
    ["initialized", null],
    ["init_complete", null],
    ["generic_agent_failed", null],
    ["skipped_duplicate", null],
    ["output_finalized", null],
    ["published", null],
    ["completed", null],
    ["failed", null],
    ["abandoned", null],
    ["", null],
    [null, null],
    [undefined, null],
  ])("maps the stage %j to %j", (stage, agent) => {
    expect(agentOfStage(stage)).toBe(agent);
  });
});
