import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

// The budget limits are the settings document's `budget` section (#171): the
// tab reads them from GET /config/settings/effective and writes the changed
// fields through PATCH /config/settings/budget. The API client is the seam,
// so each test sees the requests the tab really makes.
const mockGet = vi.fn<(url: string, config?: unknown) => Promise<{ data: unknown }>>();
const mockPatch = vi.fn<(url: string, body: unknown, config?: unknown) => Promise<{ data: unknown }>>();
vi.mock("@/api/client", () => ({
  api: {
    get: (url: string, config?: unknown) => mockGet(url, config),
    patch: (url: string, body: unknown, config?: unknown) => mockPatch(url, body, config),
  },
}));

let admin = true;
vi.mock("@/stores/authStore", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/stores/authStore")>()),
  useIsAdmin: () => admin,
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

import GeneralTab from "../GeneralTab";

const LIMITS = {
  max_budget_per_job_usd: 12.5,
  daily_cap_usd: 75,
  monthly_cap_usd: 900,
  rate_limit_per_author_per_hour: 4,
  overrun_mode: "hard",
};

function key(name: string, value: unknown) {
  return {
    key: name,
    value,
    default: value,
    source: "document",
    set_by: null,
    set_at: null,
    env: null,
    env_still_set: false,
    overridable: [],
    refused: null,
    description: "",
  };
}

const EFFECTIVE = {
  generation: 3,
  unknown_sections: [],
  unknown_keys: [],
  sections: [
    { name: "asks", summary: "", keys: [key("daily_cap_usd", 1)] },
    {
      name: "budget",
      summary: "",
      keys: [...Object.entries(LIMITS).map(([name, value]) => key(name, value)), key("fix_job_usd", 2)],
    },
  ],
};

const DOCUMENT = { scope: { layer: "deployment" }, generation: 3, data: {}, yaml_text: "", allowlist_covers: true };

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <GeneralTab />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  admin = true;
  mockGet.mockReset();
  mockPatch.mockReset();
  mockGet.mockImplementation(async (url: string) => {
    if (url === "/config/settings/effective") return { data: EFFECTIVE };
    if (url === "/config/settings") return { data: DOCUMENT };
    throw new Error(`the General tab asked for ${url}`);
  });
  mockPatch.mockResolvedValue({ data: { generation: 4, revision_id: 9, changes: [] } });
});

describe("GeneralTab", () => {
  it("shows the budget section's limits in effect", async () => {
    renderTab();
    expect(await screen.findByLabelText("Daily budget cap (USD)")).toHaveValue(75);
    expect(screen.getByLabelText("Max budget per job (USD)")).toHaveValue(12.5);
    expect(screen.getByLabelText("Monthly budget cap (USD)")).toHaveValue(900);
    expect(screen.getByLabelText(/Rate limit/)).toHaveValue(4);
    expect(screen.getByLabelText("Overrun mode")).toHaveValue("hard");
    expect(mockGet).not.toHaveBeenCalledWith("/config/budget", expect.anything());
  });

  it("writes only the changed fields through PATCH /config/settings/budget", async () => {
    renderTab();
    fireEvent.change(await screen.findByLabelText("Daily budget cap (USD)"), { target: { value: "40" } });
    fireEvent.change(screen.getByLabelText("Overrun mode"), { target: { value: "soft" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(mockPatch).toHaveBeenCalledTimes(1));
    const [url, body] = mockPatch.mock.calls[0];
    expect(url).toBe("/config/settings/budget");
    expect(body).toEqual({ daily_cap_usd: 40, overrun_mode: "soft" });
  });

  it("shows a refused field's reason next to it", async () => {
    mockPatch.mockRejectedValue({
      response: {
        status: 422,
        data: { detail: [{ loc: ["budget", "daily_cap_usd"], type: "greater_than_equal", msg: "Input should be at least 0" }] },
      },
    });
    renderTab();
    fireEvent.change(await screen.findByLabelText("Daily budget cap (USD)"), { target: { value: "41" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Input should be at least 0");
    expect(screen.getByLabelText("Daily budget cap (USD)")).toHaveAttribute("aria-invalid", "true");
  });

  it("shows the limits read-only to anyone but an admin", async () => {
    admin = false;
    renderTab();
    expect(await screen.findByLabelText("Daily budget cap (USD)")).toBeDisabled();
    expect(screen.getByLabelText("Overrun mode")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(screen.getByTestId("budget-read-only")).toHaveTextContent("Only an admin can change the settings document.");
  });

  it("says where each limit's value comes from", async () => {
    const sources: Record<string, { source: string; set_by?: string | null; set_at?: string | null }> = {
      max_budget_per_job_usd: { source: "bootstrap", set_by: "embry0:bootstrap", set_at: "2026-10-30T08:00:00Z" },
      daily_cap_usd: { source: "document", set_by: "alex@example.com", set_at: "2026-10-30T09:30:00Z" },
      monthly_cap_usd: { source: "default" },
      rate_limit_per_author_per_hour: { source: "document", set_by: null, set_at: "2026-10-30T10:00:00Z" },
    };
    mockGet.mockImplementation(async (url: string) => {
      if (url === "/config/settings") return { data: DOCUMENT };
      return {
        data: {
          ...EFFECTIVE,
          sections: [
            {
              name: "budget",
              summary: "",
              keys: Object.entries(LIMITS).map(([name, value]) => ({ ...key(name, value), ...(sources[name] ?? {}) })),
            },
          ],
        },
      };
    });
    renderTab();
    expect(await screen.findByTestId("monthly_cap_usd-source")).toHaveTextContent(/^Default$/);
    expect(screen.getByTestId("max_budget_per_job_usd-source")).toHaveTextContent(/^Imported at upgrade on /);
    expect(screen.getByTestId("daily_cap_usd-source")).toHaveTextContent(/^Set by alex@example\.com on /);
    expect(screen.getByTestId("rate_limit_per_author_per_hour-source")).toHaveTextContent(/^Set by someone else on /);
  });

  it("shows the limits enforced while the import has not happened, and where they are read from", async () => {
    // The review's case: the import failed over a saved row of 2 / 20 / hard. The tab must show the caps
    // enforced, so an operator who lowers the daily cap lowers it from 20, not from the default 100.
    const enforced: Record<string, { value: unknown; label: string; read_from: string }> = {
      max_budget_per_job_usd: { value: 2, read_from: "row", label: "the budget_config table" },
      daily_cap_usd: { value: 20, read_from: "row", label: "the budget_config table" },
      overrun_mode: { value: "hard", read_from: "row", label: "the budget_config table" },
      monthly_cap_usd: { value: 500, read_from: "default", label: "the default" },
      rate_limit_per_author_per_hour: { value: 3, read_from: "variable", label: "RATE_LIMIT_PER_AUTHOR_PER_HOUR" },
    };
    mockGet.mockImplementation(async (url: string) => {
      if (url === "/config/settings") return { data: DOCUMENT };
      return {
        data: {
          ...EFFECTIVE,
          sections: [
            {
              name: "budget",
              summary: "",
              keys: Object.entries(LIMITS).map(([name, value]) => {
                const pending = enforced[name];
                if (!pending) return key(name, value);
                return {
                  ...key(name, pending.value),
                  source: "not_imported",
                  not_imported: { read_from: pending.read_from, label: pending.label },
                };
              }),
            },
          ],
        },
      };
    });
    renderTab();
    expect(await screen.findByLabelText("Daily budget cap (USD)")).toHaveValue(20);
    expect(screen.getByLabelText("Max budget per job (USD)")).toHaveValue(2);
    expect(screen.getByLabelText("Overrun mode")).toHaveValue("hard");
    expect(screen.getByTestId("daily_cap_usd-source")).toHaveTextContent(
      /^Not imported yet: read from the budget_config table$/,
    );
    expect(screen.getByTestId("rate_limit_per_author_per_hour-source")).toHaveTextContent(
      /^Not imported yet: read from RATE_LIMIT_PER_AUTHOR_PER_HOUR$/,
    );
    expect(screen.getByTestId("monthly_cap_usd-source")).toHaveTextContent(/^Not imported yet: the default$/);
  });

  it("names the providers page in sentence case, as its section title and the sidebar do", async () => {
    renderTab();
    expect(await screen.findByRole("button", { name: "Open LLM providers" })).toBeInTheDocument();
    expect(screen.getByText(/connection tests live on the LLM providers page\./)).toBeInTheDocument();
    expect(screen.queryByText(/LLM Providers/)).toBeNull();
  });
});
