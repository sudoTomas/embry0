import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { toast } from "sonner";
import type { AreasProposal, AreasSaveResult, RepoAreas } from "@/api/areas";

const state: { data: RepoAreas | undefined; proposal: AreasProposal | null; error: Error | null } = {
  data: undefined,
  proposal: null,
  error: null,
};
const refetch = vi.fn();
const mapMutate = vi.fn();
const previewMutate = vi.fn();
const saveMutate = vi.fn();
const decideMutate = vi.fn();
const reread = vi.fn();
vi.mock("@/hooks/useRepoAreas", () => ({
  useRereadRepoAreas: () => reread,
  useRepoAreas: () => ({
    data: state.data,
    isLoading: false,
    isError: state.error !== null,
    error: state.error,
    isFetching: false,
    refetch,
  }),
  useAreasProposal: () => ({ data: state.proposal }),
  useMapAreas: () => ({ mutate: mapMutate, isPending: false }),
  usePreviewRepoAreas: () => ({ mutate: previewMutate, isPending: false }),
  useSaveRepoAreas: () => ({ mutate: saveMutate, isPending: false }),
  useDecideAreasProposal: () => ({ mutate: decideMutate, isPending: false }),
}));
vi.mock("@/hooks/useRunWith", () => ({
  useRunWithKeys: () => ({ data: [{ id: "k-1", label: "team key", provider: "anthropic", scope: "repo", status: "ok" }] }),
}));
vi.mock("@/hooks/useModels", () => ({ useModels: () => ({ models: ["claude-sonnet-5"], providers: [], isLoading: false }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

import { useAuthStore } from "@/stores/authStore";
import { RepoAreasSection } from "../RepoAreasSection";

const TEXT = "# ours\nareas:\n  - name: Billing\n    paths: [services/billing/]\n";
const AREAS: RepoAreas = {
  repo: "acme/web",
  yaml_text: TEXT,
  areas: [{ name: "Billing", slug: "billing", paths: ["services/billing/**"], logs: ["billing-api"], owners: [] }],
  generation: 3,
  updated_by: "ada@example.com",
  updated_at: "2026-11-10T12:00:00Z",
  imported: null,
  latest_job: { job_id: "job-1", status: "completed", created_at: null, finished_at: null, pr_url: null, live: false },
  proposal: null,
};
const PROPOSAL: AreasProposal = {
  id: "areas-1",
  repo: "acme/web",
  status: "pending",
  job_id: "job-1",
  job_status: "completed",
  base_generation: 3,
  stale: false,
  proposed_text: "areas:\n  - name: Search\n    paths: [search/]\n    owners: [octo]\n",
  changes: [
    { area: "Search", change: "added", keys: ["name", "paths", "logs", "owners"], owners_added: ["octo"], owners_removed: [] },
    { area: "Billing", change: "removed", keys: [], owners_added: [], owners_removed: [] },
  ],
  text_diff: "--- stored\n+++ proposed\n@@ -1 +1 @@\n-a\n+b\n",
  notes_md: "## Changes\n\nAdded Search.",
  failure: "",
  created_by: "ada@example.com",
  created_at: "2026-11-11T09:00:00Z",
  decided_by: null,
  decided_at: null,
};

function renderSection() {
  return render(
    <MemoryRouter>
      <RepoAreasSection repo="acme/web" />
    </MemoryRouter>,
  );
}

function refused(status: number, detail: unknown) {
  return Object.assign(new Error("refused"), { response: { status, data: { detail } } });
}

beforeEach(() => {
  for (const fn of [mapMutate, previewMutate, saveMutate, decideMutate, reread, refetch]) fn.mockReset();
  vi.mocked(toast.error).mockReset();
  state.data = AREAS;
  state.proposal = null;
  state.error = null;
  useAuthStore.setState({ status: "authenticated", authMode: "key", user: null, keyTier: "operator" });
});

describe("RepoAreasSection (areas kept in embry0, spec 2026-11-10)", () => {
  it("lists the areas embry0 keeps and what the last map did", () => {
    renderSection();
    expect(screen.getByRole("cell", { name: "Billing" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "services/billing/**" })).toBeInTheDocument();
    expect(screen.getByTestId("repo-areas-latest")).toHaveTextContent("Last mapped: completed · job-1");
    expect(screen.getByText(/nothing changes until you apply them, and nothing is written to the repository/)).toBeInTheDocument();
    expect(screen.queryByText(/pull request with/)).toBeNull();
  });

  it("maps with the model and key chosen, and not while a map runs", async () => {
    renderSection();
    await userEvent.click(screen.getByRole("button", { name: /Run with/ }));
    await userEvent.selectOptions(screen.getByLabelText("Key"), "k-1");
    await userEvent.click(screen.getByRole("button", { name: "Map areas again" }));
    expect(mapMutate.mock.calls[0][0]).toEqual({ credential_id: "k-1" });

    state.data = { ...AREAS, proposal: { id: "areas-2", status: "running", job_id: "job-2", at: null } };
    renderSection();
    expect(screen.getAllByRole("button", { name: /Map areas/ }).at(-1)).toBeDisabled();
  });

  it("says a repository with no areas names none, and says where imported ones came from", () => {
    state.data = { ...AREAS, areas: [], yaml_text: "", generation: 0, latest_job: null };
    renderSection();
    expect(screen.getByText(/names no areas yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Write areas" })).toBeInTheDocument();
    state.data = { ...AREAS, imported: { outcome: "imported", at: "2026-11-11T08:00:00Z", blob_sha: "b1" } };
    renderSection();
    expect(screen.getByTestId("repo-areas-imported")).toHaveTextContent("embry0 no longer reads that file");
  });

  it("previews a change against the version it started from, then saves it", async () => {
    previewMutate.mockImplementation((_vars, opts) =>
      opts.onSuccess({
        repo: "acme/web",
        generation: 3,
        revision_id: null,
        would_change: true,
        text_diff: "",
        changes: [{ area: "Billing", change: "changed", keys: ["owners"], owners_added: ["hubot"], owners_removed: [] }],
      } satisfies AreasSaveResult),
    );
    renderSection();
    await userEvent.click(screen.getByRole("button", { name: "Edit areas" }));
    const editor = screen.getByLabelText("Areas document");
    expect(editor).toHaveValue(TEXT);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(editor, { target: { value: `${TEXT}    owners: [hubot]\n` } });
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(previewMutate.mock.calls[0][0]).toEqual({ yamlText: `${TEXT}    owners: [hubot]\n`, generation: 3 });
    expect(screen.getByTestId("area-owners-added")).toHaveTextContent("makes hubot an owner");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(saveMutate.mock.calls[0][0]).toEqual({ yamlText: `${TEXT}    owners: [hubot]\n`, generation: 3 });
  });

  it("lists every problem of a refused document by its key, and says when someone changed them meanwhile", async () => {
    previewMutate.mockImplementationOnce((_vars, opts) =>
      opts.onError(
        refused(422, [
          { loc: ["areas", 0, "paths", 0], type: "invalid_glob", msg: "A glob starts at the repository's root." },
          { loc: ["areas", 1, "name"], type: "missing", msg: "An area needs a name." },
        ]),
      ),
    );
    renderSection();
    await userEvent.click(screen.getByRole("button", { name: "Edit areas" }));
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    const problems = screen.getAllByTestId("areas-problem");
    expect(problems.map((p) => p.textContent)).toEqual([
      "areas.0.paths.0: A glob starts at the repository's root.",
      "areas.1.name: An area needs a name.",
    ]);
    previewMutate.mockImplementationOnce((_vars, opts) => opts.onError(refused(409, "changed")));
    reread.mockResolvedValue({ ...AREAS, generation: 9 });
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(screen.getByTestId("areas-conflict")).toHaveTextContent("Someone changed these areas after you started");
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(previewMutate.mock.calls.at(-1)?.[0]).toEqual({ yamlText: TEXT, generation: 9 });
  });

  it("shows a waiting map's changes with the owners it adds first, and applies or rejects it", async () => {
    state.proposal = PROPOSAL;
    renderSection();
    const card = within(screen.getByTestId("areas-proposal"));
    expect(card.getByText(/Nothing changes until you apply them/)).toBeInTheDocument();
    const changes = card.getAllByTestId("area-change").map((c) => c.textContent);
    expect(changes).toEqual(["Search added · makes octo an owner", "Billing removed"]);
    await userEvent.click(card.getByRole("button", { name: "Apply" }));
    expect(decideMutate.mock.calls[0][0]).toEqual({ id: "areas-1", decision: "apply" });
    await userEvent.click(card.getByRole("button", { name: "Reject" }));
    expect(decideMutate.mock.calls[1][0]).toEqual({ id: "areas-1", decision: "reject" });
  });

  it("cannot apply a stale map, and opens a map's document in the editor to change it first", async () => {
    state.proposal = { ...PROPOSAL, stale: true };
    renderSection();
    const card = within(screen.getByTestId("areas-proposal"));
    expect(card.getByTestId("areas-proposal-stale")).toHaveTextContent("Map again for a fresh one");
    expect(card.getByRole("button", { name: "Apply" })).toBeDisabled();
    await userEvent.click(card.getByRole("button", { name: "Change it first" }));
    expect(screen.getByLabelText("Areas document")).toHaveValue(PROPOSAL.proposed_text);
  });

  it("names every problem of a map that is no longer valid, each by its key, as a refused save does", async () => {
    state.proposal = PROPOSAL;
    decideMutate.mockImplementation((_vars, options) =>
      options.onError(
        refused(422, [
          { loc: ["areas", 0, "paths", 0], type: "invalid_glob", msg: "A glob starts at the repository's root." },
          { loc: ["areas", 1, "name"], type: "duplicate_slug", msg: "Each area needs a name of its own." },
        ]),
      ),
    );
    renderSection();
    await userEvent.click(within(screen.getByTestId("areas-proposal")).getByRole("button", { name: "Apply" }));
    expect(vi.mocked(toast.error)).toHaveBeenCalledWith(
      "Could not apply the map: areas.0.paths.0: A glob starts at the repository's root.; " +
        "areas.1.name: Each area needs a name of its own.",
    );
  });

  it("says the areas could not be read, and reads them again on Try again", async () => {
    state.data = undefined;
    state.error = refused(500, "the database is down");
    renderSection();
    const failed = screen.queryByTestId("repo-areas-error");
    expect(failed, "a failed read shows a skeleton forever, with no message").not.toBeNull();
    expect(failed).toHaveTextContent("The areas could not be read");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("offers a read key no edit, map, apply or reject, and says what they take", () => {
    useAuthStore.setState({ authMode: "key", keyTier: "read" });
    state.proposal = PROPOSAL;
    renderSection();
    for (const name of ["Edit areas", "Map areas again", "Apply", "Reject", "Change it first"]) {
      expect(screen.queryByRole("button", { name }), `a read key is offered ${name}, which can only fail`).toBeNull();
    }
    expect(screen.getByTestId("repo-areas-read-only")).toHaveTextContent("take an operate key");
    expect(screen.getByTestId("areas-proposal")).toBeInTheDocument();
  });

  it("offers an operator's SSO session every action, and a key whose tier is not reported too", () => {
    for (const session of [
      { authMode: "session" as const, user: { email: "o@x", name: "O", tier: "operator" }, keyTier: undefined },
      { authMode: "key" as const, user: null, keyTier: undefined },
    ]) {
      useAuthStore.setState(session);
      const { unmount } = renderSection();
      expect(screen.getByRole("button", { name: "Edit areas" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Map areas again" })).toBeInTheDocument();
      unmount();
    }
  });

  it("says why the last map did not finish", () => {
    state.proposal = { ...PROPOSAL, status: "failed", failure: "The map's document cannot be stored: areas.0.name: An area needs a name." };
    renderSection();
    expect(screen.getByTestId("areas-proposal")).toHaveAttribute("data-status", "failed");
    expect(screen.getByTestId("areas-proposal-failure")).toHaveTextContent("areas.0.name: An area needs a name.");
    expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
  });
});
