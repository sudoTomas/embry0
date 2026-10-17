import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// A real store, so the drawer flag and its setter behave as in the app.
import { useLayoutStore } from "@/stores/layoutStore";

vi.mock("@/api/queue", () => ({
  fetchQueue: () =>
    Promise.resolve({ depth: 0, pending: 0, running: 0, awaiting_input: 0, paused: 0 }),
}));

import { Sidebar } from "../Sidebar";
import { TopBar } from "../TopBar";

const originalMatchMedia = window.matchMedia;

function pretendViewport(narrow: boolean) {
  window.matchMedia = ((query: string) =>
    ({
      matches: narrow && query.includes("max-width"),
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList) as typeof window.matchMedia;
}

function renderShell() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/"]}>
        <TopBar />
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useLayoutStore.setState({ sidebarOpen: true, drawerOpen: false });
});

afterEach(() => {
  window.matchMedia = originalMatchMedia;
});

describe("Sidebar at phone width — a drawer, not a column", () => {
  it("renders no sidebar until the menu button opens it", () => {
    pretendViewport(true);
    renderShell();
    expect(screen.queryByTestId("sidebar")).toBeNull();
    expect(screen.getByRole("button", { name: "Open menu" })).toBeInTheDocument();
  });

  it("opens as a fixed drawer with a backdrop, and the backdrop closes it", () => {
    pretendViewport(true);
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    const aside = screen.getByTestId("sidebar");
    expect(aside).toHaveAttribute("data-mode", "drawer");
    expect(aside.className).toContain("fixed");
    expect(screen.getByRole("button", { name: "Close menu" })).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("sidebar-backdrop"));
    expect(screen.queryByTestId("sidebar")).toBeNull();
  });

  it("closes once a destination is picked", () => {
    pretendViewport(true);
    useLayoutStore.setState({ drawerOpen: true });
    renderShell();
    fireEvent.click(screen.getByRole("link", { name: "Jobs" }));
    expect(useLayoutStore.getState().drawerOpen).toBe(false);
    expect(screen.queryByTestId("sidebar")).toBeNull();
  });

  it("keeps the desktop column and its collapse toggle at wide widths", () => {
    pretendViewport(false);
    renderShell();
    const aside = screen.getByTestId("sidebar");
    expect(aside).toHaveAttribute("data-mode", "column");
    expect(aside.className).not.toContain("fixed");
    expect(screen.queryByTestId("sidebar-backdrop")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Close sidebar" }));
    expect(useLayoutStore.getState().sidebarOpen).toBe(false);
    expect(useLayoutStore.getState().drawerOpen).toBe(false);
  });

  it("does not persist the drawer flag across reloads", () => {
    pretendViewport(true);
    useLayoutStore.setState({ drawerOpen: true });
    const stored = JSON.parse(window.localStorage.getItem("embry0-layout") ?? "{}") as {
      state?: Record<string, unknown>;
    };
    expect(stored.state).toBeDefined();
    expect("drawerOpen" in (stored.state ?? {})).toBe(false);
  });
});
