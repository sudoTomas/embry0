import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";

vi.mock("@/stores/layoutStore", () => ({
  useLayoutStore: (selector?: (s: { sidebarOpen: boolean }) => unknown) =>
    selector ? selector({ sidebarOpen: true }) : { sidebarOpen: true },
}));

import { Sidebar } from "../Sidebar";

function renderSidebar() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Sidebar />
    </MemoryRouter>,
  );
}

describe("Sidebar — unified IA (ticket 011)", () => {
  it("renders the six section labels in order: Overview, Work, Pipelines & QA, Infra, Insights, Settings", () => {
    renderSidebar();
    const labels = screen
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent?.trim());
    expect(labels).toEqual([
      "Overview",
      "Work",
      "Pipelines & QA",
      "Infra",
      "Insights",
      "Settings",
    ]);
  });

  it("Work section contains Issues, Jobs, Tasks, Proposals in that order", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Work" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links.map((a) => a.textContent?.trim())).toEqual([
      "Issues",
      "Jobs",
      "Tasks",
      "Proposals",
    ]);
  });

  it("Infra section contains Sandboxes, Agents, Environments, Repos in that order", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Infra" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links.map((a) => a.textContent?.trim())).toEqual([
      "Sandboxes",
      "Agents",
      "Environments",
      "Repos",
    ]);
  });

  it("Pipelines & QA section contains Pipelines, QA, Provider overrides", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Pipelines & QA" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links.map((a) => a.textContent?.trim())).toEqual([
      "Pipelines",
      "QA",
      "Provider overrides",
    ]);
  });

  it("Overview section contains a single Overview link to /", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Overview" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/");
    expect(links[0].textContent?.trim()).toBe("Overview");
  });

  it("Insights section contains a single Insights link to /insights", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Insights" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/insights");
  });

  it("every nav item points to its expected route (blocker-ticket coverage check)", () => {
    renderSidebar();
    const expectedHrefs: Record<string, string> = {
      Overview: "/",
      Issues: "/issues",
      Jobs: "/jobs",
      Tasks: "/tasks",
      Proposals: "/proposals",
      Pipelines: "/pipelines",
      QA: "/qa/repos",
      "Provider overrides": "/qa/admin/providers",
      Sandboxes: "/sandboxes",
      Agents: "/agents",
      Environments: "/environments",
      Repos: "/repos",
      Insights: "/insights",
      Settings: "/settings",
    };
    for (const [label, href] of Object.entries(expectedHrefs)) {
      const link = screen.getByRole("link", { name: label });
      expect(link, `nav link "${label}"`).toHaveAttribute("href", href);
    }
  });
});
