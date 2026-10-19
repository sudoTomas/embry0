import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Routes, Route } from "react-router";

// Stub the layout sub-components so this test focuses on the shell contract
// rather than re-asserting sidebar/topbar behavior (covered elsewhere).
vi.mock("../Sidebar", () => ({
  Sidebar: () => <div data-testid="stub-sidebar" />,
}));
vi.mock("../TopBar", () => ({
  TopBar: () => <div data-testid="stub-topbar" />,
}));
vi.mock("@/components/dashboard/BriefBar", () => ({
  BriefBar: () => <div data-testid="stub-briefbar" />,
}));
vi.mock("@/stores/layoutStore", () => ({
  useLayoutStore: (selector?: (s: { densityMode: string }) => unknown) =>
    selector ? selector({ densityMode: "comfortable" }) : { densityMode: "comfortable" },
}));

import { AppLayout } from "../AppLayout";

describe("AppLayout", () => {
  it("renders sidebar, topbar, and the routed outlet content", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<div>home</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("stub-sidebar")).toBeInTheDocument();
    expect(screen.getByTestId("stub-topbar")).toBeInTheDocument();
    expect(screen.getByText("home")).toBeInTheDocument();
  });

  it("puts the brief at the top of the Overview and the breadcrumb on every other route", () => {
    const { unmount } = render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<div>home</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("stub-briefbar")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
    unmount();

    render(
      <MemoryRouter initialEntries={["/jobs"]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="jobs" element={<div>jobs</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("stub-briefbar")).toBeNull();
    // A sidebar page's line names its section; its h1 names the page.
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent("Work");
  });

  it("mounts no Companion surfaces (FAB, palette, notifications) — RAV-1058 A2 drop", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<div>home</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("feedback-fab")).toBeNull();
    expect(screen.queryByTestId("command-palette")).toBeNull();
  });
  it("sizes the shell to the visible viewport and keeps a scroll inside the content", () => {
    const { container } = render(
      <MemoryRouter initialEntries={["/jobs"]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="jobs" element={<div>jobs</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    // 100vh is the viewport with a phone's browser bar hidden, so with the bar
    // shown the shell's bottom sat behind it and could not be scrolled into view.
    const shell = container.querySelector("[data-density]") as HTMLElement;
    expect(shell.className).toContain("h-dvh");
    expect(shell.className).not.toContain("h-screen");
    // The content is the only scroller: it must not chain its scroll into the
    // shell (the header and sidebar bounced) or shift when its scrollbar shows.
    const main = screen.getByRole("main");
    expect(main.className).toContain("overscroll-contain");
    expect(main.className).toContain("[scrollbar-gutter:stable]");
  });
});

/** A page with state of its own, a tab link (a search change) and a link to
 * another page (a path change). */
function StatefulPage({ name }: { name: string }) {
  const [clicks, setClicks] = useState(0);
  return (
    <div>
      <h1>{name}</h1>
      <button type="button" onClick={() => setClicks((n) => n + 1)}>
        clicked {clicks}
      </button>
      <Link to="?tab=second">second tab</Link>
      <Link to="/issues">to issues</Link>
    </div>
  );
}

function renderShellAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="jobs" element={<StatefulPage name="jobs" />} />
          <Route path="issues" element={<StatefulPage name="issues" />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

/** The cell the routed page arrives in: main's one child. */
function pageCell() {
  const cell = screen.getByRole("main").firstElementChild;
  if (!(cell instanceof HTMLElement)) throw new Error("main holds no page cell");
  return cell;
}

describe("AppLayout — each page arrives as it opens", () => {
  it("brings the page in through .motion-enter while the shell around it stays put", () => {
    renderShellAt("/jobs");
    const cell = pageCell();
    expect(cell.className).toContain("motion-enter");
    expect(cell).toContainElement(screen.getByRole("heading", { name: "jobs" }));
    expect(cell).not.toContainElement(screen.getByTestId("stub-topbar"));
    expect(cell).not.toContainElement(screen.getByTestId("stub-sidebar"));
    expect(cell).not.toContainElement(screen.getByRole("navigation", { name: "Breadcrumb" }));
  });

  it("brings the next page in afresh when the path changes", () => {
    renderShellAt("/jobs");
    const before = pageCell();
    fireEvent.click(screen.getByRole("link", { name: "to issues" }));
    expect(screen.getByRole("heading", { name: "issues" })).toBeInTheDocument();
    const after = pageCell();
    expect(after, "a new path mounts a new cell, so its arrival plays").not.toBe(before);
    expect(after.className).toContain("motion-enter");
  });

  it("keeps the page, its state and its cell when only the search changes", () => {
    renderShellAt("/jobs");
    fireEvent.click(screen.getByRole("button", { name: "clicked 0" }));
    const before = pageCell();
    fireEvent.click(screen.getByRole("link", { name: "second tab" }));
    expect(pageCell(), "a tab or a filter must not replay the arrival").toBe(before);
    expect(screen.getByRole("button", { name: "clicked 1" })).toBeInTheDocument();
  });

  it("lets a page fill the content area's height and still end on its padding", () => {
    renderShellAt("/jobs");
    // One grid cell at least as tall as main: a page's h-full resolves against
    // it (the pipeline editor fills the area), and a page taller than main
    // grows the cell, so main's bottom padding still follows the content.
    const cell = pageCell();
    expect(cell.className).toContain("grid");
    expect(cell.className).toContain("min-h-full");
    expect(cell.className).toContain("grid-rows-[1fr]");
    expect(cell.className).toContain("grid-cols-[minmax(0,1fr)]");
  });
});

