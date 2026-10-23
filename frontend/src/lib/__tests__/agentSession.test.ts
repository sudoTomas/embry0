import { describe, expect, it } from "vitest";
import { sessionAgentType } from "../agentSession";

describe("sessionAgentType", () => {
  it("names the agent_sessions row of a run's agent", () => {
    // The review step's events say `reviewer`; nodes/_review.py stores the row as `review`.
    expect(sessionAgentType("reviewer"), "a reviewer run must read the review row").toBe("review");
    expect(sessionAgentType("review")).toBe("review");
    // Every other writer stores the row under the agent's own type.
    for (const agent of ["developer", "triage", "qa", "pr-reviewer", "onboarding"]) {
      expect(sessionAgentType(agent), `${agent} stores its row under its own type`).toBe(agent);
    }
  });
});
