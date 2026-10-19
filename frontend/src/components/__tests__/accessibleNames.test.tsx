// Icon-only controls carry an accessible name, and no control is nested in
// another (a link wrapping a button is two tab stops that announce as one
// unnamed thing).
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { Pagination } from "@/components/ui/Pagination";
import { MaskedSecretInput } from "@/components/ui/MaskedSecretInput";
import { Label } from "@/components/ui/Label";
import { QuestionsForm } from "@/components/jobs/QuestionsForm";
import { JobsTable } from "@/components/jobs/JobsTable";
import type { JobResponse } from "@/lib/types";

function noNestedControls(container: HTMLElement) {
  expect(container.querySelector("a button, a a, button button, button a"), "a control inside a control").toBeNull();
}

describe("accessible names", () => {
  it("the pager's chevrons say which page they go to", () => {
    render(<Pagination total={50} offset={20} limit={20} onPageChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Previous page" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeInTheDocument();
  });

  it("a secret field is named by its label, and its eye toggle says what it does", () => {
    render(
      <div>
        <Label htmlFor="hmac">HMAC secret</Label>
        <MaskedSecretInput id="hmac" isSet={false} onChange={vi.fn()} />
      </div>,
    );
    expect(screen.getByLabelText("HMAC secret")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show secret" }));
    expect(screen.getByRole("button", { name: "Hide secret" })).toBeInTheDocument();
  });

  it("the box an agent's question is answered in is named by the question", () => {
    render(<QuestionsForm inputs={[{ id: "q1", question: "Which database?", status: "pending" }]} onAnswer={vi.fn()} />);
    expect(screen.getByRole("textbox", { name: "Which database?" })).toBeInTheDocument();
  });

  it("the Jobs list's row actions are named links and buttons, none nested", () => {
    const job = {
      job_id: "job-aaaa1111bbbb", status: "running", repo: "acme/app", task: "Fix it", issue_number: null, issue_id: null,
      pr_url: "https://forge.example/acme/app/pull/1", total_cost_usd: 0, created_at: "2026-10-18T00:00:00Z",
      started_at: null, finished_at: null, error_message: null,
    } as JobResponse;
    const { container } = render(
      <MemoryRouter>
        <JobsTable
          jobs={[job]} total={1} offset={0} limit={20} filters={{}} repoOptions={[]} statusOptions={[]}
          onStatusChange={vi.fn()} onRepoChange={vi.fn()} onPageChange={vi.fn()} onCancel={vi.fn()}
        />
      </MemoryRouter>,
    );
    const row = screen.getByRole("link", { name: /View job/ });
    expect(within(row).getByRole("link", { name: "View pull request" })).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "View logs" })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Cancel job" })).toBeInTheDocument();
    noNestedControls(container);
  });
});
