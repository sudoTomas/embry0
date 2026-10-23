import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { DeepStats } from "../DeepStats";
import type { StatsResponse } from "@/lib/types";

const BASE: StatsResponse = {
  total_issues: 10,
  total_jobs: 20,
  completed: 18,
  failed: 2,
  success_rate: 0.9,
  total_cost_usd: 12.5,
  failure_categories: {},
  daily_cost_usd: 3.45,
  monthly_cost_usd: 47.5,
  queue_depth: 3,
  recent_issues: [],
};

function renderStats(stats: StatsResponse) {
  return render(
    <MemoryRouter>
      <DeepStats stats={stats} />
    </MemoryRouter>,
  );
}

describe("DeepStats", () => {
  it("draws the failure categories as labelled bars, largest first, and leaves out the zeros", () => {
    renderStats({ ...BASE, failure_categories: { sandbox: 1, agent: 4, infra: 0 } });
    const panel = screen.getByTestId("panel-Failures by category");
    const rows = within(panel).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual(["Agent4", "Sandbox1"]);
    expect(within(panel).getByLabelText("Agent")).toHaveAttribute("aria-valuenow", "4");
  });

  it("tables the cost by repository, the tokens by agent and the most expensive issues, each issue linked", () => {
    renderStats({
      ...BASE,
      cost_by_repo: [{ repo: "acme/widgets", cost_usd: 4.5, job_count: 3 }],
      tokens_by_agent: [
        { agent_type: "developer", runs: 5, input_tokens: 1200, output_tokens: 300, cache_read_tokens: 9000, cache_creation_tokens: 0, cost_usd: 2.5, cache_hit_rate: 0.88 },
      ],
      top_expensive_issues: [{ trace_id: "tr-1", issue_number: 42, title: "Fix the widget", repo: "acme/widgets", cost_usd: 3.1, job_count: 2 }],
    });
    expect(within(screen.getByTestId("panel-Cost by repository")).getByText("acme/widgets")).toBeInTheDocument();
    expect(within(screen.getByTestId("panel-Tokens by agent")).getByText("Developer")).toBeInTheDocument();
    expect(within(screen.getByTestId("panel-Tokens by agent")).getByText("88%")).toBeInTheDocument();
    const issues = screen.getByTestId("panel-Most expensive issues");
    expect(within(issues).getByRole("link", { name: /acme\/widgets#42/ })).toHaveAttribute("href", "/issues/tr-1");
    expect(within(issues).getByText("Fix the widget")).toBeInTheDocument();
  });

  it("says each panel is empty, and draws no table, when a backend sends nothing for it", () => {
    renderStats(BASE);
    expect(screen.queryAllByRole("table")).toHaveLength(0);
    expect(within(screen.getByTestId("panel-Failures by category")).getByText("No failures recorded.")).toBeInTheDocument();
    expect(within(screen.getByTestId("panel-Cost by repository")).getByText("No jobs with a cost yet.")).toBeInTheDocument();
  });

  it("reports the queue depth, the QA images and the credentials, and hides the two that are not wired", () => {
    renderStats({
      ...BASE,
      qa_prebuilt_images: { active: 4, fresh: 3, stale: [{ repo: "acme/widgets", image_tag: "t1" }], unknown: [] },
      credentials: { count: 5, quarantined: 1, disabled: 0, env_fallback_keys: ["GITHUB_TOKEN"] },
    });
    const fleet = screen.getByTestId("panel-Fleet");
    expect(within(fleet).getByText("Queue depth")).toBeInTheDocument();
    expect(within(fleet).getByText("3")).toBeInTheDocument();
    expect(within(fleet).getByText("3 fresh · 1 stale · 0 unknown")).toBeInTheDocument();
    expect(within(fleet).getByText("5 · 1 quarantined · 0 disabled · env fallback: GITHUB_TOKEN")).toBeInTheDocument();
    expect(within(fleet).getByTitle("Stale: acme/widgets")).toBeInTheDocument();

    renderStats({ ...BASE, qa_prebuilt_images: null, credentials: null });
    const second = screen.getAllByTestId("panel-Fleet")[1];
    expect(within(second).queryByText("QA images")).toBeNull();
    expect(within(second).queryByText("Credentials")).toBeNull();
  });

  it("spends no gold: the Overview's one gold is the Needs-you tile", () => {
    renderStats({ ...BASE, cost_by_repo: [{ repo: "acme/widgets", cost_usd: 4.5, job_count: 3 }] });
    const gold = [...document.querySelectorAll("*")].filter((el) => /(^|\s)(bg-primary|bg-primary-dim|text-primary)(\s|$)/.test(el.getAttribute("class") ?? ""));
    expect(gold).toHaveLength(0);
  });
});
