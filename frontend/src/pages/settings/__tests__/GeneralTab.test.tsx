import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

vi.mock("@/hooks/useConfig", () => ({
  useConfig: () => ({
    data: {
      max_budget_per_job_usd: 5,
      daily_cap_usd: 50,
      monthly_cap_usd: 500,
      rate_limit_per_author_per_hour: 10,
      overrun_mode: "pause",
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useUpdateConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

import GeneralTab from "../GeneralTab";

describe("GeneralTab", () => {
  it("names the providers page in sentence case, as its section title and the sidebar do", () => {
    render(
      <MemoryRouter>
        <GeneralTab />
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: "Open LLM providers" })).toBeInTheDocument();
    expect(screen.getByText(/connection tests live on the LLM providers page\./)).toBeInTheDocument();
    expect(screen.queryByText(/LLM Providers/)).toBeNull();
  });
});
