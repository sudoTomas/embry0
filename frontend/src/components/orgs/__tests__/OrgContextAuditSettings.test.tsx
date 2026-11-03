import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchEffectiveSettings = vi.fn();
const mockFetchSettings = vi.fn();
const mockSetSettingsKeys = vi.fn();

// The fields read and write the organization's layer of the settings document (#170).
vi.mock("@/api/settings", () => ({
  fetchEffectiveSettings: (layer: unknown) => mockFetchEffectiveSettings(layer),
  fetchSettings: (layer: unknown) => mockFetchSettings(layer),
  setSettingsKeys: (request: unknown, layer: unknown) => mockSetSettingsKeys(request, layer),
}));

let mockIsAdmin = true;
vi.mock("@/stores/authStore", () => ({ useIsAdmin: () => mockIsAdmin }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { OrgContextAuditSettings } from "../OrgContextAuditSettings";

const LAYER = { layer: "organization", owner: "acme" };

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
    overridable: ["organization", "repository"],
    refused: null,
    description: null,
    inherited: null,
    layers: [],
    ...extra,
  };
}

/** The organization's view: it sets its schedule (imported from its row) and nothing else. */
function effective(keys: Record<string, ReturnType<typeof key>> = {}) {
  return {
    generation: 3,
    scope: { layer: "organization", owner: "acme", repo: null },
    sections: [
      {
        name: "context_audit",
        summary: "",
        keys: [
          keys.days ?? key("days", 7, "organization", { set_by: "embry0:bootstrap", set_at: "2026-11-03T10:00:00Z" }),
          keys.open_prs ?? key("open_prs", false, "default", { inherited: false, default: false }),
          keys.files ??
            key("files", ["**/CLAUDE.md", "**/AGENTS.md"], "document", {
              inherited: ["**/CLAUDE.md", "**/AGENTS.md"],
              set_by: "alex@example.com",
              set_at: "2026-11-01T09:00:00Z",
            }),
          keys.exclude_dirs ?? key("exclude_dirs", null, "default"),
          key("check_minutes", 15, "default"),
        ],
      },
    ],
    unknown_sections: [],
    unknown_keys: [],
  };
}

function renderFields() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OrgContextAuditSettings owner="acme" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockIsAdmin = true;
  mockFetchEffectiveSettings.mockResolvedValue(effective());
  mockFetchSettings.mockResolvedValue({ generation: 4, allowlist_covers: true });
  mockSetSettingsKeys.mockResolvedValue({ generation: 5, revision_id: 9, changes: [], managed_by: null });
});

describe("OrgContextAuditSettings", () => {
  it("shows each key as the organization reads it and where its value comes from", async () => {
    renderFields();
    expect(await screen.findByRole("combobox", { name: "Schedule" })).toHaveValue("every");
    expect(screen.getByRole("textbox", { name: "Days between audits" })).toHaveValue("7");
    expect(screen.getByTestId("org-context-audit-days-source")).toHaveTextContent(
      /Imported from acme's own settings on/,
    );
    expect(screen.getByRole("combobox", { name: "Pull requests" })).toHaveValue("inherit");
    expect(screen.getByTestId("org-context-audit-open_prs-source")).toHaveTextContent("Default");
    expect(screen.getByRole("combobox", { name: "Instruction files" })).toHaveValue("inherit");
    expect(screen.getByTestId("org-context-audit-files-inherited")).toHaveTextContent("**/CLAUDE.md, **/AGENTS.md");
    expect(screen.getByTestId("org-context-audit-files-source")).toHaveTextContent(
      /The deployment's by alex@example.com on/,
    );
    expect(screen.getByTestId("org-context-audit-exclude_dirs-inherited")).toHaveTextContent("the built-in list");
    // Read from the organization's own layer, not the deployment's.
    expect(mockFetchEffectiveSettings).toHaveBeenCalledWith(LAYER);
    expect(screen.getByTestId("org-context-audit-save")).toBeDisabled();
  });

  it("saves only the keys changed, into the organization's layer, against the generation read", async () => {
    renderFields();
    const days = await screen.findByRole("textbox", { name: "Days between audits" });
    await userEvent.clear(days);
    await userEvent.type(days, "30");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Pull requests" }), "on");
    await userEvent.click(screen.getByTestId("org-context-audit-save"));
    await waitFor(() =>
      expect(mockSetSettingsKeys).toHaveBeenCalledWith(
        { section: "context_audit", values: { days: 30, open_prs: true }, generation: 4 },
        LAYER,
      ),
    );
    expect(toast.success).toHaveBeenCalledWith("Context audit settings saved");
  });

  it("turns the schedule off with false and hands a key back to the deployment with null", async () => {
    renderFields();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Schedule" }), "off");
    await userEvent.click(screen.getByTestId("org-context-audit-save"));
    await waitFor(() =>
      expect(mockSetSettingsKeys).toHaveBeenLastCalledWith(
        { section: "context_audit", values: { days: false }, generation: 4 },
        LAYER,
      ),
    );
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Schedule" }), "inherit");
    await userEvent.click(screen.getByTestId("org-context-audit-save"));
    await waitFor(() =>
      expect(mockSetSettingsKeys).toHaveBeenLastCalledWith(
        { section: "context_audit", values: { days: null }, generation: 4 },
        LAYER,
      ),
    );
  });

  it("names its own instruction files one per line, and refuses an empty list before asking", async () => {
    renderFields();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Instruction files" }), "own");
    const globs = screen.getByRole("textbox", { name: "Instruction file globs, one per line" });
    await userEvent.clear(globs);
    expect(screen.getByTestId("org-context-audit-files-problem")).toHaveTextContent("Name at least one glob");
    expect(screen.getByTestId("org-context-audit-save")).toBeDisabled();
    await userEvent.type(globs, "**/CONVENTIONS.md{enter}.roo/rules");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Skipped directories" }), "own");
    await userEvent.type(
      screen.getByRole("textbox", { name: "Skipped directories, separated by commas" }),
      "vendor, third_party",
    );
    await userEvent.click(screen.getByTestId("org-context-audit-save"));
    await waitFor(() =>
      expect(mockSetSettingsKeys).toHaveBeenCalledWith(
        {
          section: "context_audit",
          values: { files: ["**/CONVENTIONS.md", ".roo/rules"], exclude_dirs: ["vendor", "third_party"] },
          generation: 4,
        },
        LAYER,
      ),
    );
  });

  it("shows the server's refusal next to the key it names", async () => {
    mockSetSettingsKeys.mockRejectedValue({
      response: {
        status: 422,
        data: { detail: [{ loc: ["context_audit", "files"], msg: "Value error, glob 1: a glob holds only letters" }] },
      },
    });
    renderFields();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Instruction files" }), "own");
    const globs = screen.getByRole("textbox", { name: "Instruction file globs, one per line" });
    await userEvent.clear(globs);
    await userEvent.type(globs, "docs/?.md");
    await userEvent.click(screen.getByTestId("org-context-audit-save"));
    expect(await screen.findByTestId("org-context-audit-files-problem")).toHaveTextContent(
      "glob 1: a glob holds only letters",
    );
  });

  it("shows a value read from the organization's row until its layer imports it", async () => {
    mockFetchEffectiveSettings.mockResolvedValue(
      effective({
        days: key("days", 14, "not_imported", {
          not_imported: { read_from: "organization", label: "acme's own settings" },
        }),
      }),
    );
    renderFields();
    expect(await screen.findByTestId("org-context-audit-days-source")).toHaveTextContent(
      "Not imported yet: read from acme's own settings",
    );
    expect(screen.getByRole("combobox", { name: "Schedule" })).toHaveValue("inherit");
  });

  it("shows everything and changes nothing for someone who may not write the layer", async () => {
    mockIsAdmin = false;
    renderFields();
    const schedule = await screen.findByRole("combobox", { name: "Schedule" });
    expect(schedule).toBeDisabled();
    for (const name of ["Pull requests", "Instruction files", "Skipped directories"]) {
      expect(screen.getByRole("combobox", { name })).toBeDisabled();
    }
    expect(screen.getByRole("textbox", { name: "Days between audits" })).toBeDisabled();
    expect(screen.getByTestId("org-context-audit-save")).toBeDisabled();
    expect(within(screen.getByTestId("org-context-audit-settings")).getByTestId("org-context-audit-read-only")).not.toBeEmptyDOMElement();
  });
});
