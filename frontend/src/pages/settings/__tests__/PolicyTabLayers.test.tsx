import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router";
import { toast } from "sonner";
import { useAuthStore } from "@/stores/authStore";
import type {
  EffectiveSetting,
  EffectiveSettings,
  SettingsDocument,
  SettingsLayerRow,
  SettingsPreview,
  SettingsRevision,
} from "@/lib/types/settings";

const api = vi.hoisted(() => ({
  fetchSettings: vi.fn(),
  previewSettings: vi.fn(),
  saveSettings: vi.fn(),
  fetchEffectiveSettings: vi.fn(),
  fetchSettingsRevisions: vi.fn(),
  fetchSettingsRevision: vi.fn(),
  undoSettingsChange: vi.fn(),
  fetchSettingsLayers: vi.fn(),
}));
const lists = vi.hoisted(() => ({ fetchOrganizations: vi.fn(), fetchRepos: vi.fn() }));
vi.mock("@/api/settings", () => api);
vi.mock("@/api/orgs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/orgs")>()),
  fetchOrganizations: lists.fetchOrganizations,
}));
vi.mock("@/api/repos", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/repos")>()),
  fetchRepos: lists.fetchRepos,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

import PolicyTab from "../PolicyTab";

const WHEN = "2026-10-29T10:00:00Z";
const DEPLOYMENT_LAYER = { layer: "deployment" } as const;
const ACME = { layer: "organization", owner: "acme" } as const;
const GLOBEX = { layer: "organization", owner: "globex" } as const;
const GADGETS = { layer: "repository", repo: "acme/gadgets" } as const;
const ORG_KEYS = { asks: ["expire_days", "hours", "max_open_per_person", "remind_hours", "timezone", "weekdays"], sample: [] };

function doc(over: Partial<SettingsDocument>): SettingsDocument {
  return {
    scope: { layer: "deployment", owner: null, repo: null },
    yaml_text: "",
    data: {},
    schema_version: 1,
    generation: 0,
    updated_by: null,
    updated_at: null,
    sections: ["asks", "sample"],
    settable: ORG_KEYS,
    unknown_sections: [],
    unknown_keys: [],
    managed_by: null,
    allowlist_covers: true,
    ...over,
  };
}

const DEPLOYMENT_DOC = doc({ yaml_text: "asks:\n  hours: \"09-18\"\n", generation: 5, updated_by: "alex@example.com", updated_at: WHEN });
const ACME_TEXT = "# acme works around the clock\nasks:\n  hours: \"00-24\"\n";
const ACME_DOC = doc({
  scope: { layer: "organization", owner: "acme", repo: null },
  yaml_text: ACME_TEXT,
  data: { asks: { hours: "00-24" } },
  generation: 2,
  updated_by: "alex@example.com",
  updated_at: WHEN,
});
const GLOBEX_DOC = doc({ scope: { layer: "organization", owner: "globex", repo: null } });
const REPO_KEYS = { asks: [], sample: ["mode"] };
const WIDGETS_DOC = doc({ scope: { layer: "repository", owner: "acme", repo: "acme/widgets" }, settable: REPO_KEYS });
const GADGETS_DOC = doc({ scope: { layer: "repository", owner: "acme", repo: "acme/gadgets" }, settable: { asks: [], sample: [] } });

const DOCS: Record<string, SettingsDocument> = {
  deployment: DEPLOYMENT_DOC,
  "organization:acme": ACME_DOC,
  "organization:globex": GLOBEX_DOC,
  "repository:acme/widgets": WIDGETS_DOC,
  "repository:acme/gadgets": GADGETS_DOC,
};

function keyOf(layer: { layer: string; owner?: string; repo?: string }): string {
  if (layer.layer === "organization") return `organization:${layer.owner}`;
  if (layer.layer === "repository") return `repository:${layer.repo}`;
  return "deployment";
}

const STORED: SettingsLayerRow[] = [
  {
    layer: "organization",
    owner: "acme",
    repo: null,
    stored_as: null,
    generation: 2,
    updated_by: "alex@example.com",
    updated_at: WHEN,
    keys: ["asks.hours"],
  },
];

function entry(layer: "deployment" | "organization" | "repository", over: Record<string, unknown>) {
  const scope =
    layer === "deployment"
      ? { layer, owner: null, repo: null }
      : layer === "organization"
        ? { layer, owner: "acme", repo: null }
        : { layer, owner: "acme", repo: "acme/widgets" };
  return { ...scope, set: false, value: null, set_by: null, set_at: null, settable: true, refused: null, ...over };
}

function setting(over: Partial<EffectiveSetting>): EffectiveSetting {
  return {
    key: "hours",
    value: "09-18",
    default: "09-18",
    source: "default",
    set_by: null,
    set_at: null,
    env: null,
    env_still_set: false,
    overridable: ["organization"],
    refused: null,
    description: null,
    ...over,
  };
}

/** acme's view: one key it overrides, one it inherits, one nothing sets, one it may not set. */
const ACME_EFFECTIVE: EffectiveSettings = {
  generation: 7,
  scope: { layer: "organization", owner: "acme", repo: null },
  unknown_sections: [],
  unknown_keys: [],
  sections: [
    {
      name: "asks",
      summary: "When and how often embry0 asks a person about a decision.",
      keys: [
        setting({
          key: "hours",
          value: "00-24",
          source: "organization",
          set_by: "alex@example.com",
          set_at: WHEN,
          inherited: "09-18",
          layers: [entry("deployment", { set: true, value: "09-18" }), entry("organization", { set: true, value: "00-24" })],
        }),
        setting({
          key: "weekdays",
          value: "sun-thu",
          default: "mon-fri",
          source: "document",
          set_by: "alex@example.com",
          set_at: WHEN,
          inherited: "sun-thu",
          layers: [entry("deployment", { set: true, value: "sun-thu" }), entry("organization", {})],
        }),
        setting({
          key: "timezone",
          value: "UTC",
          default: "UTC",
          inherited: "UTC",
          layers: [entry("deployment", {}), entry("organization", {})],
        }),
        setting({
          key: "enabled",
          value: true,
          default: false,
          source: "document",
          overridable: [],
          inherited: true,
          layers: [entry("deployment", { set: true, value: true, settable: true }), entry("organization", { settable: false })],
        }),
      ],
    },
  ],
};

/** acme/widgets's view: a key from its organization it may not set, its own override, and a refused organization value. */
const WIDGETS_EFFECTIVE: EffectiveSettings = {
  generation: 7,
  scope: { layer: "repository", owner: "acme", repo: "acme/widgets" },
  unknown_sections: [],
  unknown_keys: [],
  sections: [
    {
      name: "asks",
      summary: "",
      keys: [
        setting({
          key: "hours",
          value: "00-24",
          source: "organization",
          set_by: "alex@example.com",
          set_at: WHEN,
          inherited: "00-24",
          layers: [
            entry("deployment", { set: true, value: "09-18" }),
            entry("organization", { set: true, value: "00-24" }),
            entry("repository", { settable: false }),
          ],
        }),
      ],
    },
    {
      name: "sample",
      summary: "",
      keys: [
        setting({
          key: "mode",
          value: "on",
          default: "shadow",
          source: "repository",
          overridable: ["repository"],
          inherited: "shadow",
          set_by: null,
          set_at: WHEN,
          layers: [
            entry("deployment", {}),
            entry("organization", { settable: false }),
            entry("repository", { set: true, value: "on", set_at: WHEN }),
          ],
        }),
        setting({
          key: "low",
          value: 10,
          default: 10,
          overridable: ["organization", "repository"],
          inherited: 10,
          layers: [
            entry("deployment", {}),
            entry("organization", { value: 70, refused: "high must not be below low" }),
            entry("repository", {}),
          ],
        }),
      ],
    },
  ],
};

const DEPLOYMENT_EFFECTIVE: EffectiveSettings = { generation: 7, sections: [], unknown_sections: [], unknown_keys: [] };

function effectiveFor(layer: { layer: string; owner?: string; repo?: string }): EffectiveSettings {
  const key = keyOf(layer);
  if (key === "organization:acme") return ACME_EFFECTIVE;
  if (key === "repository:acme/widgets") return WIDGETS_EFFECTIVE;
  return DEPLOYMENT_EFFECTIVE;
}

const ACME_REVISIONS: SettingsRevision[] = [
  {
    id: 21,
    what: "Changed: asks.hours",
    actor: "alex@example.com",
    source: "person",
    reason: "",
    created_at: WHEN,
    undoable: true,
    why_not: null,
  },
];

function httpError(status: number, detail: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } } });
}

function asAdmin() {
  useAuthStore.setState({ status: "authenticated", authMode: "session", user: { email: "a@x", name: "A", tier: "admin" } });
}
function asOperator() {
  useAuthStore.setState({ status: "authenticated", authMode: "session", user: { email: "o@x", name: "O", tier: "operator" } });
}

function Search() {
  return <p data-testid="search">{useLocation().search}</p>;
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <PolicyTab />
        <Search />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const picker = () => screen.findByTestId("policy-layer-picker") as Promise<HTMLSelectElement>;
const textarea = () => screen.findByTestId("settings-textarea");

beforeEach(() => {
  for (const mock of [...Object.values(api), ...Object.values(lists)]) mock.mockReset();
  api.fetchSettings.mockImplementation(async (layer) => DOCS[keyOf(layer)]);
  api.fetchEffectiveSettings.mockImplementation(async (layer) => effectiveFor(layer));
  api.fetchSettingsRevisions.mockImplementation(async (layer) => (keyOf(layer) === "organization:acme" ? ACME_REVISIONS : []));
  api.fetchSettingsLayers.mockResolvedValue(STORED);
  lists.fetchOrganizations.mockResolvedValue([{ owner: "acme" }, { owner: "globex" }]);
  lists.fetchRepos.mockResolvedValue([{ repo: "acme/widgets", active: true }, { repo: "acme/gadgets", active: true }]);
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.error).mockReset();
  asAdmin();
});
afterEach(() => vi.restoreAllMocks());

describe("the layer picker", () => {
  it("offers the deployment, every organization and repository, and marks the ones with a stored layer", async () => {
    renderAt("/settings?tab=policy");
    const select = await picker();
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(5));
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Deployment",
      "acme (layer, 1 key)",
      "globex",
      "acme/gadgets",
      "acme/widgets",
    ]);
    expect(select.value, "no layer in the URL is the deployment's document").toBe("deployment");
    expect(within(select).getByRole("group", { name: "Organizations" })).toBeInTheDocument();
    expect(within(select).getByRole("group", { name: "Repositories" })).toBeInTheDocument();
  });

  it("opens the layer the URL names and reads every card from that layer", async () => {
    renderAt("/settings?tab=policy&owner=Acme");
    expect((await picker()).value).toBe("organization:acme");
    expect(await textarea()).toHaveValue(ACME_TEXT);
    await waitFor(() => expect(api.fetchEffectiveSettings).toHaveBeenCalledWith(ACME));
    expect(api.fetchSettings).toHaveBeenCalledWith(ACME);
    expect(api.fetchSettingsRevisions).toHaveBeenCalledWith(ACME);
    expect(api.fetchSettings, "the deployment's document was read for an organization's page").not.toHaveBeenCalledWith(
      DEPLOYMENT_LAYER,
    );
    expect(screen.getByTestId("policy-layer-description")).toHaveTextContent(
      "acme's layer: what every repository under acme reads over the deployment's document",
    );
  });

  it("writes the picked layer into the URL, keeping the tab, and reads the picked layer", async () => {
    renderAt("/settings?tab=policy&owner=acme");
    await textarea();
    await userEvent.selectOptions(await picker(), "repository:acme/gadgets");
    await waitFor(() => expect(screen.getByTestId("search")).toHaveTextContent(/^\?tab=policy&repo=acme%2Fgadgets$/));
    await waitFor(() => expect(api.fetchSettings).toHaveBeenCalledWith(GADGETS));

    await userEvent.selectOptions(await picker(), "deployment");
    await waitFor(() => expect(screen.getByTestId("search")).toHaveTextContent(/^\?tab=policy$/));
    await waitFor(() => expect(api.fetchSettings).toHaveBeenCalledWith(DEPLOYMENT_LAYER));
  });

  it("asks before an unsaved edit is dropped for another layer, and stays when told to", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    renderAt("/settings?tab=policy&owner=acme");
    fireEvent.change(await textarea(), { target: { value: `${ACME_TEXT}# more\n` } });

    await userEvent.selectOptions(await picker(), "organization:globex");
    expect(confirm).toHaveBeenCalledWith("Leave your unsaved edit of acme's layer? It is not kept.");
    expect(screen.getByTestId("search")).toHaveTextContent(/^\?tab=policy&owner=acme$/);
    expect(await textarea(), "the edit was dropped though the person chose to stay").toHaveValue(`${ACME_TEXT}# more\n`);

    await userEvent.selectOptions(await picker(), "organization:globex");
    await waitFor(() => expect(screen.getByTestId("search")).toHaveTextContent(/^\?tab=policy&owner=globex$/));
    await waitFor(() => expect(screen.getByTestId("settings-textarea")).toHaveValue(""));
  });
});

describe("editing a layer", () => {
  it("starts a layer nobody wrote: says it inherits everything, and its first save sends If-Match 0", async () => {
    const text = "asks:\n  hours: \"08-17\"\n";
    const preview: SettingsPreview = {
      would_change: true,
      text_diff: "--- stored\n+++ proposed\n@@ -0,0 +1,2 @@\n+asks:\n+  hours: \"08-17\"\n",
      changes: [{ key: "asks.hours", before: null, after: "08-17", set_before: false, set_after: true }],
      unknown_sections: [],
      unknown_keys: [],
      layers_refused: [],
      layers_refused_total: 0,
    };
    api.previewSettings.mockResolvedValue(preview);
    api.saveSettings.mockResolvedValue({ generation: 1, revision_id: 30, changes: preview.changes, unknown_sections: [], unknown_keys: [] });
    renderAt("/settings?tab=policy&owner=globex");

    expect(await screen.findByTestId("settings-document-meta")).toHaveTextContent(
      "globex has no layer yet, so it inherits every key. Write the keys it sets and save to start one.",
    );
    fireEvent.change(await textarea(), { target: { value: text } });
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(api.previewSettings).toHaveBeenCalledWith(text, 0, GLOBEX);
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));
    expect(api.saveSettings).toHaveBeenCalledWith({ yamlText: text, generation: 0, reason: "" }, GLOBEX);
  });

  it("saves a stored layer against the generation it read", async () => {
    const text = `${ACME_TEXT}  timezone: Europe/Berlin\n`;
    api.previewSettings.mockResolvedValue({ would_change: true, text_diff: "", changes: [], unknown_sections: [], unknown_keys: [] });
    api.saveSettings.mockResolvedValue({ generation: 3, revision_id: 22, changes: [], unknown_sections: [], unknown_keys: [] });
    renderAt("/settings?tab=policy&owner=acme");

    expect(await screen.findByTestId("settings-document-meta")).toHaveTextContent("Generation 2, last changed by alex@example.com");
    fireEvent.change(await textarea(), { target: { value: text } });
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(api.previewSettings).toHaveBeenCalledWith(text, 2, ACME);
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));
    expect(api.saveSettings).toHaveBeenCalledWith({ yamlText: text, generation: 2, reason: "" }, ACME);
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Settings saved. Each loop reads them on its next tick."));
  });

  it("lists the keys the layer may set, per section, and says so when it may set none", async () => {
    renderAt("/settings?tab=policy&owner=acme");
    const settable = await screen.findByTestId("settings-settable");
    expect(settable).toHaveTextContent("An organization can set only these keys");
    expect(within(settable).getAllByTestId("settings-settable-section").map((li) => li.textContent)).toEqual([
      "asks: expire_days, hours, max_open_per_person, remind_hours, timezone, weekdays",
    ]);

    await userEvent.selectOptions(await picker(), "repository:acme/gadgets");
    await waitFor(() =>
      expect(screen.getByTestId("settings-settable")).toHaveTextContent(
        "A repository cannot set any key yet: no section lets it. Every value here is inherited.",
      ),
    );
  });

  it("marks each key the layer may not set when the server refuses it as not_overridable", async () => {
    api.previewSettings.mockRejectedValue(
      httpError(422, [
        {
          loc: ["asks", "enabled"],
          type: "not_overridable",
          msg: "'enabled' is the deployment's; an organization can set expire_days, hours, max_open_per_person, remind_hours, timezone, weekdays in 'asks'.",
        },
        { loc: ["asks", "hours"], type: "string_too_long", msg: "String should have at most 16 characters" },
      ]),
    );
    renderAt("/settings?tab=policy&owner=acme");
    fireEvent.change(await textarea(), { target: { value: "asks:\n  enabled: true\n  hours: \"08-17-and-more-text\"\n" } });
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));

    const [refused, other] = within(await screen.findByTestId("settings-problems")).getAllByTestId("settings-problem");
    expect(refused).toHaveTextContent(/^asks\.enabled Not settable by an organization: 'enabled' is the deployment's;/);
    expect(within(refused).getByText("Not settable by an organization")).toBeInTheDocument();
    expect(other, "only a not_overridable refusal is marked so").not.toHaveTextContent("Not settable");
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("names, before and after a deployment's save, the layer values it makes read as inherited", async () => {
    const refusal = {
      scope: { layer: "organization" as const, owner: "acme", repo: null },
      key: "sample.low",
      reason: "high must not be below low",
    };
    api.previewSettings.mockResolvedValue({
      would_change: true,
      text_diff: "",
      changes: [{ key: "sample.high", before: 80, after: 50, set_before: true, set_after: true }],
      unknown_sections: [],
      unknown_keys: [],
      layers_refused: [refusal],
      layers_refused_total: 3,
    });
    api.saveSettings.mockResolvedValue({
      generation: 6,
      revision_id: 40,
      changes: [],
      unknown_sections: [],
      unknown_keys: [],
      layers_refused: [refusal],
      layers_refused_total: 1,
    });
    renderAt("/settings?tab=policy");
    fireEvent.change(await textarea(), { target: { value: "sample:\n  high: 50\n" } });
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));

    const before = await screen.findByTestId("settings-preview-layers-refused");
    expect(before).toHaveTextContent("Saving would make these layer values read as inherited");
    expect(within(before).getByTestId("layers-refused-entry")).toHaveTextContent("acme: sample.low (high must not be below low)");
    expect(before).toHaveTextContent("And 2 more.");
    expect(within(before).queryByRole("link"), "a preview's list links away from the edit it previews").not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const after = await screen.findByTestId("settings-saved-layers-refused");
    expect(after).toHaveTextContent("Saved. These layer values now read as inherited");
    expect(within(after).getByRole("link", { name: "acme" })).toHaveAttribute("href", "/settings?tab=policy&owner=acme");
    expect(after).not.toHaveTextContent("more.");

    fireEvent.change(await textarea(), { target: { value: "sample:\n  high: 60\n" } });
    expect(screen.queryByTestId("settings-saved-layers-refused"), "a new edit clears the last save's answer").not.toBeInTheDocument();
  });

  it("names the layer values an undo makes read as inherited, each linked to its layer", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    api.undoSettingsChange.mockResolvedValue({
      generation: 3,
      revision_id: 23,
      changes: [],
      unknown_sections: [],
      unknown_keys: [],
      layers_refused: [
        {
          scope: { layer: "repository", owner: "acme", repo: "acme/widgets" },
          key: "sample.low",
          reason: "high must not be below low",
        },
      ],
      layers_refused_total: 1,
    });
    renderAt("/settings?tab=policy&owner=acme");
    const [newest] = await screen.findAllByTestId("settings-revision");
    await userEvent.click(within(newest).getByRole("button", { name: "Undo this change" }));

    expect(api.undoSettingsChange).toHaveBeenCalledWith(21);
    const undone = await screen.findByTestId("settings-undo-layers-refused");
    expect(undone).toHaveTextContent("Undone. These layer values now read as inherited");
    expect(within(undone).getByRole("link", { name: "acme/widgets" })).toHaveAttribute(
      "href",
      "/settings?tab=policy&repo=acme%2Fwidgets",
    );
  });

  it("says to register an organization nothing registers, with no Retry", async () => {
    api.fetchSettings.mockImplementation(async (layer) => {
      if (keyOf(layer) === "organization:initech") throw httpError(404, "No organization initech is registered; register it first.");
      return DOCS[keyOf(layer)];
    });
    renderAt("/settings?tab=policy&owner=initech");
    expect(await screen.findByText("No organization initech is registered; register it first.")).toBeInTheDocument();
    expect((await picker()).value).toBe("organization:initech");
  });
});

describe("values in effect for a layer", () => {
  it("shows what an organization's value overrides, what it inherits and from where, and dims a key it may not set", async () => {
    renderAt("/settings?tab=policy&owner=acme");
    const [hours, weekdays, timezone, enabled] = await screen.findAllByTestId("effective-key");

    expect(within(hours).getByTestId("effective-value")).toHaveTextContent('"00-24"');
    expect(hours).toHaveTextContent("Set by acme");
    expect(within(hours).getByTestId("effective-provenance")).toHaveTextContent('Overrides "09-18" from the deployment.');
    expect(hours).toHaveTextContent("set by alex@example.com on");

    expect(within(weekdays).getByTestId("effective-provenance")).toHaveTextContent("Inherited from the deployment.");
    expect(weekdays).toHaveTextContent("Set by the deployment");
    expect(within(timezone).getByTestId("effective-provenance")).toHaveTextContent("The default: nothing sets it.");

    expect(enabled).toHaveAttribute("data-settable", "false");
    expect(enabled.className).toContain("opacity-60");
    expect(within(enabled).getByTestId("effective-provenance")).toHaveTextContent(
      "An organization cannot set this key: it is the deployment's.",
    );
    expect(enabled).toHaveTextContent("Not settable by an organization");
    for (const settable of [hours, weekdays, timezone]) expect(settable).toHaveAttribute("data-settable", "true");
  });

  it("shows a repository what it reads from its organization, its own override, and a refused value", async () => {
    renderAt("/settings?tab=policy&repo=acme/widgets");
    const [hours, mode, low] = await screen.findAllByTestId("effective-key");

    expect(hours).toHaveTextContent("Set by acme");
    expect(hours).toHaveAttribute("data-settable", "false");
    expect(within(hours).getByTestId("effective-provenance")).toHaveTextContent(
      "A repository cannot set this key: it is set per organization.",
    );

    expect(mode).toHaveTextContent("Set by acme/widgets");
    // A badge never wraps, and a repository's name can be wider than a phone: these words may.
    expect(within(mode).getByTestId("effective-source")).toHaveClass("whitespace-normal");
    expect(within(mode).getByTestId("effective-provenance")).toHaveTextContent('Overrides the default "shadow".');

    expect(within(low).getByTestId("effective-refused")).toHaveTextContent(
      "The value acme stores is refused, so the inherited value is read: high must not be below low",
    );
  });
});

describe("who may change a layer", () => {
  it("lets an admin key limited to some repositories read an organization's layer but change nothing", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    api.fetchSettings.mockImplementation(async (layer) => ({ ...DOCS[keyOf(layer)], allowlist_covers: false }));
    renderAt("/settings?tab=policy&owner=acme");

    expect(await textarea()).toHaveAttribute("readonly");
    await waitFor(() =>
      expect(screen.getByTestId("settings-read-only")).toHaveTextContent(
        "This API key is limited to a list of repositories, so it cannot change an organization's layer",
      ),
    );
    expect(screen.getByRole("button", { name: "Preview" })).toBeDisabled();
    const [newest] = await screen.findAllByTestId("settings-revision");
    await waitFor(() => expect(within(newest).getByRole("button", { name: "Undo this change" })).toBeDisabled());
    await userEvent.click(within(newest).getByRole("button", { name: "Undo this change" })).catch(() => undefined);
    expect(api.undoSettingsChange).not.toHaveBeenCalled();
    expect(await screen.findAllByTestId("effective-key"), "the values in effect are still shown").toHaveLength(4);
  });

  it("lets that key change a repository's layer its allowlist covers, and no other", async () => {
    api.fetchSettings.mockImplementation(async (layer) => ({
      ...DOCS[keyOf(layer)],
      allowlist_covers: keyOf(layer) === "repository:acme/widgets",
    }));
    renderAt("/settings?tab=policy&repo=acme/widgets");
    const covered = await screen.findByRole("textbox", { name: "acme/widgets's layer (YAML)" });
    expect(covered).not.toHaveAttribute("readonly");
    expect(screen.queryByTestId("settings-read-only")).not.toBeInTheDocument();
    fireEvent.change(covered, { target: { value: "sample:\n  mode: \"on\"\n" } });
    expect(screen.getByRole("button", { name: "Preview" })).toBeEnabled();
    fireEvent.change(covered, { target: { value: "" } });

    await userEvent.selectOptions(await picker(), "repository:acme/gadgets");
    const other = await screen.findByRole("textbox", { name: "acme/gadgets's layer (YAML)" });
    await waitFor(() => expect(other).toHaveAttribute("readonly"));
    expect(screen.getByTestId("settings-read-only")).toHaveTextContent(
      "This API key's repository allowlist does not include acme/gadgets, so it cannot change its layer.",
    );
    expect(screen.getByRole("button", { name: "Preview" })).toBeDisabled();
  });

  it("lets an operator read a layer and change nothing", async () => {
    asOperator();
    renderAt("/settings?tab=policy&owner=acme");
    expect(await textarea()).toHaveAttribute("readonly");
    expect(screen.getByTestId("settings-read-only")).toHaveTextContent("Only an admin can change acme's layer.");
    expect(screen.getByRole("button", { name: "Preview" })).toBeDisabled();
    const [newest] = await screen.findAllByTestId("settings-revision");
    expect(within(newest).getByRole("button", { name: "Undo this change" })).toBeDisabled();
  });

  it("shows whoever the server hides as someone else: the layer's author, a value's setter and a change's actor", async () => {
    asOperator();
    api.fetchSettings.mockImplementation(async (layer) => ({ ...DOCS[keyOf(layer)], updated_by: null }));
    api.fetchEffectiveSettings.mockImplementation(async (layer) => {
      const view = effectiveFor(layer);
      return {
        ...view,
        sections: view.sections.map((section) => ({
          ...section,
          keys: section.keys.map((key) => ({ ...key, set_by: null })),
        })),
      };
    });
    api.fetchSettingsRevisions.mockResolvedValue([{ ...ACME_REVISIONS[0], actor: null }]);
    renderAt("/settings?tab=policy&owner=acme");

    expect(await screen.findByTestId("settings-document-meta")).toHaveTextContent(/^Generation 2, last changed by someone else on /);
    const [hours] = await screen.findAllByTestId("effective-key");
    expect(hours).toHaveTextContent("set by someone else on");
    const [newest] = await screen.findAllByTestId("settings-revision");
    expect(within(newest).getByTestId("settings-revision-meta")).toHaveTextContent(/^Someone else · Dashboard or API ·/);
    for (const where of [screen.getByTestId("settings-document-meta"), hours, newest]) {
      expect(where).not.toHaveTextContent(/null|undefined/);
    }
  });
});
