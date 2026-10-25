import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import type { RepoAreas } from "@/api/areas";

const state: { data: RepoAreas | undefined } = { data: undefined };
const mutate = vi.fn();
vi.mock("@/hooks/useRepoAreas", () => ({
  useRepoAreas: () => ({ data: state.data, isLoading: false }),
  useMapAreas: () => ({ mutate, isPending: false }),
}));
vi.mock("@/hooks/useRunWith", () => ({
  useRunWithKeys: () => ({ data: [{ id: "k-1", label: "team key", provider: "anthropic", scope: "repo", status: "ok" }] }),
}));
vi.mock("@/hooks/useModels", () => ({ useModels: () => ({ models: ["claude-sonnet-5"], providers: [], isLoading: false }) }));

import { RepoAreasSection } from "../RepoAreasSection";

const AREAS: RepoAreas = {
  repo: "acme/web",
  path: ".embry0/areas.yml",
  areas: [{ name: "Billing", slug: "billing", paths: ["services/billing/**"], logs: ["billing-api"], owners: [] }],
  latest_job: { job_id: "job-1", status: "completed", created_at: null, finished_at: null, pr_url: "https://forge/pull/3", live: false },
};

function renderSection() {
  return render(
    <MemoryRouter>
      <RepoAreasSection repo="acme/web" />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mutate.mockReset();
  state.data = AREAS;
});

describe("RepoAreasSection (map areas, spec 2026-10-25)", () => {
  it("lists the areas the file names and what the last map did", () => {
    renderSection();
    expect(screen.getByRole("cell", { name: "Billing" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "services/billing/**" })).toBeInTheDocument();
    expect(screen.getByTestId("repo-areas-latest")).toHaveTextContent("Last mapped: completed · job-1 · its pull request");
    expect(screen.getByRole("link", { name: "its pull request" })).toHaveAttribute("href", "https://forge/pull/3");
  });

  it("maps with the model and key chosen, and not while a map runs", async () => {
    renderSection();
    await userEvent.click(screen.getByRole("button", { name: /Run with/ }));
    await userEvent.selectOptions(screen.getByLabelText("Key"), "k-1");
    await userEvent.click(screen.getByRole("button", { name: "Map areas again" }));
    expect(mutate.mock.calls[0][0]).toEqual({ credential_id: "k-1" });

    state.data = { ...AREAS, latest_job: { ...AREAS.latest_job!, live: true, status: "running" } };
    renderSection();
    expect(screen.getAllByRole("button", { name: /Map areas/ }).at(-1)).toBeDisabled();
  });

  it("says a repository without the file names no areas", () => {
    state.data = { ...AREAS, areas: [], latest_job: null };
    renderSection();
    expect(screen.getByText(/names no areas yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Map areas" })).toBeEnabled();
  });
});
