import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";

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

