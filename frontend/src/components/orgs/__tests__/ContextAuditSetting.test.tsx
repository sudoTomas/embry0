import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockPatch = vi.fn();
vi.mock("@/api/orgs", async () => {
  const actual = await vi.importActual<typeof import("@/api/orgs")>("@/api/orgs");
  return { ...actual, patchOrganization: (...a: unknown[]) => mockPatch(...a) };
});

import type { Organization } from "@/api/orgs";
import { ContextAuditSetting } from "../ContextAuditSetting";
import { org } from "./fixtures";

function renderSetting(o: Organization, isAdmin = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContextAuditSetting org={o} isAdmin={isAdmin} />
    </QueryClientProvider>,
  );
}

const days = () => screen.getByTestId("org-context-audit-days") as HTMLInputElement;

beforeEach(() => {
  mockPatch.mockReset().mockImplementation(async (owner: string, settings: Record<string, unknown>) =>
    org({ owner, settings }),
  );
});

describe("ContextAuditSetting", () => {
  it("shows the current interval, with nothing to save until it changes", () => {
    renderSetting(org({ settings: { context_audit_days: 7 } }));
    expect(days().value).toBe("7");
    expect(screen.getByLabelText("Days between context audits, empty for off")).toBe(days());
    expect(screen.queryByTestId("org-context-audit-save")).toBeNull();
    expect(screen.getByTestId("org-context-audit-off")).toHaveTextContent("Turn off");
  });

  it("has no checkbox and no gold, like the rediscovery setting beside it", () => {
    renderSetting(org({ settings: { context_audit_days: 7 } }));
    const setting = screen.getByTestId("org-context-audit-setting");
    expect(within(setting).queryByRole("checkbox")).toBeNull();
    expect(setting.innerHTML).not.toMatch(/accent-|bg-primary/);
  });

  it("saves a new interval with PATCH settings.context_audit_days", async () => {
    renderSetting(org({ settings: { context_audit_days: 7 } }));
    fireEvent.change(days(), { target: { value: "30" } });
    expect(screen.getByTestId("org-context-audit-current")).toHaveTextContent("now every 7 days");
    fireEvent.click(screen.getByTestId("org-context-audit-save"));
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("acme", { context_audit_days: 30 }));
    await waitFor(() => expect(screen.queryByTestId("org-context-audit-save")).toBeNull());
  });

  it("turns the schedule off with null: Turn off empties the field, and Save sends it", async () => {
    renderSetting(org({ settings: { context_audit_days: 7 } }));
    fireEvent.click(screen.getByTestId("org-context-audit-off"));
    expect(days().value).toBe("");
    expect(screen.getByText("(off)")).toBeInTheDocument();
    expect(mockPatch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("org-context-audit-save"));
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("acme", { context_audit_days: null }));
  });

  it("reads an owner off the schedule as off, and switches it on with a number of days", async () => {
    renderSetting(org({ settings: { rediscover_hours: 24 } }));
    expect(days().value).toBe("");
    expect(screen.getByText("(off)")).toBeInTheDocument();
    expect(screen.queryByTestId("org-context-audit-off")).toBeNull();
    fireEvent.change(days(), { target: { value: "1" } });
    expect(screen.getByTestId("org-context-audit-current")).toHaveTextContent("now off");
    fireEvent.click(screen.getByTestId("org-context-audit-save"));
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("acme", { context_audit_days: 1 }));
  });

  it("refuses an interval outside 1 to 365 before sending it", () => {
    renderSetting(org({ settings: { context_audit_days: 7 } }));
    for (const bad of ["0", "366", "2.5", "1e2", "-3", "weekly"]) {
      fireEvent.change(days(), { target: { value: bad } });
      expect(screen.getByTestId("org-context-audit-invalid")).toBeInTheDocument();
      expect(screen.getByTestId("org-context-audit-save")).toBeDisabled();
    }
    expect(mockPatch).not.toHaveBeenCalled();
  });

  it("shows the server's refusal", async () => {
    mockPatch.mockRejectedValue({ response: { status: 403, data: { detail: "admin tier required" } } });
    renderSetting(org({ settings: { context_audit_days: 7 } }));
    fireEvent.change(days(), { target: { value: "14" } });
    fireEvent.click(screen.getByTestId("org-context-audit-save"));
    expect(await screen.findByTestId("org-context-audit-error")).toHaveTextContent("requires admin scope");
  });

  it("cancels an edit back to the stored value", () => {
    renderSetting(org({ settings: { context_audit_days: 7 } }));
    fireEvent.change(days(), { target: { value: "48" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(days().value).toBe("7");
  });

  it("reads the interval as words without admin scope", () => {
    renderSetting(org({ settings: { context_audit_days: 1 } }), false);
    expect(screen.getByTestId("org-context-audit-setting")).toHaveTextContent("Audits agent instructions every 1 day.");
    expect(screen.queryByTestId("org-context-audit-days")).toBeNull();
    renderSetting(org({ settings: {} }), false);
    expect(screen.getAllByTestId("org-context-audit-setting")[1]).toHaveTextContent("The context audit is off.");
  });
});
