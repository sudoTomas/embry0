import { describe, expect, it, vi, beforeEach } from "vitest";

const mockGet = vi.fn();
vi.mock("../client", () => ({ api: { get: (...args: unknown[]) => mockGet(...args) } }));
import { fetchJobVerification, fetchAgentSession } from "../jobs";

const notFound = Object.assign(new Error("404"), { response: { status: 404 } });

describe("fetchJobVerification", () => {
  beforeEach(() => { mockGet.mockReset(); });
  it("returns the row", async () => {
    mockGet.mockResolvedValue({ status: 200, data: { job_id: "j", gate_verdict: "passed", report: {} } });
    expect((await fetchJobVerification("j"))?.gate_verdict).toBe("passed");
    expect(mockGet).toHaveBeenCalledWith("/jobs/j/verification");
  });
  it("404 resolves to null, not an error", async () => {
    mockGet.mockRejectedValue(notFound);
    await expect(fetchJobVerification("j"), "a job without a verification is not an error").resolves.toBeNull();
  });
  it("other errors still reject", async () => {
    mockGet.mockRejectedValue(Object.assign(new Error("500"), { response: { status: 500 } }));
    await expect(fetchJobVerification("j")).rejects.toThrow("500");
  });
});

describe("fetchAgentSession", () => {
  beforeEach(() => { mockGet.mockReset(); });
  it("returns the session", async () => {
    mockGet.mockResolvedValue({ status: 200, data: { job_id: "j", agent_type: "developer", mode: "anthropic_api", session_id: null, last_turn_at: null, messages: [] } });
    expect((await fetchAgentSession("j", "developer"))?.agent_type).toBe("developer");
    expect(mockGet).toHaveBeenCalledWith("/jobs/j/agents/developer/session");
  });
  it("404 and 204 resolve to null", async () => {
    mockGet.mockRejectedValueOnce(notFound);
    await expect(fetchAgentSession("j", "developer")).resolves.toBeNull();
    mockGet.mockResolvedValueOnce({ status: 204, data: "" });
    await expect(fetchAgentSession("j", "developer"), "a CLI-only session has no transcript").resolves.toBeNull();
  });
});
