import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockRun = vi.fn();
const mockDismiss = vi.fn();
vi.mock("@/api/orgs", async () => {
  const actual = await vi.importActual<typeof import("@/api/orgs")>("@/api/orgs");
  return {
    ...actual,
    runOrganizationAction: (...a: unknown[]) => mockRun(...a),
    dismissNeed: (...a: unknown[]) => mockDismiss(...a),
    reopenNeed: vi.fn(),
  };
});

import type { OrgNeeds } from "@/api/orgs";
import { OrgActPanel } from "../OrgActPanel";
import { need, needs } from "./fixtures";

const NEEDS = needs([
  need("n1", "not_registered", ["acme/a", "acme/b"]),
  need("n2", "no_webhook", ["acme/a"]),
  need("n3", "access_limited", ["acme/c"]),
  need("n4", "not_watched", ["acme/d"], { status: "dismissed" }),
]);

function renderPanel(list: OrgNeeds | null = NEEDS, isAdmin = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OrgActPanel owner="acme" needs={list} isAdmin={isAdmin} />
    </QueryClientProvider>,
  );
}

function needRow(id: string) {
  return screen.getAllByTestId("org-act-need").find((el) => el.getAttribute("data-need") === id)!;
}

beforeEach(() => {
  mockRun.mockReset().mockImplementation(async (_owner: string, body: { action: string; repos: string[]; dry_run: boolean }) => ({
    owner: "acme",
    action: body.action,
    dry_run: body.dry_run,
    results: body.repos.map((repo) => ({ repo, outcome: body.dry_run ? "skipped" : "done", detail: `${body.action} ${repo}` })),
  }));
  mockDismiss.mockReset();
});

describe("OrgActPanel", () => {
  it("offers a checkbox only for open needs embry0 can act on", () => {
    renderPanel();
    expect(within(needRow("n1")).getByTestId("org-act-checkbox")).toBeInTheDocument();
    expect(within(needRow("n3")).queryByTestId("org-act-checkbox")).toBeNull();
    expect(needRow("n3")).toHaveTextContent("by hand");
  });

  it("previews as a dry run, then applies the same plan, register first", async () => {
    renderPanel();
    const apply = screen.getByTestId("org-act-apply-button");
    fireEvent.click(within(needRow("n2")).getByTestId("org-act-checkbox"));
    fireEvent.click(within(needRow("n1")).getByTestId("org-act-checkbox"));
    expect(apply).toBeDisabled();

    fireEvent.click(screen.getByTestId("org-act-preview-button"));
    const preview = await screen.findByTestId("org-act-preview");
    expect(mockRun.mock.calls).toEqual([
      ["acme", { action: "register", repos: ["acme/a", "acme/b"], dry_run: true }],
      ["acme", { action: "hook", repos: ["acme/a"], dry_run: true }],
    ]);
    expect(within(preview).getAllByTestId("org-act-result-row")).toHaveLength(3);
    expect(apply).toBeEnabled();

    fireEvent.click(apply);
    const results = await screen.findByTestId("org-act-results");
    expect(mockRun).toHaveBeenLastCalledWith("acme", { action: "hook", repos: ["acme/a"], dry_run: false });
    expect(within(results).getAllByText("done")).toHaveLength(3);
  });

  it("asks for a new preview when the selection changes", async () => {
    renderPanel();
    fireEvent.click(within(needRow("n1")).getByTestId("org-act-checkbox"));
    fireEvent.click(screen.getByTestId("org-act-preview-button"));
    await screen.findByTestId("org-act-preview");
    expect(screen.getByTestId("org-act-apply-button")).toBeEnabled();

    fireEvent.click(within(needRow("n2")).getByTestId("org-act-checkbox"));
    expect(screen.getByTestId("org-act-apply-button")).toBeDisabled();
    expect(screen.queryByTestId("org-act-preview")).toBeNull();
  });

  it("says the server has no actions endpoint yet instead of failing", async () => {
    mockRun.mockRejectedValue({ response: { status: 404, data: { detail: "Not Found" } } });
    renderPanel();
    fireEvent.click(within(needRow("n1")).getByTestId("org-act-checkbox"));
    fireEvent.click(screen.getByTestId("org-act-preview-button"));
    expect(await screen.findByText("This server has no actions endpoint yet")).toBeInTheDocument();
  });

  it("shows a failed call inline with the server's words", async () => {
    mockRun.mockRejectedValue({ response: { status: 422, data: { detail: "acme/a is archived" } } });
    renderPanel();
    fireEvent.click(within(needRow("n1")).getByTestId("org-act-checkbox"));
    fireEvent.click(screen.getByTestId("org-act-preview-button"));
    expect(await screen.findByTestId("org-act-error")).toHaveTextContent("register: acme/a is archived");
    expect(screen.getByTestId("org-act-apply-button")).toBeDisabled();
  });

  it("dismisses a need through the needs endpoint", async () => {
    mockDismiss.mockResolvedValue({ ...NEEDS.needs[2], status: "dismissed" });
    renderPanel();
    fireEvent.click(within(needRow("n3")).getByTestId("org-need-dismiss"));
    await waitFor(() => expect(mockDismiss).toHaveBeenCalledWith("acme", "n3"));
  });

  it("shows the needs EmptyState when the server has no needs endpoint", () => {
    renderPanel(null);
    expect(screen.getByText("This server has no needs endpoint yet")).toBeInTheDocument();
  });

  it("disables acting without admin scope", () => {
    renderPanel(NEEDS, false);
    expect(screen.getByTestId("org-act-admin-note")).toBeInTheDocument();
    expect(within(needRow("n1")).getByTestId("org-act-checkbox")).toBeDisabled();
  });
});
