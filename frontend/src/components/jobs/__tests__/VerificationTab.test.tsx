import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { VerificationTab } from "../VerificationTab";
import type { JobVerification } from "@/lib/types";

const row: JobVerification = {
  job_id: "j", repo: "o/r", mode: "on", tier: "backend_only", gate_verdict: "failed", blocking: true,
  browser_qa: "skipped", human_merge_only: true, base_sha: "a".repeat(40), head_sha: "b".repeat(40),
  report: {
    tier: { tier: "backend_only", reason: "only backend files changed" },
    gate: {
      verdict: "failed", blocking: true, base_ref: "main",
      targets: [
        { target: "jvm", status: "failed", comparison: "base", new_failures: ["LoginTest.rejectsEmptyPassword"], preexisting: [], flaky: [], fixed: [], blocking: true },
        { target: "python", status: "passed", comparison: "none", new_failures: [], preexisting: ["old_test"], flaky: [], fixed: [], blocking: false },
      ],
    },
    browser_qa: { planned: "skipped", reason_code: "backend_only" },
    human_merge_only: true, human_merge_reasons: ["gate_new_failures"], config_error: null,
  },
  published_at: null, created_at: "2026-10-20T12:00:00Z", updated_at: "2026-10-20T12:00:00Z",
};

describe("VerificationTab", () => {
  it("shows the verdict, the tier, the gate table and the browser QA line", () => {
    render(<VerificationTab verification={row} />);
    const tab = screen.getByTestId("verification-tab");
    expect(within(tab).getByTestId("verdict").textContent).toMatch(/failed/i);
    expect(within(tab).getByTestId("verdict").getAttribute("data-verdict")).toBe("failed");
    expect(tab.textContent).toContain("backend_only");
    expect(tab.textContent).toContain("only backend files changed");
    const rows = within(tab).getAllByRole("row");
    expect(rows.length, "a header row plus one per target").toBe(3);
    expect(rows[1].textContent).toContain("jvm");
    expect(rows[1].textContent).toContain("1 new");
    expect(rows[2].textContent).toContain("1 pre-existing");
    expect(tab.textContent).toMatch(/browser qa.*skipped.*backend_only/i);
    expect(tab.textContent).toMatch(/human merge only/i);
    expect(tab.textContent).toContain("gate_new_failures");
  });

  it("renders a passed row with no targets and no reasons without blanks", () => {
    const passed: JobVerification = { ...row, gate_verdict: "passed", blocking: false, human_merge_only: false, report: { gate: { verdict: "passed", targets: [] }, browser_qa: { planned: "ran", verdict: "passed" }, human_merge_reasons: [] } };
    render(<VerificationTab verification={passed} />);
    expect(screen.getByTestId("verdict").getAttribute("data-verdict")).toBe("passed");
    expect(screen.getByText(/no gate targets/i)).toBeTruthy();
    expect(screen.queryByText(/human merge only/i)).toBeNull();
  });
});
