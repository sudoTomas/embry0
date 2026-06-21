import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

const mockInterpret = vi.fn();
vi.mock("@/api/agent", async () => {
  const actual = await vi.importActual<typeof import("@/api/agent")>("@/api/agent");
  return {
    ...actual,
    interpretCommand: (q: string) => mockInterpret(q),
  };
});

import { CommandPalette } from "../CommandPalette";

function renderPalette() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <CommandPalette />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockInterpret.mockReset();
});

describe("CommandPalette", () => {
  it("is closed by default — no overlay rendered", () => {
    renderPalette();
    expect(screen.queryByTestId("command-palette")).toBeNull();
  });

  it("opens when the user presses Cmd+K", async () => {
    renderPalette();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(screen.getByTestId("command-palette")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("command-palette-input")).toHaveFocus(),
    );
  });

  it("also opens on Ctrl+K (Linux / Windows operators)", () => {
    renderPalette();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByTestId("command-palette")).toBeInTheDocument();
  });

  it("closes on Escape", () => {
    renderPalette();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(screen.getByTestId("command-palette")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("command-palette")).toBeNull();
  });

  it("submitting the form calls interpretCommand and shows the message", async () => {
    mockInterpret.mockResolvedValue({
      intent: "answer",
      message: "5 jobs running right now.",
    });
    renderPalette();

    fireEvent.keyDown(window, { key: "k", metaKey: true });
    const input = screen.getByTestId("command-palette-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "how many jobs running?" } });
    fireEvent.submit(screen.getByTestId("command-palette-form"));

    await waitFor(() =>
      expect(mockInterpret).toHaveBeenCalledWith("how many jobs running?"),
    );
    expect(
      await screen.findByTestId("command-palette-result"),
    ).toHaveTextContent("5 jobs running right now.");
  });

  it("does not call interpret on submit when the query is blank", async () => {
    renderPalette();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    fireEvent.submit(screen.getByTestId("command-palette-form"));
    // give react-query a tick
    await new Promise((r) => setTimeout(r, 0));
    expect(mockInterpret).not.toHaveBeenCalled();
  });
});
