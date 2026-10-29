import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";
import { useAuthStore } from "@/stores/authStore";
import type {
  EffectiveSettings,
  SettingsDocument,
  SettingsPreview,
  SettingsRevision,
  SettingsRevisionDetail,
} from "@/lib/types/settings";

const api = vi.hoisted(() => ({
  fetchSettings: vi.fn(),
  previewSettings: vi.fn(),
  saveSettings: vi.fn(),
  fetchEffectiveSettings: vi.fn(),
  fetchSettingsRevisions: vi.fn(),
  fetchSettingsRevision: vi.fn(),
  undoSettingsChange: vi.fn(),
}));
vi.mock("@/api/settings", () => api);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

import PolicyTab from "../PolicyTab";

const WHEN = "2026-10-28T10:00:00Z";
const WHY_NOT = "This changed again later. Undo the newer change first.";
const STORED = "# Fewer at a time while we watch it.\nversion: 1\nsample:\n  per_tick: 5\n";
const EDITED = "# Fewer at a time while we watch it.\nversion: 1\nsample:\n  per_tick: 3\n";

const DOC: SettingsDocument = {
  yaml_text: STORED,
  data: { version: 1, sample: { per_tick: 5 } },
  schema_version: 1,
  generation: 3,
  updated_by: "alex@example.com",
  updated_at: WHEN,
  sections: ["sample"],
  unknown_sections: [],
  unknown_keys: [],
};

const EMPTY_DOC: SettingsDocument = {
  yaml_text: "",
  data: {},
  schema_version: 1,
  generation: 0,
  updated_by: "",
  updated_at: null,
  sections: [],
  unknown_sections: [],
  unknown_keys: [],
};

const EFFECTIVE: EffectiveSettings = {
  generation: 3,
  sections: [
    {
      name: "sample",
      summary: "A section for the tests.",
      keys: [
        {
          key: "per_tick",
          value: 5,
          default: 10,
          source: "document",
          set_by: "alex@example.com",
          set_at: WHEN,
          env: "SAMPLE_PER_TICK",
          env_still_set: true,
          overridable: ["organization"],
          refused: null,
          description: "How many each tick handles.",
        },
        {
          key: "enabled",
          value: false,
          default: false,
          source: "default",
          set_by: null,
          set_at: null,
          env: null,
          env_still_set: false,
          overridable: [],
          refused: "Input should be a valid boolean",
          description: "Turn the sample on.",
        },
      ],
    },
  ],
  unknown_sections: ["later"],
  unknown_keys: ["sample.extra"],
};

const REVISIONS: SettingsRevision[] = [
  {
    id: 9,
    what: "Changed: sample.per_tick",
    actor: "alex@example.com",
    source: "person",
    reason: "fewer at a time",
    created_at: WHEN,
    undoable: true,
    why_not: null,
  },
  {
    id: 8,
    what: "Imported from the environment: sample.per_tick",
    actor: "embry0",
    source: "bootstrap",
    reason: "",
    created_at: WHEN,
    undoable: false,
    why_not: WHY_NOT,
  },
];

const PREVIEW: SettingsPreview = {
  would_change: true,
  text_diff: "--- stored\n+++ proposed\n@@ -3,2 +3,2 @@\n sample:\n-  per_tick: 5\n+  per_tick: 3\n",
  changes: [
    {
      key: "sample.per_tick",
      before: 5,
      after: 3,
      set_before: true,
      set_after: true,
      effective_before: 5,
      effective_after: 3,
    },
  ],
  unknown_sections: [],
  unknown_keys: [],
};

function httpError(status: number, detail: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data: { detail } },
  });
}

function asAdmin() {
  useAuthStore.setState({ status: "authenticated", authMode: "session", user: { email: "a@x", name: "A", tier: "admin" } });
}
function asOperator() {
  useAuthStore.setState({ status: "authenticated", authMode: "session", user: { email: "o@x", name: "O", tier: "operator" } });
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PolicyTab />
    </QueryClientProvider>,
  );
}

const textarea = () => screen.findByTestId("settings-textarea");

async function editAndPreview(text = EDITED) {
  fireEvent.change(await textarea(), { target: { value: text } });
  await userEvent.click(screen.getByRole("button", { name: "Preview" }));
}

beforeEach(() => {
  for (const mock of Object.values(api)) mock.mockReset();
  api.fetchSettings.mockResolvedValue(DOC);
  api.fetchEffectiveSettings.mockResolvedValue(EFFECTIVE);
  api.fetchSettingsRevisions.mockResolvedValue(REVISIONS);
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.error).mockReset();
  asAdmin();
});
afterEach(() => vi.restoreAllMocks());

describe("PolicyTab", () => {
  it("says no section is registered yet, nothing is stored and nothing has changed", async () => {
    api.fetchSettings.mockResolvedValue(EMPTY_DOC);
    api.fetchEffectiveSettings.mockResolvedValue({ generation: 0, sections: [], unknown_sections: [], unknown_keys: [] });
    api.fetchSettingsRevisions.mockResolvedValue([]);
    renderTab();

    expect(await screen.findByTestId("settings-no-sections")).toHaveTextContent(
      "No feature keeps its policy in this document yet",
    );
    expect(screen.getByTestId("settings-document-meta")).toHaveTextContent(
      "Nothing is stored yet, so every key takes its default.",
    );
    expect(await screen.findByText("No settings sections yet")).toBeInTheDocument();
    expect(await screen.findByText("No changes recorded yet.")).toBeInTheDocument();
    expect(await textarea()).toHaveValue("");
  });

  it("previews a change, then saves exactly that text with If-Match and the reason", async () => {
    api.previewSettings.mockResolvedValue(PREVIEW);
    api.saveSettings.mockResolvedValue({
      generation: 4,
      revision_id: 10,
      changes: PREVIEW.changes,
      unknown_sections: [],
      unknown_keys: [],
    });
    renderTab();
    expect(await textarea()).toHaveValue(STORED);
    expect(screen.getByRole("button", { name: "Preview" }), "nothing to preview before an edit").toBeDisabled();

    fireEvent.change(await textarea(), { target: { value: EDITED } });
    expect(screen.queryByRole("button", { name: "Save" }), "no save before a preview").not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));

    // The dry run names the version the edit started from: a stale one is a 409 there too.
    expect(api.previewSettings).toHaveBeenCalledWith(EDITED, 3);
    const preview = await screen.findByTestId("settings-preview");
    const change = within(preview).getByTestId("settings-change");
    expect(change).toHaveTextContent("sample.per_tick");
    // The arrow is read out as a word.
    expect(change).toHaveTextContent(/^sample\.per_tick5→becomes3$/);
    const diff = within(preview).getByTestId("text-diff");
    expect(diff.querySelector('[data-kind="remove"]')).toHaveTextContent("per_tick: 5");
    expect(diff.querySelector('[data-kind="add"]')).toHaveTextContent("per_tick: 3");

    fireEvent.change(within(preview).getByLabelText("Reason (optional)"), { target: { value: "fewer at a time" } });
    api.fetchSettings.mockResolvedValue({ ...DOC, yaml_text: EDITED, generation: 4 });
    await userEvent.click(within(preview).getByRole("button", { name: "Save" }));

    expect(api.saveSettings).toHaveBeenCalledWith({ yamlText: EDITED, generation: 3, reason: "fewer at a time" });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Settings saved. Each loop reads them on its next tick."));
    expect(screen.queryByTestId("settings-preview")).not.toBeInTheDocument();
    // Read back: the stored text is the saved one and there is no edit left.
    await waitFor(() => expect(screen.getByTestId("settings-document-meta")).toHaveTextContent("Generation 4"));
    expect(screen.getByRole("button", { name: "Revert" })).toBeDisabled();
  });

  it("names in the preview the sections and keys this release keeps but does not read", async () => {
    api.previewSettings.mockResolvedValue({ ...PREVIEW, unknown_sections: [], unknown_keys: ["sample.extra"] });
    renderTab();
    await editAndPreview();
    expect(await screen.findByTestId("settings-preview-unknown")).toHaveTextContent(
      "Kept as stored, but not read by this release: key sample.extra.",
    );
  });

  it("shows git's no-newline marker in the diff as a note, not as a change", async () => {
    api.previewSettings.mockResolvedValue({
      ...PREVIEW,
      text_diff:
        "--- stored\n+++ proposed\n@@ -3,2 +3,2 @@\n sample:\n-  per_tick: 5\n\\ No newline at end of file\n+  per_tick: 3\n",
    });
    renderTab();
    await editAndPreview();
    const diff = within(await screen.findByTestId("settings-preview")).getByTestId("text-diff");
    const note = diff.querySelector('[data-kind="note"]');
    expect(note).toHaveTextContent("\\ No newline at end of file");
    expect(note?.previousElementSibling).toHaveAttribute("data-kind", "remove");
    expect(diff.querySelectorAll('[data-kind="add"], [data-kind="remove"]')).toHaveLength(2);
  });

  it("drops a preview once the text changes again", async () => {
    api.previewSettings.mockResolvedValue(PREVIEW);
    renderTab();
    await editAndPreview();
    await screen.findByTestId("settings-preview");

    fireEvent.change(await textarea(), { target: { value: `${EDITED}# and more\n` } });
    expect(screen.queryByTestId("settings-preview")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });

  it("lists every problem of a refused document by its key, next to the text, with no toast", async () => {
    api.previewSettings.mockRejectedValue(
      httpError(422, [
        { loc: ["sample", "per_tick"], type: "less_than_equal", msg: "Input should be less than or equal to 20" },
        { loc: ["smaple"], type: "unknown_section", msg: "'smaple' is not a settings section. Did you mean 'sample'?" },
        { loc: [], type: "yaml_syntax", msg: "Line 4, column 3: mapping values are not allowed here" },
      ]),
    );
    renderTab();
    await editAndPreview();

    const problems = await screen.findByTestId("settings-problems");
    expect(problems).toHaveAttribute("role", "alert");
    expect(within(problems).getAllByTestId("settings-problem").map((li) => li.textContent)).toEqual([
      "sample.per_tick: Input should be less than or equal to 20",
      "smaple: 'smaple' is not a settings section. Did you mean 'sample'?",
      "Line 4, column 3: mapping values are not allowed here",
    ]);
    expect(within(problems).getByText("sample.per_tick").tagName).toBe("CODE");
    expect(toast.error).not.toHaveBeenCalled();
    expect(screen.queryByTestId("settings-preview")).not.toBeInTheDocument();

    fireEvent.change(await textarea(), { target: { value: STORED.replace("5", "6") } });
    expect(screen.queryByTestId("settings-problems"), "an edit clears the problems of the old text").not.toBeInTheDocument();
  });

  it("on a 409 reads the new version in, keeps the text, says so, and saves against the new version next", async () => {
    const detail =
      "The settings changed since you read them (now generation 4). Read them again and apply your change to the new version.";
    api.previewSettings.mockResolvedValue(PREVIEW);
    api.saveSettings.mockRejectedValueOnce(httpError(409, detail));
    renderTab();
    await editAndPreview();
    await screen.findByTestId("settings-preview");

    const theirs = { ...DOC, yaml_text: `${STORED}# someone else\n`, generation: 4, updated_by: "sam@example.com" };
    api.fetchSettings.mockResolvedValue(theirs);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    const conflict = await screen.findByTestId("settings-conflict");
    expect(conflict).toHaveTextContent(detail);
    expect(conflict).toHaveTextContent("Your text is still in the editor");
    expect(api.fetchSettings).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("settings-document-meta")).toHaveTextContent("Generation 4, last changed by sam@example.com");
    expect(await textarea()).toHaveValue(EDITED);
    expect(screen.queryByTestId("settings-preview")).not.toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();

    api.saveSettings.mockResolvedValue({ generation: 5, revision_id: 11, changes: [], unknown_sections: [], unknown_keys: [] });
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(api.previewSettings).toHaveBeenLastCalledWith(EDITED, 4);
    expect(screen.queryByTestId("settings-conflict")).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));
    expect(api.saveSettings).toHaveBeenLastCalledWith({ yamlText: EDITED, generation: 4, reason: "" });
  });

  it("previews an edit against the version it started from, even when the page already shows a newer one", async () => {
    api.previewSettings.mockRejectedValueOnce(httpError(409, "The settings changed since you read them."));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <PolicyTab />
      </QueryClientProvider>,
    );
    fireEvent.change(await textarea(), { target: { value: EDITED } });
    // A background read picks up someone else's save while the edit is open.
    api.fetchSettings.mockResolvedValue({ ...DOC, yaml_text: `${STORED}# theirs\n`, generation: 4 });
    await client.refetchQueries();
    await waitFor(() => expect(screen.getByTestId("settings-document-meta")).toHaveTextContent("Generation 4"));

    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(api.previewSettings).toHaveBeenCalledWith(EDITED, 3);
    expect(await screen.findByTestId("settings-conflict")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });

  it("on a Preview refused with 409 reads the new version in, keeps the text, says so, and previews against it next", async () => {
    const detail =
      "The settings changed since you read them (now generation 4). Read them again and apply your change to the new version.";
    api.previewSettings.mockRejectedValueOnce(httpError(409, detail));
    renderTab();
    fireEvent.change(await textarea(), { target: { value: EDITED } });
    const theirs = { ...DOC, yaml_text: `${STORED}# someone else\n`, generation: 4, updated_by: "sam@example.com" };
    api.fetchSettings.mockResolvedValue(theirs);
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));

    const conflict = await screen.findByTestId("settings-conflict");
    expect(conflict).toHaveTextContent(detail);
    expect(conflict).toHaveTextContent("Your text is still in the editor");
    expect(api.fetchSettings).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("settings-document-meta")).toHaveTextContent("Generation 4, last changed by sam@example.com");
    expect(await textarea()).toHaveValue(EDITED);
    expect(screen.queryByTestId("settings-preview")).not.toBeInTheDocument();
    expect(screen.queryByTestId("settings-problems")).not.toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();

    api.previewSettings.mockResolvedValue(PREVIEW);
    api.saveSettings.mockResolvedValue({ generation: 5, revision_id: 11, changes: [], unknown_sections: [], unknown_keys: [] });
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(api.previewSettings).toHaveBeenLastCalledWith(EDITED, 4);
    expect(screen.queryByTestId("settings-conflict")).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));
    expect(api.saveSettings).toHaveBeenLastCalledWith({ yamlText: EDITED, generation: 4, reason: "" });
  });

  it("says why a document too large to read was refused", async () => {
    api.previewSettings.mockRejectedValue(httpError(413, "the settings document is larger than 262144 bytes"));
    renderTab();
    await editAndPreview();
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Could not preview the change: the settings document is larger than 262144 bytes",
      ),
    );
    expect(screen.queryByTestId("settings-problems")).not.toBeInTheDocument();
    expect(screen.queryByTestId("settings-preview")).not.toBeInTheDocument();
  });

  it("shows each value in effect with where it came from, its default and its warnings", async () => {
    renderTab();
    const [perTick, enabled] = await screen.findAllByTestId("effective-key");
    expect(within(perTick).getByTestId("effective-value")).toHaveTextContent("5");
    expect(perTick).toHaveTextContent("Set in the document");
    expect(perTick).toHaveTextContent("How many each tick handles.");
    expect(perTick).toHaveTextContent("Default 10 · set by alex@example.com on");
    expect(perTick).toHaveTextContent("replaces SAMPLE_PER_TICK · an organization can override it");
    expect(within(perTick).getByTestId("effective-env-still-set")).toHaveTextContent(
      "SAMPLE_PER_TICK is still set in the environment.",
    );
    expect(within(enabled).getByTestId("effective-value")).toHaveTextContent("false");
    expect(enabled).toHaveTextContent("Default");
    expect(within(enabled).getByTestId("effective-refused")).toHaveTextContent(
      "The stored value is refused, so the default is read: Input should be a valid boolean",
    );
    expect(within(enabled).queryByTestId("effective-env-still-set")).not.toBeInTheDocument();
    expect(screen.getByTestId("effective-unknown")).toHaveTextContent(
      "Stored but not read by this release: section later and key sample.extra.",
    );
  });

  it("names a value imported from the environment by its variable", async () => {
    const [first, second] = EFFECTIVE.sections[0].keys;
    api.fetchEffectiveSettings.mockResolvedValue({
      ...EFFECTIVE,
      sections: [{ ...EFFECTIVE.sections[0], keys: [{ ...first, source: "bootstrap", env_still_set: false }, second] }],
    });
    renderTab();
    const [perTick] = await screen.findAllByTestId("effective-key");
    expect(perTick).toHaveTextContent("Imported from SAMPLE_PER_TICK");
    expect(perTick).not.toHaveTextContent("replaces SAMPLE_PER_TICK");
  });

  it("offers Undo only on the newest change and says why on the others", async () => {
    renderTab();
    const [newest, older] = await screen.findAllByTestId("settings-revision");
    expect(within(newest).getByRole("button", { name: "Undo this change" })).toBeEnabled();
    expect(within(newest).getByTestId("settings-revision-meta")).toHaveTextContent(
      "alex@example.com · Dashboard or API ·",
    );
    expect(newest).toHaveTextContent("Reason: fewer at a time");
    expect(within(older).queryByRole("button", { name: "Undo this change" })).not.toBeInTheDocument();
    expect(within(older).getByText(WHY_NOT)).toBeInTheDocument();
    expect(within(older).getByTestId("settings-revision-meta")).toHaveTextContent("embry0 · Imported from the environment ·");
    expect(screen.getAllByRole("button", { name: "Undo this change" })).toHaveLength(1);
  });

  it("reads a change whose author is hidden (null, or left out) as someone else's, list and detail alike", async () => {
    const { actor: _hidden, ...withoutActor } = REVISIONS[1];
    api.fetchSettingsRevisions.mockResolvedValue([{ ...REVISIONS[0], actor: null }, withoutActor]);
    api.fetchSettingsRevision.mockResolvedValue({
      ...withoutActor,
      before_text: STORED,
      after_text: EDITED,
      changes: [{ key: "sample.per_tick", before: 5, after: 3, set_before: true, set_after: true }],
    });
    renderTab();
    const [nulled, absent] = await screen.findAllByTestId("settings-revision");
    expect(within(nulled).getByTestId("settings-revision-meta")).toHaveTextContent(/^Someone else · Dashboard or API ·/);
    expect(within(absent).getByTestId("settings-revision-meta")).toHaveTextContent(/^Someone else · Imported from the environment ·/);
    for (const row of [nulled, absent]) {
      expect(row).not.toHaveTextContent(/null|undefined/);
    }

    await userEvent.click(within(absent).getByRole("button", { name: "Show the change" }));
    const opened = await within(absent).findByTestId("settings-revision-detail");
    expect(within(opened).getByTestId("settings-change")).toHaveTextContent("sample.per_tick");
    expect(absent).not.toHaveTextContent(/null|undefined/);
  });

  it("asks before undoing, then undoes the newest change", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    api.undoSettingsChange.mockResolvedValue({ generation: 4, revision_id: 10, changes: [] });
    renderTab();
    const [newest] = await screen.findAllByTestId("settings-revision");
    await userEvent.click(within(newest).getByRole("button", { name: "Undo this change" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(api.undoSettingsChange).not.toHaveBeenCalled();

    await userEvent.click(within(newest).getByRole("button", { name: "Undo this change" }));
    await waitFor(() => expect(api.undoSettingsChange).toHaveBeenCalledWith(9));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Change undone."));
  });

  it("toasts the server's reason when an undo is refused", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    api.undoSettingsChange.mockRejectedValue(httpError(409, WHY_NOT));
    renderTab();
    const [newest] = await screen.findAllByTestId("settings-revision");
    await userEvent.click(within(newest).getByRole("button", { name: "Undo this change" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(`Could not undo the change: ${WHY_NOT}`));
    // The history is read again, so it shows what can be undone now.
    await waitFor(() => expect(api.fetchSettingsRevisions).toHaveBeenCalledTimes(2));
  });

  it("says why an old version cannot be put back, every reason in one toast", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    api.undoSettingsChange.mockRejectedValue(
      httpError(422, [
        { loc: [], type: "revision_invalid", msg: "That earlier version is no longer valid." },
        { loc: [], type: "too_large", msg: "the settings document is larger than 262144 bytes" },
      ]),
    );
    renderTab();
    const [newest] = await screen.findAllByTestId("settings-revision");
    await userEvent.click(within(newest).getByRole("button", { name: "Undo this change" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Could not undo the change: That earlier version is no longer valid.; the settings document is larger than 262144 bytes",
      ),
    );
  });

  it("opens a change to show the keys it changed and its text diff", async () => {
    const detail: SettingsRevisionDetail = {
      ...REVISIONS[0],
      before_text: STORED,
      after_text: EDITED,
      changes: [{ key: "sample.per_tick", before: 5, after: 3, set_before: true, set_after: true }],
    };
    api.fetchSettingsRevision.mockResolvedValue(detail);
    renderTab();
    const [newest] = await screen.findAllByTestId("settings-revision");
    expect(api.fetchSettingsRevision).not.toHaveBeenCalled();
    await userEvent.click(within(newest).getByRole("button", { name: "Show the change" }));

    const opened = await within(newest).findByTestId("settings-revision-detail");
    expect(api.fetchSettingsRevision).toHaveBeenCalledWith(9);
    expect(within(opened).getByTestId("settings-change")).toHaveTextContent(/^sample\.per_tick5→becomes3$/);
    const diff = within(opened).getByTestId("text-diff");
    expect(diff.querySelector('[data-kind="remove"]')).toHaveTextContent("per_tick: 5");
    expect(diff.querySelector('[data-kind="add"]')).toHaveTextContent("per_tick: 3");

    await userEvent.click(within(newest).getByRole("button", { name: "Hide the change" }));
    expect(within(newest).queryByTestId("settings-revision-detail")).not.toBeInTheDocument();
  });

  it("lets an operator read everything but change nothing", async () => {
    asOperator();
    renderTab();
    expect(await textarea()).toHaveAttribute("readonly");
    expect(screen.getByText("Only an admin can change the settings document.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview" })).toBeDisabled();
    const [newest] = await screen.findAllByTestId("settings-revision");
    expect(within(newest).getByRole("button", { name: "Undo this change" })).toBeDisabled();
    expect(await screen.findAllByTestId("effective-key")).toHaveLength(2);
  });
});
