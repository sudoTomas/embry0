import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchEffectiveSettings = vi.fn();
const mockSetSettingsKeys = vi.fn();
// The caps are the organization's layer of the budget section (#171), changed here (#170).
vi.mock("@/api/settings", () => ({
  fetchEffectiveSettings: (layer: unknown) => mockFetchEffectiveSettings(layer),
  fetchSettings: () => Promise.resolve({ generation: 6, allowlist_covers: true }),
  setSettingsKeys: (request: unknown, layer: unknown) => mockSetSettingsKeys(request, layer),
}));
let mockIsAdmin = true;
vi.mock("@/stores/authStore", () => ({ useIsAdmin: () => mockIsAdmin }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { OrgContextAuditCaps } from "../OrgContextAuditCaps";

function key(name: string, value: unknown, source: string, extra: Record<string, unknown> = {}) {
  return {
    key: name,
    value,
    default: null,
    source,
    set_by: null,
    set_at: null,
    env: null,
    env_still_set: false,
    overridable: ["organization"],
    refused: null,
    description: null,
    ...extra,
  };
}

function effective() {
  return {
    generation: 2,
    sections: [
      {
        name: "budget",
        summary: "",
        keys: [
          key("daily_cap_usd", 0, "default"),
          key("context_audit_job_usd", 5, "organization", { inherited: 2, set_by: "alex@example.com" }),
          key("context_audit_max_jobs_per_tick", 3, "default", { inherited: 3 }),
          key("fix_job_usd", 0.5, "not_imported", {
            inherited: 2,
            not_imported: { read_from: "organization", label: "acme's own settings" },
          }),
        ],
      },
    ],
    unknown_sections: [],
    unknown_keys: [],
  };
}

function renderCaps() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OrgContextAuditCaps owner="acme" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockIsAdmin = true;
  mockFetchEffectiveSettings.mockResolvedValue(effective());
  mockSetSettingsKeys.mockResolvedValue({ generation: 7, revision_id: 2, changes: [], managed_by: null });
});

describe("OrgContextAuditCaps", () => {
  it("shows each cap the organization reads, and where it comes from", async () => {
    renderCaps();
    expect(await screen.findByRole("combobox", { name: "Hard cap per audit (USD)" })).toHaveValue("own");
    expect(screen.getByRole("textbox", { name: "Hard cap per audit (USD)" })).toHaveValue("5");
    expect(screen.getByTestId("org-context-audit-cap-context_audit_job_usd-source")).toHaveTextContent(
      "Set for acme by alex@example.com",
    );
    expect(screen.getByRole("combobox", { name: "Audits started per wake" })).toHaveValue("inherit");
    expect(screen.getByTestId("org-context-audit-cap-fix_job_usd-source")).toHaveTextContent(
      "Not imported yet: read from acme's own settings",
    );
    expect(screen.queryByText(/daily/i)).toBeNull();
  });

  it("saves the caps changed into the organization's layer of the budget section", async () => {
    renderCaps();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Audits started per wake" }), "own");
    const count = screen.getByRole("textbox", { name: "Audits started per wake" });
    expect(count).toHaveValue("3"); // an own value starts from the one in effect
    await userEvent.clear(count);
    await userEvent.type(count, "1");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Hard cap per audit (USD)" }), "inherit");
    await userEvent.click(screen.getByTestId("org-context-audit-caps-save"));
    await waitFor(() =>
      expect(mockSetSettingsKeys).toHaveBeenCalledWith(
        { section: "budget", values: { context_audit_job_usd: null, context_audit_max_jobs_per_tick: 1 }, generation: 6 },
        { layer: "organization", owner: "acme" },
      ),
    );
  });

  it("refuses a cap out of range before asking, and changes nothing for a reader", async () => {
    const view = renderCaps();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Audits started per wake" }), "own");
    await userEvent.clear(screen.getByRole("textbox", { name: "Audits started per wake" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Audits started per wake" }), "51");
    expect(screen.getByTestId("org-context-audit-cap-context_audit_max_jobs_per_tick-problem")).toHaveTextContent(
      "A whole number from 0 to 50.",
    );
    expect(screen.getByTestId("org-context-audit-caps-save")).toBeDisabled();
    view.unmount();

    mockIsAdmin = false;
    renderCaps();
    expect(await screen.findByRole("combobox", { name: "Hard cap per audit (USD)" })).toBeDisabled();
    expect(screen.getByTestId("org-context-audit-caps-save")).toBeDisabled();
  });
});
