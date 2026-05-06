import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { BootPhasePanel } from "../BootPhasePanel";
import type { BootPhaseDetail } from "@/lib/types";

describe("BootPhasePanel", () => {
  it("renders nothing when boot_phase is null", () => {
    const { container } = render(<BootPhasePanel boot_phase={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when boot_phase is undefined", () => {
    const { container } = render(<BootPhasePanel boot_phase={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the failed_checks list and stdout tail when outcome=timeout", () => {
    const bp: BootPhaseDetail = {
      outcome: "timeout",
      attempts: 12,
      duration_ms: 600_000,
      failed_checks: [
        "http://localhost:3000/health: got 503 expected 200",
      ],
      boot_stdout_tail:
        "[next-server] info: ready - started server on http://localhost:3000\n",
    };
    render(<BootPhasePanel boot_phase={bp} />);

    // Header text bundles outcome, attempts, and elapsed seconds.
    expect(
      screen.getByText(/Boot phase:/i, { selector: "header" }),
    ).toHaveTextContent("Boot phase: timeout");
    expect(
      screen.getByText(/Boot phase:/i, { selector: "header" }),
    ).toHaveTextContent("12 attempts");

    // Failed checks list surfaces the URL + the got/expected diff.
    expect(
      screen.getByText("http://localhost:3000/health: got 503 expected 200"),
    ).toBeInTheDocument();

    // The collapsible <details> exposes the stdout tail.
    expect(screen.getByText(/started server on/)).toBeInTheDocument();
  });

  it("hides the failed_checks block when none and the stdout block when empty", () => {
    const bp: BootPhaseDetail = {
      outcome: "startup_failed",
      attempts: 1,
      duration_ms: 250,
      failed_checks: [],
      boot_stdout_tail: "",
    };
    render(<BootPhasePanel boot_phase={bp} />);
    expect(screen.queryByText(/Failed checks/i)).toBeNull();
    expect(screen.queryByText(/Boot stdout/i)).toBeNull();
  });
});
