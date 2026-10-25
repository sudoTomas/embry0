import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { formatDateTime } from "@/lib/utils";
import { AccountWaitBanner } from "../AccountWaitBanner";

const UNTIL = "2026-10-26T00:00:00+00:00";

describe("AccountWaitBanner", () => {
  it("says what the job waits for and when it goes on, and offers try now and cancel", () => {
    const onTryNow = vi.fn();
    const onCancel = vi.fn();
    render(
      <AccountWaitBanner
        wait={{ provider: "anthropic", reason: "quota_exhausted", until: UNTIL, agent: "triage", model: "claude-sonnet-5" }}
        onTryNow={onTryNow}
        onCancel={onCancel}
      />,
    );
    const banner = screen.getByTestId("account-wait-banner");
    expect(banner).toHaveTextContent("Waiting for the anthropic account (quota exhausted)");
    expect(banner).toHaveTextContent(`Goes on at ${formatDateTime(UNTIL)}`);
    expect(banner).toHaveTextContent("triage on claude-sonnet-5 waits.");
    fireEvent.click(screen.getByRole("button", { name: /try now/i }));
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onTryNow).toHaveBeenCalledOnce();
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("names the local model when every slot is taken", () => {
    render(
      <AccountWaitBanner wait={{ provider: "custom", reason: "local_busy", until: UNTIL }} onTryNow={() => {}} onCancel={() => {}} />,
    );
    expect(screen.getByTestId("account-wait-banner")).toHaveTextContent("Waiting for a free slot on the local model");
  });
});
