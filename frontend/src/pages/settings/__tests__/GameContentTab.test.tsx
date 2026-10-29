import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PackDraft, PackPreview, PackState, PackVersion } from "@/api/progressionPack";
import { useAuthStore } from "@/stores/authStore";

const api = {
  fetchPack: vi.fn(),
  fetchPackDraft: vi.fn(),
  fetchDefaultDiff: vi.fn(),
  fetchPackVersions: vi.fn(),
  fetchPackVersion: vi.fn(),
  checkPack: vi.fn(),
  savePackDraft: vi.fn(),
  deletePackDraft: vi.fn(),
  previewPackDraft: vi.fn(),
  activatePackDraft: vi.fn(),
  restorePackVersion: vi.fn(),
  resetPack: vi.fn(),
};

vi.mock("@/api/progressionPack", () => ({
  fetchPack: () => api.fetchPack(),
  fetchPackDraft: () => api.fetchPackDraft(),
  fetchDefaultDiff: () => api.fetchDefaultDiff(),
  fetchPackVersions: () => api.fetchPackVersions(),
  fetchPackVersion: (v: number) => api.fetchPackVersion(v),
  checkPack: (text: string) => api.checkPack(text),
  savePackDraft: (request: unknown) => api.savePackDraft(request),
  deletePackDraft: (v: number) => api.deletePackDraft(v),
  previewPackDraft: () => api.previewPackDraft(),
  activatePackDraft: (v: number, reason?: string) => api.activatePackDraft(v, reason),
  restorePackVersion: (v: number, active: number) => api.restorePackVersion(v, active),
  resetPack: (active: number) => api.resetPack(active),
}));
const mockUseProgression = vi.fn();
vi.mock("@/hooks/useProgression", () => ({ useProgression: () => mockUseProgression() }));
vi.mock("@/hooks/useAgentIdentity", () => ({
  useIdentityLookup: () => (type: string) => ({ displayName: type === "developer" ? "Developer" : type }),
}));

import GameContentTab from "../GameContentTab";

const ACTIVE_TEXT = "version: 1\ntimezone: UTC\ntiers:\n  - {id: bronze, name: Bronze}\n";
const DRAFT_TEXT = "version: 1\ntimezone: UTC\ntiers:\n  - {id: bronze, name: Copper}\n";
const XSS = '<img src=x onerror="alert(1)">';

function pack(overrides: Partial<PackState> = {}): PackState {
  return {
    version: 5,
    follows_default: false,
    schema_version: 1,
    hash: "h5",
    text: ACTIVE_TEXT,
    based_on: "d1",
    default_hash: "d1",
    default_moved: false,
    source: "person",
    reason: "",
    created_by: "ann@example.com",
    activated_at: "2026-10-01T10:00:00+00:00",
    fallback: null,
    draft_version: null,
    ...overrides,
  };
}

function version(overrides: Partial<PackVersion> = {}): PackVersion {
  return {
    version: 5,
    status: "active",
    follows_default: false,
    schema_version: 1,
    based_on: "d1",
    source: "person",
    reason: "",
    created_by: "ann@example.com",
    created_at: "2026-10-01T10:00:00+00:00",
    activated_at: "2026-10-01T10:00:00+00:00",
    ...overrides,
  };
}

function draft(overrides: Partial<PackDraft> = {}): PackDraft {
  return {
    ...version({ version: 7, status: "draft", activated_at: null }),
    text: DRAFT_TEXT,
    hash: "h7",
    sections: ["tiers"],
    problems: null,
    ...overrides,
  };
}

function refused(status: number, detail: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status, data: { detail } } });
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <GameContentTab />
    </QueryClientProvider>,
  );
}

async function textarea(): Promise<HTMLTextAreaElement> {
  const box = (await screen.findByTestId("pack-textarea")) as HTMLTextAreaElement;
  await waitFor(() => expect(box.value).not.toBe(""));
  return box;
}

function asAdmin() {
  useAuthStore.setState({ authMode: "key", keyTier: null, user: null });
}

function asOperator() {
  useAuthStore.setState({ authMode: "session", keyTier: undefined, user: { email: "ops@example.test", name: "Ops", tier: "operator" } });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUseProgression.mockReturnValue({ data: undefined });
  asAdmin();
  api.fetchPack.mockResolvedValue(pack());
  api.fetchPackDraft.mockResolvedValue(null);
  api.fetchPackVersions.mockResolvedValue([version()]);
  api.fetchDefaultDiff.mockResolvedValue({ based_on: "d1", default_hash: "d2", moved: true, text_diff: null });
});

describe("GameContentTab", () => {
  it("saves an admin's edit as the draft, naming the draft it replaces", async () => {
    api.savePackDraft.mockResolvedValue({
      version: 7,
      replaced_version: null,
      hash: "h7",
      based_on: "d1",
      sections: ["tiers"],
      warnings: ["shell:plain trains no skill: its calls earn nothing"],
    });
    renderTab();
    const box = await textarea();
    expect(box.value, "with no draft the editor shows the pack in effect").toBe(ACTIVE_TEXT);
    expect(screen.getByTestId("pack-meta")).toHaveTextContent("Version 5 is a customised pack, activated by ann@example.com");
    expect(screen.getByTestId("pack-preview-button"), "nothing to preview without a saved draft").toBeDisabled();
    fireEvent.change(box, { target: { value: DRAFT_TEXT } });
    await userEvent.type(screen.getByLabelText("Reason (optional)"), "copper");
    await userEvent.click(screen.getByTestId("pack-save-draft"));
    await waitFor(() =>
      expect(api.savePackDraft).toHaveBeenCalledWith({ text: DRAFT_TEXT, draftVersion: 0, rebase: false, reason: "copper" }),
    );
    expect(await screen.findByTestId("pack-warnings")).toHaveTextContent("shell:plain trains no skill");
  });

  it("asks for the draft only when the pack names one, so no request answers 404", async () => {
    renderTab();
    await textarea();
    expect(api.fetchPackDraft, "the pack names no draft, yet the tab asked for one").not.toHaveBeenCalled();
  });

  it("reads the draft the pack names", async () => {
    api.fetchPack.mockResolvedValue(pack({ draft_version: 7 }));
    api.fetchPackDraft.mockResolvedValue(draft());
    renderTab();
    await waitFor(() => expect(api.fetchPackDraft).toHaveBeenCalledTimes(1));
    expect(await textarea()).toHaveValue(draft().text);
  });

  it("checks a text: the sections it changes, its warnings and the text diff", async () => {
    api.checkPack.mockResolvedValue({
      would_change: true,
      version: 5,
      hash: "h7",
      sections: ["tiers"],
      text_diff: "--- active\n+++ proposed\n@@ -4 +4 @@\n-  - {id: bronze, name: Bronze}\n+  - {id: bronze, name: Copper}\n",
      warnings: [],
    });
    renderTab();
    fireEvent.change(await textarea(), { target: { value: DRAFT_TEXT } });
    await userEvent.click(screen.getByTestId("pack-check-button"));
    expect(api.checkPack).toHaveBeenCalledWith(DRAFT_TEXT);
    const result = await screen.findByTestId("pack-check");
    expect(result).toHaveTextContent("it changes: tiers");
    const lines = within(result).getByTestId("text-diff").querySelectorAll("[data-kind]");
    expect([...lines].map((l) => l.getAttribute("data-kind"))).toEqual(["gap", "remove", "add"]);
  });

  it("shows git's no-newline marker in the diff as a note, as the Policy tab does", async () => {
    api.checkPack.mockResolvedValue({
      would_change: true,
      version: 5,
      hash: "h7",
      sections: ["labels"],
      text_diff:
        "--- active\n+++ proposed\n@@ -3,2 +3,2 @@\n labels:\n-  complete: Complete\n\\ No newline at end of file\n+  complete: Done\n",
      warnings: [],
    });
    renderTab();
    fireEvent.change(await textarea(), { target: { value: DRAFT_TEXT } });
    await userEvent.click(screen.getByTestId("pack-check-button"));
    const diff = within(await screen.findByTestId("pack-check")).getByTestId("text-diff");
    const note = diff.querySelector('[data-kind="note"]');
    expect(note, "the marker is a note on the line before it, not a line of the text").toHaveTextContent(
      "\\ No newline at end of file",
    );
    expect(note?.previousElementSibling).toHaveAttribute("data-kind", "remove");
    expect(diff.querySelectorAll('[data-kind="add"], [data-kind="remove"]')).toHaveLength(2);
  });

  it("lists every problem of a refused pack by its key", async () => {
    api.checkPack.mockRejectedValue(
      refused(422, [
        { loc: ["tiers", 0, "from_level"], type: "first_tier", msg: "the first tier starts at level 1" },
        { loc: [], type: "yaml_syntax", msg: "line 3, column 1: could not find expected ':'" },
      ]),
    );
    renderTab();
    fireEvent.change(await textarea(), { target: { value: "tiers: [" } });
    await userEvent.click(screen.getByTestId("pack-check-button"));
    const problems = await screen.findAllByTestId("pack-problem");
    expect(problems.map((p) => p.textContent)).toEqual([
      "tiers[0].from_level: the first tier starts at level 1",
      "line 3, column 1: could not find expected ':'",
    ]);
  });

  it("previews the history re-priced under the draft, then activates exactly that draft", async () => {
    api.fetchPack.mockResolvedValue(pack({ draft_version: 7 }));
    api.fetchPackDraft.mockResolvedValue(draft());
    api.previewPackDraft.mockResolvedValue({
      draft: { version: 7, hash: "h7" },
      active: { hash: "h5" },
      changes: {
        characters: [
          {
            agent_type: "developer",
            before: { level: 12, total_level: 300, xp: 1000 },
            after: { level: 14, total_level: 320, xp: 9000 },
          },
          { agent_type: "review", before: { level: 3, total_level: 40, xp: 10 }, after: { level: 3, total_level: 40, xp: 10 } },
        ],
        account: { before: { total_level: 340, xp: 1010 }, after: { total_level: 360, xp: 9010 } },
        achievements_unlocked: { before: 4, after: 6 },
      },
    } as unknown as PackPreview);
    api.activatePackDraft.mockResolvedValue({
      changed: true,
      version: 7,
      previous_version: 5,
      source: "person",
      hash: "h7",
      follows_default: false,
      warnings: [],
    });
    renderTab();
    expect((await textarea()).value, "a saved draft is what the editor shows").toBe(DRAFT_TEXT);
    expect(screen.getByTestId("pack-draft-meta")).toHaveTextContent("A draft is saved (version 7");
    await userEvent.click(screen.getByTestId("pack-preview-button"));
    const preview = await screen.findByTestId("pack-preview");
    expect(preview).toHaveTextContent("1 of 2 characters would change level or total level");
    expect(preview).toHaveTextContent("Achievements unlocked: 4 → 6");
    const rows = within(preview).getAllByTestId("pack-preview-row");
    expect(rows[0]).toHaveTextContent(/Developer.*12.*14.*\+2.*300.*320.*\+20/);

    await userEvent.click(screen.getByTestId("pack-activate"));
    const dialog = await screen.findByRole("dialog", { name: "Activate the draft?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Activate" }));
    await waitFor(() => expect(api.activatePackDraft).toHaveBeenCalledWith(7, ""));
  });

  it("re-previews when the pack in effect changed since the draft was saved, instead of activating over it", async () => {
    api.fetchPack.mockResolvedValue(pack({ draft_version: 7 }));
    api.fetchPackDraft.mockResolvedValue(draft());
    api.previewPackDraft.mockResolvedValue({
      draft: { version: 7, hash: "h7", parent_version: 5, stale: false },
      active: { hash: "h5", version: 5 },
      changes: {
        characters: [],
        account: { before: { total_level: 0, xp: 0 }, after: { total_level: 0, xp: 0 } },
        achievements_unlocked: { before: 0, after: 0 },
      },
    } as unknown as PackPreview);
    api.activatePackDraft.mockRejectedValue(
      refused(
        409,
        "The pack in effect changed since this draft was saved: it was saved over version 5, and version 6 is in effect now.",
      ),
    );
    renderTab();
    await textarea();
    await userEvent.click(screen.getByTestId("pack-preview-button"));
    await screen.findByTestId("pack-preview");
    expect(api.previewPackDraft).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByTestId("pack-activate"));
    const dialog = await screen.findByRole("dialog", { name: "Activate the draft?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Activate" }));
    expect(await screen.findByTestId("pack-conflict")).toHaveTextContent("version 6 is in effect now");
    await waitFor(() =>
      expect(api.previewPackDraft, "a refused activation shows the preview again, against the pack in effect").toHaveBeenCalledTimes(2),
    );
  });

  it("will not activate a draft saved over an earlier pack until it is saved again", async () => {
    api.fetchPack.mockResolvedValue(pack({ version: 6, draft_version: 7 }));
    api.fetchPackDraft.mockResolvedValue(draft({ parent_version: 5, stale: true }));
    api.savePackDraft.mockResolvedValue({
      version: 8,
      replaced_version: 7,
      hash: "h7",
      based_on: "d1",
      sections: ["tiers"],
      warnings: [],
    });
    renderTab();
    await textarea();
    expect(await screen.findByTestId("pack-draft-stale")).toHaveTextContent(
      "saved over version 5: version 6 is in effect now",
    );
    expect(screen.getByTestId("pack-activate"), "activating would undo version 6 unseen").toBeDisabled();
    const save = screen.getByTestId("pack-save-draft");
    expect(save, "saving it again, unchanged, is how it is based on the pack in effect").toBeEnabled();
    await userEvent.click(save);
    await waitFor(() => expect(api.savePackDraft).toHaveBeenCalledWith(expect.objectContaining({ draftVersion: 7 })));
  });

  it("names nobody when the server hides who made a version", async () => {
    asOperator();
    api.fetchPack.mockResolvedValue(pack({ created_by: null }));
    api.fetchPackVersions.mockResolvedValue([version({ version: 5, created_by: null })]);
    renderTab();
    const rows = await screen.findAllByTestId("pack-version");
    expect(within(rows[0]).getByTestId("pack-version-meta")).toHaveTextContent("Someone else");
    expect(await screen.findByTestId("pack-meta")).not.toHaveTextContent("activated by");
  });

  it("says when someone else changed the draft, and keeps the text", async () => {
    api.fetchPack.mockResolvedValue(pack({ draft_version: 7 }));
    api.fetchPackDraft.mockResolvedValue(draft());
    api.savePackDraft.mockRejectedValue(
      refused(409, "The draft changed since you read it (now version 9). Read it again and apply your change to the new version."),
    );
    renderTab();
    const box = await textarea();
    fireEvent.change(box, { target: { value: DRAFT_TEXT + "# mine\n" } });
    await userEvent.click(screen.getByTestId("pack-save-draft"));
    await waitFor(() => expect(api.savePackDraft).toHaveBeenCalledWith(expect.objectContaining({ draftVersion: 7 })));
    expect(await screen.findByTestId("pack-conflict")).toHaveTextContent("now version 9");
    expect(box.value).toBe(DRAFT_TEXT + "# mine\n");
  });

  it("resets to the shipped default against the version in effect", async () => {
    api.resetPack.mockResolvedValue({
      changed: true,
      version: 6,
      previous_version: 5,
      source: "reset",
      hash: "d1",
      follows_default: true,
      warnings: [],
    });
    renderTab();
    await textarea();
    await userEvent.click(screen.getByTestId("pack-reset"));
    const dialog = await screen.findByRole("dialog", { name: "Reset to the shipped default?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(api.resetPack).toHaveBeenCalledWith(5));
  });

  it("undoes the last change from the history against the version in effect", async () => {
    api.fetchPack.mockResolvedValue(pack({ version: 9 }));
    api.fetchPackVersions.mockResolvedValue([
      version({ version: 9, source: "file" }),
      version({ version: 8, status: "superseded", reason: "rename" }),
      version({ version: 3, status: "superseded", source: "reset", follows_default: true }),
    ]);
    api.restorePackVersion.mockResolvedValue({
      changed: true,
      version: 10,
      previous_version: 9,
      source: "restore",
      hash: "h8",
      follows_default: false,
      warnings: [],
    });
    renderTab();
    const rows = await screen.findAllByTestId("pack-version");
    expect(rows.map((r) => within(r).queryByTestId("pack-restore")?.textContent ?? null)).toEqual([
      null,
      "Undo the last change",
      "Restore",
    ]);
    expect(within(rows[0]).getByTestId("pack-version-meta")).toHaveTextContent("Pack file");
    await userEvent.click(within(rows[1]).getByTestId("pack-restore"));
    const dialog = await screen.findByRole("dialog", { name: "Restore version 8?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(api.restorePackVersion).toHaveBeenCalledWith(8, 9));
  });

  it("shows how the shipped default moved on, and lets the next draft say it took it in", async () => {
    api.fetchPack.mockResolvedValue(pack({ default_moved: true, default_hash: "d2" }));
    api.fetchDefaultDiff.mockResolvedValue({
      based_on: "d1",
      default_hash: "d2",
      moved: true,
      text_diff: "--- based on\n+++ shipped now\n@@ -1 +1 @@\n-  - {id: gold, name: Gold}\n+  - {id: gold, name: Golden}\n",
    });
    api.savePackDraft.mockResolvedValue({ version: 7, replaced_version: null, hash: "h7", based_on: "d2", sections: [], warnings: [] });
    renderTab();
    const notice = await screen.findByTestId("default-moved");
    expect(api.fetchDefaultDiff, "the diff is read only when asked for").not.toHaveBeenCalled();
    await userEvent.click(within(notice).getByTestId("default-moved-toggle"));
    const diff = await within(notice).findByTestId("text-diff");
    expect(diff).toHaveTextContent("name: Golden");
    await userEvent.click(screen.getByTestId("pack-rebase"));
    await userEvent.click(screen.getByTestId("pack-save-draft"));
    await waitFor(() => expect(api.savePackDraft).toHaveBeenCalledWith(expect.objectContaining({ rebase: true })));
  });

  it("says when the stored pack is refused and the shipped default is in effect", async () => {
    api.fetchPack.mockResolvedValue(
      pack({ fallback: [{ loc: ["retired_section"], type: "extra_forbidden", msg: "Extra inputs are not permitted" }] }),
    );
    renderTab();
    const fallback = await screen.findByTestId("pack-fallback");
    expect(fallback).toHaveTextContent("the shipped default is in effect");
    expect(fallback).toHaveTextContent("retired_section: Extra inputs are not permitted");
  });

  it("says when the achievements were counted on UTC because the database does not know the pack's timezone", async () => {
    mockUseProgression.mockReturnValue({
      data: { pack: { hash: "h5", version: 1, timezone: "Europe/Berlin", timezone_applied: "UTC" } },
    });
    renderTab();
    const refused = await screen.findByTestId("pack-timezone-refused");
    expect(refused, "the fallback calendar is reported where the pack is edited").toHaveTextContent(
      "does not know the pack's timezone, Europe/Berlin",
    );
    expect(refused).toHaveTextContent("counted on UTC for now");
  });

  it("says nothing about the calendar when the pack's own timezone applied", async () => {
    mockUseProgression.mockReturnValue({
      data: { pack: { hash: "h5", version: 1, timezone: "Europe/Berlin", timezone_applied: "Europe/Berlin" } },
    });
    renderTab();
    await textarea();
    expect(screen.queryByTestId("pack-timezone-refused")).toBeNull();
  });

  it("is read-only for anyone but an admin", async () => {
    useAuthStore.setState({ authMode: "session", keyTier: undefined, user: { email: "ops@example.test", name: "Ops", tier: "operator" } });
    api.fetchPack.mockResolvedValue(pack({ draft_version: 7 }));
    api.fetchPackDraft.mockResolvedValue(draft());
    api.fetchPackVersions.mockResolvedValue([version(), version({ version: 4, status: "superseded" })]);
    renderTab();
    const box = await textarea();
    expect(box).toHaveAttribute("readonly");
    expect(screen.getByText("Only an admin can change the content pack.")).toBeInTheDocument();
    for (const id of ["pack-check-button", "pack-save-draft", "pack-preview-button", "pack-activate", "pack-discard", "pack-reset"]) {
      expect(screen.getByTestId(id), id).toBeDisabled();
    }
    expect(await screen.findByTestId("pack-restore")).toBeDisabled();
  });

  it("prints an admin's words as text, never as markup", async () => {
    api.fetchPack.mockResolvedValue(pack({ fallback: [{ loc: ["labels", "gear"], type: "placeholder", msg: XSS }] }));
    api.fetchPackVersions.mockResolvedValue([version({ reason: XSS }), version({ version: 4, status: "superseded", created_by: XSS })]);
    api.checkPack.mockRejectedValue(refused(422, [{ loc: ["messages", "level"], type: "placeholder", msg: XSS }]));
    const { container } = renderTab();
    fireEvent.change(await textarea(), { target: { value: XSS } });
    await userEvent.click(screen.getByTestId("pack-check-button"));
    await screen.findAllByTestId("pack-problem");
    await screen.findAllByTestId("pack-version");
    expect(container.querySelector("img"), "no element is made from a pack's words").toBeNull();
    expect(screen.getByTestId("pack-fallback")).toHaveTextContent(XSS);
    expect(screen.getByTestId("pack-problems")).toHaveTextContent(`messages.level: ${XSS}`);
    expect(screen.getByText(`Reason: ${XSS}`)).toBeInTheDocument();
  });
});
