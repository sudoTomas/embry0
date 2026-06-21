import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";

// Stub the layout sub-components so this test focuses on the global FAB mount
// rather than re-asserting sidebar/topbar behavior (covered elsewhere).
vi.mock("../Sidebar", () => ({
  Sidebar: () => <div data-testid="stub-sidebar" />,
}));
vi.mock("../TopBar", () => ({
  TopBar: () => <div data-testid="stub-topbar" />,
}));
// CommandPalette uses react-query (useMutation) and has its own dedicated
// test; stub it here so this test stays focused on the global FAB mount and
// does not require a QueryClientProvider.
vi.mock("@/components/CommandPalette", () => ({
  CommandPalette: () => <div data-testid="stub-command-palette" />,
}));
vi.mock("@/stores/layoutStore", () => ({
  useLayoutStore: (selector?: (s: { densityMode: string }) => unknown) =>
    selector ? selector({ densityMode: "comfortable" }) : { densityMode: "comfortable" },
}));

import { AppLayout } from "../AppLayout";

describe("AppLayout", () => {
  it("mounts the global FeedbackFab so feedback is reachable from every route", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<div>home</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("feedback-fab")).toBeInTheDocument();
  });
});
