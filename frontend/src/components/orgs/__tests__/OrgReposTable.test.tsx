import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { OrgReposTable } from "../OrgReposTable";
import { repo } from "./fixtures";

const REPOS = [
  repo("widgets", { registered: true, qa_config: true, hook_present: true, boot_hint: "npm run dev", frontend: true }),
  repo("gadgets", { hook_present: null }, { language: "Python" }),
  repo("old", { skipped: "archived" }, { archived: true, language: null }),
];

function renderTable() {
  return render(
    <MemoryRouter>
      <OrgReposTable repos={REPOS} />
    </MemoryRouter>,
  );
}

const rows = () => screen.getAllByTestId("org-repo-row");

describe("OrgReposTable", () => {
  it("links every row to its repository page, and registered rows to their QA config", () => {
    renderTable();
    const links = screen.getAllByTestId("org-repo-link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "/repos/acme/widgets",
      "/repos/acme/gadgets",
      "/repos/acme/old",
    ]);
    const qa = screen.getAllByTestId("org-repo-qa-link");
    expect(qa).toHaveLength(1);
    expect(qa[0]).toHaveAttribute("href", "/qa/repos/acme%2Fwidgets/config");
  });

  it("shows one chip per sense, with what the credential could not see marked", () => {
    renderTable();
    const [widgets, gadgets, old] = rows();
    const chips = within(widgets).getAllByTestId(/^sense-/);
    expect(chips.map((c) => c.textContent)).toEqual(["intake", "registry", "QA", "watch", "tracker", "UI", "boot"]);
    expect(within(widgets).getByTestId("sense-intake")).toHaveAttribute("data-state", "on");
    expect(within(widgets).getByTestId("sense-watch")).toHaveAttribute("data-state", "off");
    expect(within(gadgets).getByTestId("sense-intake")).toHaveAttribute("data-state", "unknown");
    expect(within(old).queryByTestId("sense-intake")).toBeNull();
    expect(old).toHaveTextContent("archived");
  });

  it("filters by text and by a missing sense", () => {
    renderTable();
    fireEvent.change(screen.getByTestId("org-repos-filter"), { target: { value: "python" } });
    expect(rows()).toHaveLength(1);
    expect(rows()[0]).toHaveTextContent("acme/gadgets");

    fireEvent.change(screen.getByTestId("org-repos-filter"), { target: { value: "" } });
    fireEvent.change(screen.getByTestId("org-repos-missing"), { target: { value: "registry" } });
    expect(rows().map((r) => within(r).getByTestId("org-repo-link").textContent)).toEqual(["acme/gadgets"]);
    expect(screen.getByTestId("org-repos-count")).toHaveTextContent("1 of 3");

    fireEvent.change(screen.getByTestId("org-repos-filter"), { target: { value: "nothing-matches" } });
    expect(screen.queryAllByTestId("org-repo-row")).toHaveLength(0);
    expect(screen.getByText("No repository matches these filters.")).toBeInTheDocument();
  });

  it("shows what each repository takes to maintain under its name, and nothing when the server sent none", () => {
    const hint = "Not registered; a UI with a dev script, so register, then onboard to draft QA.";
    render(
      <MemoryRouter>
        <OrgReposTable
          repos={[
            repo("widgets", { frontend: true, boot_hint: "npm run dev" }, { maintain_hint: hint, maintain_steps: ["register", "onboard"] }),
            repo("gadgets"),
          ]}
        />
      </MemoryRouter>,
    );
    const [widgets, gadgets] = rows();
    const shown = within(widgets).getByTestId("org-repo-hint");
    expect(shown).toHaveTextContent(hint);
    expect(shown).toHaveClass("text-muted-foreground");
    const name = within(widgets).getByTestId("org-repo-link");
    expect(name.compareDocumentPosition(shown) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(gadgets).queryByTestId("org-repo-hint")).toBeNull();
  });

  it("says so when discovery stored nothing", () => {
    render(
      <MemoryRouter>
        <OrgReposTable repos={[]} />
      </MemoryRouter>,
    );
    expect(screen.getByText("No repositories yet")).toBeInTheDocument();
  });
});
