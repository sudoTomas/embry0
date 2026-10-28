import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { MapGraph, PartView } from "@/api/maps";

const GRAPH: MapGraph = {
  slug: "shop", title: "Shop", repo: "acme/shop", commit: "abc", source: "a scanner", generated_at: "2026-10-27T15:00:00Z",
  kinds: [{ id: "script", label: "Script" }], areas: [],
  nodes: [
    { id: "script:probe.mjs", kind: "script", area: null, label: "probe.mjs", sub: null, path: "scripts/probe.mjs", line: null, url: null, repo: "acme/shop", x: 0, y: 0, r: 5, signals: ["nothing runs it"], bloat: { score: 70, band: "bloat" } },
    { id: "script:util.mjs", kind: "script", area: null, label: "util.mjs", sub: null, path: "scripts/util.mjs", line: null, url: null, repo: "acme/shop", x: 20, y: 0, r: 5, signals: [] },
  ],
  edges: [],
};
const PART: PartView = {
  map: { slug: "shop", title: "Shop", repo: "acme/shop" },
  node: GRAPH.nodes[0],
  repo: "acme/shop",
  removable: true,
  detail: { narrative: { does: "Prints one row." }, bloat: { score: 70, band: "bloat", against: [{ text: "Nothing runs it.", w: 70 }] } },
  marks: [{ by: "owner@example.com", verdict: "bloat", note: "dead", at: null }, { by: "bo@example.com", verdict: "keep", note: "", at: null }],
  work: null,
  lead: { id: "weed:1", state: "open" },
};

const mutate = vi.fn();
const mutateAsync = vi.fn();
const markMutate = vi.fn();
const hooks = vi.hoisted(() => ({ map: {} as unknown, part: {} as unknown }));
vi.mock("@/hooks/useMaps", () => ({
  useMap: () => hooks.map,
  useMapMarks: () => ({ data: { "script:probe.mjs": { bloat: 1, keep: 1, unsure: 0, mine: "bloat" } } }),
  useMapWork: () => ({ data: {} }),
  usePart: () => hooks.part,
  useMarkPart: () => ({ mutate: markMutate, isPending: false }),
  useSendParts: () => ({ mutate, mutateAsync, isPending: false }),
}));
const admin = { current: true };
vi.mock("@/stores/authStore", () => ({
  useIsAdmin: () => admin.current,
  useAuthStore: (select: (s: { user: { email: string } }) => unknown) => select({ user: { email: "owner@example.com" } }),
}));
vi.mock("@/hooks/useRunWith", () => ({ useRunWithKeys: () => ({ data: [] }) }));
vi.mock("@/hooks/useModels", () => ({ useModels: () => ({ models: [], providers: [], isLoading: false }) }));

import { MapPage } from "../MapPage";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/maps/:slug" element={<MapPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeAll(() => {
  // jsdom has no canvas: the map draws nothing here, and says so once per render without this.
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});

beforeEach(() => {
  mutate.mockReset();
  mutateAsync.mockReset();
  markMutate.mockReset();
  admin.current = true;
  hooks.map = { data: GRAPH, isLoading: false, isError: false };
  hooks.part = { data: PART, isLoading: false, isError: false };
});

describe("MapPage (knowledge maps, spec 2026-10-27)", () => {
  it("shows the map and offers to send every part the viewer marked bloat", async () => {
    mutateAsync.mockResolvedValue({ sent: 1, failed: 0, results: [{ node_id: "script:probe.mjs", job_id: "job-1", error: null }] });
    renderAt("/maps/shop");
    expect(screen.getByRole("heading", { level: 1, name: "Shop" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Send my bloat marks (1)" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("probe.mjs")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Send 1" }));
    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ node_ids: ["script:probe.mjs"], kind: "remove", by: "owner@example.com", together: true }),
    );
  });

  it("sends everything a filter picked out in one job per repository, leaving out what cannot go", async () => {
    const table = { ...GRAPH.nodes[1], id: "table:old_backup", label: "old_backup", kind: "table", path: null, repo: null, removal: "Drop it in a migration." };
    const host = { ...GRAPH.nodes[1], id: "host:box", label: "box", kind: "host", path: null, repo: null, removal: null };
    hooks.map = { data: { ...GRAPH, nodes: [...GRAPH.nodes, table, host] }, isLoading: false, isError: false };
    mutateAsync.mockResolvedValue({ sent: 2, failed: 0, results: [{ node_id: "a", job_id: "job-1", error: null }], jobs: [] });
    renderAt("/maps/shop");
    await userEvent.type(screen.getByRole("textbox", { name: "Find a part" }), "o");
    await userEvent.click(screen.getByRole("button", { name: /picked out$/ }));
    const dialog = screen.getByTestId("batch-send");
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(within(dialog).getByRole("checkbox", { name: "box" })).toBeDisabled();
    expect(within(dialog).getByText("in no repository")).toBeInTheDocument();
    expect(within(dialog).queryByRole("checkbox", { name: "util.mjs" })).toBeNull();
    expect(within(dialog).getByText(/2 parts · 1 job$/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Send 2" }));
    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ node_ids: ["script:probe.mjs", "table:old_backup"], kind: "remove", together: true }),
    );
  });

  it("opens a part from the address, with removal picked for a part the viewer marked bloat", async () => {
    renderAt("/maps/shop?part=script%3Aprobe.mjs");
    const panel = screen.getByTestId("part-panel");
    expect(within(panel).getByText("Prints one row.")).toBeInTheDocument();
    expect(within(panel).getByText("+70 Nothing runs it.")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Bloat" })).toHaveAttribute("aria-pressed", "true");
    expect(within(panel).getByRole("button", { name: "Remove" })).toHaveAttribute("aria-pressed", "true");
    expect(within(panel).getByText(/keep/)).toBeInTheDocument();
    await userEvent.click(within(panel).getByRole("button", { name: "Send to an agent" }));
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ node_ids: ["script:probe.mjs"], kind: "remove", by: "owner@example.com" }),
      expect.anything(),
    );
  });

  it("takes a mark back when the viewer presses their own verdict again", async () => {
    renderAt("/maps/shop?part=script%3Aprobe.mjs");
    await userEvent.click(within(screen.getByTestId("part-panel")).getByRole("button", { name: "Bloat" }));
    expect(markMutate).toHaveBeenCalledWith(expect.objectContaining({ node_id: "script:probe.mjs", verdict: "clear" }), expect.anything());
  });

  it("offers no removal for a whole system, and leaves its bloat mark out of the batch", () => {
    const system = { ...GRAPH.nodes[0], id: "sys:billing", label: "billing", path: null, repo: "acme/billing" };
    hooks.map = { data: { ...GRAPH, repo: null, nodes: [system] }, isLoading: false, isError: false };
    hooks.part = { data: { ...PART, node: system, repo: "acme/billing", removable: false, marks: [{ by: "owner@example.com", verdict: "bloat", note: "", at: null }] }, isLoading: false, isError: false };
    renderAt("/maps/shop?part=sys%3Abilling");
    const panel = screen.getByTestId("part-panel");
    expect(within(panel).getByRole("button", { name: "Remove" })).toBeDisabled();
    expect(within(panel).getByRole("button", { name: "Analysis" })).toHaveAttribute("aria-pressed", "true");
    expect(within(panel).getByText(/a mark stays on the map/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send my bloat marks (0)" })).toBeDisabled();
  });

  it("leaves sending to an admin", () => {
    admin.current = false;
    renderAt("/maps/shop?part=script%3Aprobe.mjs");
    expect(screen.getByRole("button", { name: "Send my bloat marks (1)" })).toBeDisabled();
    expect(within(screen.getByTestId("part-panel")).getByRole("button", { name: "Send to an agent" })).toBeDisabled();
  });

  it("says a map that does not exist is not there, with a way back", () => {
    const notFound = Object.assign(new Error("404"), { isAxiosError: true, response: { status: 404, data: {} } });
    hooks.map = { data: undefined, isLoading: false, isError: true, error: notFound, refetch: vi.fn() };
    renderAt("/maps/nope");
    expect(screen.getByText("No map with that name")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All maps" })).toHaveAttribute("href", "/maps");
  });
});
