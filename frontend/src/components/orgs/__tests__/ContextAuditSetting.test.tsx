import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchEffectiveSettings = vi.fn();
// The schedule is the settings document's context_audit.days, as the organization reads it (#170).
vi.mock("@/api/settings", () => ({
  fetchEffectiveSettings: (layer: unknown) => mockFetchEffectiveSettings(layer),
  fetchSettings: () => Promise.resolve({ generation: 1, allowlist_covers: true }),
  setSettingsKeys: vi.fn(),
}));
vi.mock("@/stores/authStore", () => ({ useIsAdmin: () => true }));

import { ContextAuditSetting } from "../ContextAuditSetting";

function view(days: unknown, source = "organization") {
  return {
    generation: 2,
    sections: [
      {
        name: "context_audit",
        summary: "",
        keys: [
          {
            key: "days",
            value: days,
            default: null,
            source,
            set_by: null,
            set_at: null,
            env: null,
            env_still_set: false,
            overridable: ["organization", "repository"],
            refused: null,
            description: null,
          },
        ],
      },
    ],
    unknown_sections: [],
    unknown_keys: [],
  };
}

function renderSetting() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ContextAuditSetting owner="acme" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => mockFetchEffectiveSettings.mockReset());

describe("ContextAuditSetting", () => {
  it("says how often the organization's repositories are audited, as its layer resolves it", async () => {
    mockFetchEffectiveSettings.mockResolvedValue(view(7));
    renderSetting();
    expect(await screen.findByTestId("org-context-audit-setting")).toHaveTextContent(
      "Audits agent instructions every 7 days.",
    );
    expect(mockFetchEffectiveSettings).toHaveBeenCalledWith({ layer: "organization", owner: "acme" });
    expect(screen.getByRole("link", { name: "Context audit settings" })).toHaveAttribute("href", "/?tab=settings");
  });

  it("says the audit is off when the schedule is false, null or out of range", async () => {
    for (const days of [false, null, 0]) {
      mockFetchEffectiveSettings.mockResolvedValue(view(days, "default"));
      const rendered = renderSetting();
      expect(await screen.findByTestId("org-context-audit-setting")).toHaveTextContent("The context audit is off.");
      rendered.unmount();
    }
  });

  it("shows nothing for a server without the section", async () => {
    mockFetchEffectiveSettings.mockResolvedValue({ generation: 1, sections: [], unknown_sections: [], unknown_keys: [] });
    const { container } = renderSetting();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(container).toBeEmptyDOMElement();
  });
});
