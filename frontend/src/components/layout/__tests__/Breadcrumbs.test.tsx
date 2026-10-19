import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { Breadcrumbs } from "../Breadcrumbs";

// The trail moved out of the header into a line at the top of the content.
// A sidebar page names its section there, never its own title again (the h1
// says that); a page under it names the parent and the record.
function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Breadcrumbs />
    </MemoryRouter>,
  );
}

function breadcrumb() {
  return within(screen.getByRole("navigation", { name: "Breadcrumb" }));
}

function trail(): string {
  return screen.getByRole("navigation", { name: "Breadcrumb" }).textContent ?? "";
}

describe("Breadcrumbs", () => {
  it("names the section on a sidebar page instead of repeating its title", () => {
    renderAt("/jobs");
    expect(trail()).toBe("Work");
    expect(breadcrumb().queryByText("Jobs")).toBeNull();
  });

  it("names the section of the sidebar pages under /admin", () => {
    renderAt("/admin/audit");
    expect(trail()).toBe("Operations");
    renderAt("/admin/users");
    expect(screen.getAllByRole("navigation", { name: "Breadcrumb" })[1]).toHaveTextContent(/^Settings$/);
  });

  it("names Credentials' section, not the app name and not the page title", () => {
    renderAt("/credentials");
    expect(trail()).toBe("Infra");
    expect(breadcrumb().queryByText("embry0")).toBeNull();
    expect(breadcrumb().queryByText("Credentials")).toBeNull();
  });

  it("keeps the line empty, out of the landmarks, when the section is the page's own name", () => {
    const { container } = renderAt("/settings");
    expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
    expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
  });

  it("never calls the root Dashboard", () => {
    renderAt("/");
    expect(screen.queryByText("Dashboard")).toBeNull();
  });

  it("labels /repos/onboard as Repos / Onboard", () => {
    renderAt("/repos/onboard");
    expect(breadcrumb().getByText("Repos")).toBeInTheDocument();
    expect(breadcrumb().getByText("Onboard")).toBeInTheDocument();
  });

  it("labels the wizard at /orgs/-/new as Organizations, matching the sidebar", () => {
    renderAt("/orgs/-/new");
    expect(breadcrumb().getByText("Organizations")).toBeInTheDocument();
    // "-" marks an app route, not a name, so the crumb skips it.
    expect(breadcrumb().getByText("new")).toBeInTheDocument();
    expect(breadcrumb().queryByText("-")).toBeNull();
  });

  it("names the owner and the page on an organization's design standard", () => {
    renderAt("/orgs/acme/standards");
    expect(trail()).toBe("Organizations/acme/Design standard");
    expect(breadcrumb().getByText("acme").className).toContain("font-mono");
  });

  it("keeps dynamic second segments raw", () => {
    renderAt("/jobs/job-abc123");
    expect(breadcrumb().getByText("Jobs")).toBeInTheDocument();
    expect(breadcrumb().getByText("job-abc123")).toBeInTheDocument();
  });

  it("names a repo in full on its page, not just the owner", () => {
    renderAt("/repos/acme/widgets");
    expect(trail()).toBe("Repos/acme/widgets");
    expect(breadcrumb().getByText("acme/widgets").className).toContain("font-mono");
  });

  it("names the repo in full on its QA run history, decoded", () => {
    renderAt("/qa/repos/acme%2Fwidgets");
    expect(breadcrumb().getByText("QA")).toBeInTheDocument();
    expect(breadcrumb().getByText("acme/widgets")).toBeInTheDocument();
    expect(breadcrumb().queryByText("Repos")).toBeNull();
  });

  it("names the page under a QA repo after the repo", () => {
    renderAt("/qa/repos/acme%2Fwidgets/flake");
    expect(trail()).toBe("QA/acme/widgets/Flake");
    renderAt("/qa/repos/acme%2Fwidgets/apps/web");
    expect(screen.getAllByRole("navigation", { name: "Breadcrumb" })[1]).toHaveTextContent("QA/acme/widgets/web");
  });

  it("names the run on a QA run page", () => {
    renderAt("/qa/runs/job-123");
    expect(trail()).toBe("QA/job-123");
  });
});
