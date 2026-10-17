import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { TopBar } from "../TopBar";

vi.mock("@/components/divine/Embry0Mark", () => ({
  Embry0Mark: () => <span data-testid="mark">embry0</span>,
}));

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <TopBar />
    </MemoryRouter>,
  );
}

function breadcrumb() {
  return within(screen.getByRole("navigation", { name: "Breadcrumb" }));
}

describe("TopBar breadcrumbs (RAV-1058 IA sweep)", () => {
  it("labels the root as Overview, matching the sidebar", () => {
    renderAt("/");
    expect(breadcrumb().getByText("Overview")).toBeInTheDocument();
    expect(breadcrumb().queryByText("Dashboard")).toBeNull();
  });

  it("labels /admin/audit as Admin / Audit", () => {
    renderAt("/admin/audit");
    expect(breadcrumb().getByText("Admin")).toBeInTheDocument();
    expect(breadcrumb().getByText("Audit")).toBeInTheDocument();
  });

  it("labels /repos/onboard as Repos / Onboard", () => {
    renderAt("/repos/onboard");
    expect(breadcrumb().getByText("Repos")).toBeInTheDocument();
    expect(breadcrumb().getByText("Onboard")).toBeInTheDocument();
  });

  it("labels /credentials as Credentials, not the app name", () => {
    renderAt("/credentials");
    expect(breadcrumb().getByText("Credentials")).toBeInTheDocument();
    expect(breadcrumb().queryByText("embry0", { selector: "span:not([data-testid])" })).toBeNull();
  });

  it("labels the wizard at /orgs/-/new as Organizations, matching the sidebar", () => {
    renderAt("/orgs/-/new");
    expect(breadcrumb().getByText("Organizations")).toBeInTheDocument();
    // "-" marks an app route, not a name, so the crumb skips it.
    expect(breadcrumb().getByText("new")).toBeInTheDocument();
    expect(breadcrumb().queryByText("-")).toBeNull();
  });

  it("labels /providers as LLM Providers, matching the sidebar", () => {
    renderAt("/providers");
    expect(breadcrumb().getByText("LLM Providers")).toBeInTheDocument();
  });

  it("keeps dynamic second segments raw", () => {
    renderAt("/jobs/job-abc123");
    expect(breadcrumb().getByText("Jobs")).toBeInTheDocument();
    expect(breadcrumb().getByText("job-abc123")).toBeInTheDocument();
  });
});
