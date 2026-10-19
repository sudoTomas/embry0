import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockPatch = vi.fn();
vi.mock("@/api/orgs", async () => {
  const actual = await vi.importActual<typeof import("@/api/orgs")>("@/api/orgs");
  return { ...actual, patchOrganization: (...a: unknown[]) => mockPatch(...a) };
});

import type { Organization } from "@/api/orgs";
import { RediscoverSetting } from "../RediscoverSetting";
import { org } from "./fixtures";

function renderSetting(o: Organization, isAdmin = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RediscoverSetting org={o} isAdmin={isAdmin} />
    </QueryClientProvider>,
  );
}

const hours = () => screen.getByTestId("org-rediscover-hours") as HTMLInputElement;

beforeEach(() => {
  mockPatch.mockReset().mockImplementation(async (owner: string, settings: Record<string, unknown>) =>
    org({ owner, settings }),
  );
});

describe("RediscoverSetting", () => {
  it("shows the current interval, with nothing to save until it changes", () => {
    renderSetting(org({ settings: { rediscover_hours: 24 } }));
    expect(hours().value).toBe("24");
    expect(screen.getByLabelText("Hours between discovery runs, empty for off")).toBe(hours());
    expect(screen.queryByTestId("org-rediscover-save")).toBeNull();
    expect(screen.getByTestId("org-rediscover-off")).toHaveTextContent("Turn off");
  });

  it("has no checkbox and no gold: the act panel's need checkboxes keep the page's one selection style", () => {
    renderSetting(org({ settings: { rediscover_hours: 24 } }));
    const setting = screen.getByTestId("org-rediscover-setting");
    expect(within(setting).queryByRole("checkbox")).toBeNull();
    expect(setting.innerHTML).not.toMatch(/accent-|bg-primary/);
  });

  it("saves a new interval with PATCH settings.rediscover_hours", async () => {
    renderSetting(org({ settings: { rediscover_hours: 24 } }));
    fireEvent.change(hours(), { target: { value: "6" } });
    expect(screen.getByTestId("org-rediscover-current")).toHaveTextContent("now every 24 hours");
    fireEvent.click(screen.getByTestId("org-rediscover-save"));
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("acme", { rediscover_hours: 6 }));
    await waitFor(() => expect(screen.queryByTestId("org-rediscover-save")).toBeNull());
  });

  it("turns the schedule off with null: Turn off empties the field, and Save sends it", async () => {
    renderSetting(org({ settings: { rediscover_hours: 24 } }));
    fireEvent.click(screen.getByTestId("org-rediscover-off"));
    expect(hours().value).toBe("");
    expect(screen.getByText("(off)")).toBeInTheDocument();
    expect(screen.getByTestId("org-rediscover-current")).toHaveTextContent("now every 24 hours");
    // Nothing is sent before Save.
    expect(mockPatch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("org-rediscover-save"));
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("acme", { rediscover_hours: null }));
  });

  it("reads an emptied field as off", async () => {
    renderSetting(org({ settings: { rediscover_hours: 24 } }));
    fireEvent.change(hours(), { target: { value: "  " } });
    expect(screen.queryByTestId("org-rediscover-invalid")).toBeNull();
    fireEvent.click(screen.getByTestId("org-rediscover-save"));
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("acme", { rediscover_hours: null }));
  });

  it("reads an owner off the schedule as off, and switches it on with a number of hours", async () => {
    renderSetting(org({ settings: {} }));
    expect(hours().value).toBe("");
    expect(screen.getByText("(off)")).toBeInTheDocument();
    expect(screen.queryByTestId("org-rediscover-off")).toBeNull();
    expect(screen.queryByTestId("org-rediscover-save")).toBeNull();
    fireEvent.change(hours(), { target: { value: "24" } });
    expect(screen.queryByText("(off)")).toBeNull();
    expect(screen.getByTestId("org-rediscover-current")).toHaveTextContent("now off");
    fireEvent.click(screen.getByTestId("org-rediscover-save"));
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("acme", { rediscover_hours: 24 }));
  });

  it("refuses an interval outside 1 to 168 before sending it", () => {
    renderSetting(org({ settings: { rediscover_hours: 24 } }));
    for (const bad of ["0", "169", "2.5", "1e2", "-3", "six"]) {
      fireEvent.change(hours(), { target: { value: bad } });
      expect(screen.getByTestId("org-rediscover-invalid")).toBeInTheDocument();
      expect(screen.getByTestId("org-rediscover-save")).toBeDisabled();
    }
    expect(mockPatch).not.toHaveBeenCalled();
  });

  it("shows the server's refusal", async () => {
    mockPatch.mockRejectedValue({ response: { status: 403, data: { detail: "admin tier required" } } });
    renderSetting(org({ settings: { rediscover_hours: 24 } }));
    fireEvent.change(hours(), { target: { value: "12" } });
    fireEvent.click(screen.getByTestId("org-rediscover-save"));
    expect(await screen.findByTestId("org-rediscover-error")).toHaveTextContent("requires admin scope");
  });

  it("cancels an edit back to the stored value", () => {
    renderSetting(org({ settings: { rediscover_hours: 24 } }));
    fireEvent.change(hours(), { target: { value: "48" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(hours().value).toBe("24");
  });

  it("reads the interval as words without admin scope", () => {
    renderSetting(org({ settings: { rediscover_hours: 24 } }), false);
    expect(screen.getByTestId("org-rediscover-setting")).toHaveTextContent("Rediscovers every 24 hours.");
    expect(screen.queryByTestId("org-rediscover-hours")).toBeNull();
    renderSetting(org({ settings: {} }), false);
    expect(screen.getAllByTestId("org-rediscover-setting")[1]).toHaveTextContent("Rediscovery is off.");
  });
});
