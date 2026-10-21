import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetch = vi.fn();
vi.mock("@/api/standards", async () => {
  const actual = await vi.importActual<typeof import("@/api/standards")>("@/api/standards");
  return { ...actual, fetchOwnerStandards: (...a: unknown[]) => mockFetch(...a) };
});

import { OrgStandardSummary } from "../OrgStandardSummary";
import { ownerStandards, state } from "./fixtures";

function renderSummary() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OrgStandardSummary owner="acme" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => mockFetch.mockReset());

describe("OrgStandardSummary", () => {
  it("shows the version, how many repositories are held to it, the lowest score and the way in", async () => {
    mockFetch.mockResolvedValue(
      ownerStandards({
        repos: [
          state("acme/a", { mode: "advise", score: 70 }),
          state("acme/b", { mode: "enforce", score: 41 }),
          state("acme/c", { mode: "off" }),
        ],
      }),
    );
    renderSummary();
    expect(await screen.findByTestId("org-standard-version")).toHaveTextContent("2");
    expect(screen.getByTestId("org-standard-held")).toHaveTextContent("2 repositories");
    expect(screen.getByTestId("org-standard-lowest")).toHaveTextContent("41 b");
    expect(screen.getByTestId("org-standard-link")).toHaveAttribute("href", "/orgs/acme/standards");
    expect(mockFetch).toHaveBeenCalledWith("acme");
  });

  it("says no audit has scored yet", async () => {
    mockFetch.mockResolvedValue(ownerStandards({ repos: [state("acme/a", { mode: "advise" })] }));
    renderSummary();
    expect(await screen.findByTestId("org-standard-lowest")).toHaveTextContent("no audit yet");
    expect(screen.getByTestId("org-standard-held")).toHaveTextContent("1 repository");
  });

  it("names the API path when the owner has no standard yet", async () => {
    mockFetch.mockResolvedValue(ownerStandards({ standards: [] }));
    renderSummary();
    expect(await screen.findByTestId("org-standard-none")).toHaveTextContent("PUT /api/v1/standards/acme/design");
    expect(screen.getByTestId("org-standard-link")).toBeInTheDocument();
  });

  it("offers a retry when the standards do not load", async () => {
    mockFetch.mockRejectedValueOnce(new Error("boom"));
    renderSummary();
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
  });
});
