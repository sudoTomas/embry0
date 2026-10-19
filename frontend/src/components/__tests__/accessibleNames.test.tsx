// Icon-only controls carry an accessible name, and no control is nested in
// another (a link wrapping a button is two tab stops that announce as one
// unnamed thing).
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Pagination } from "@/components/ui/Pagination";
import { TagInput } from "@/components/ui/TagInput";
import { MaskedSecretInput } from "@/components/ui/MaskedSecretInput";
import { Label } from "@/components/ui/Label";
import { QuestionsForm } from "@/components/jobs/QuestionsForm";
import { JobsTable } from "@/components/jobs/JobsTable";
import type { JobResponse } from "@/lib/types";

const NOW = "2026-10-18T00:00:00Z";
const issue = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("@/hooks/useIssues", () => ({
  useIssue: () => ({ data: issue.current, isLoading: false, isError: false, refetch: vi.fn() }),
  useIssueActivity: () => ({ data: [] }),
  useTriageIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useSyncIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateIssue: () => ({ mutate: vi.fn() }),
}));
vi.mock("@/components/issues/IssueActivityFeed", () => ({ IssueActivityFeed: () => null }));
vi.mock("@/components/issues/IssueQuestionsPanel", () => ({ IssueQuestionsPanel: () => null }));
vi.mock("@/api/integrations", () => ({
  fetchIntegrationConfig: vi.fn(async () => ({
    trigger_labels: ["embry0", "autofix"], webhook_secret_set: true, slack_webhook_url_set: false,
    slack_webhook_url_masked: "", telegram_bot_token_set: false, telegram_bot_token_masked: "", telegram_chat_id: "",
    webhook_notify_url: "", webhook_notify_secret_set: false, webhook_notify_secret_masked: "",
    linear_api_key_set: false, linear_api_key_masked: "", linear_webhook_secret_set: false,
    linear_webhook_secret_masked: "", linear_repo_map: "{}",
  })),
  updateIntegrationConfig: vi.fn(),
}));

import IntegrationsTab from "@/pages/settings/IntegrationsTab";
import { IssueDetailPage } from "@/pages/IssueDetailPage";

/** Every control in `container` that a screen reader announces without a name. */
function unnamedControls(container: HTMLElement): string[] {
  const unnamed = (name: string) => name.trim() === "";
  return (["button", "link", "textbox", "combobox"] as const)
    .flatMap((role) => within(container).queryAllByRole(role, { name: unnamed }))
    .map((el) => el.outerHTML.slice(0, 160));
}

function withClient(ui: React.ReactNode, path = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

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

  it("each tag's remove button says which tag it removes, and its box is named by its label", () => {
    const { container } = render(
      <div>
        <Label htmlFor="labels">Trigger labels</Label>
        <TagInput id="labels" value={["agent", "bot"]} onChange={vi.fn()} />
      </div>,
    );
    expect(screen.getByRole("textbox", { name: "Trigger labels" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove agent" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove bot" })).toBeInTheDocument();
    expect(unnamedControls(container)).toEqual([]);
  });

  it("Settings > Integrations names its copy button and every other control", async () => {
    const { container } = withClient(<IntegrationsTab />);
    await screen.findByText("Webhook configuration");
    expect(screen.getByRole("button", { name: "Copy webhook URL" })).toBeInTheDocument();
    expect(container.querySelector("input[readonly]"), "the address to paste into the forge").toHaveAccessibleName("Webhook URL");
    expect(screen.getByRole("textbox", { name: "Trigger labels" })).toBeInTheDocument();
    expect(unnamedControls(container)).toEqual([]);
  });

  it("the issue page's tracker link names the tracker it opens", () => {
    issue.current = {
      id: "iss-1", title: "Fix the login redirect", body: "", status: "open", priority: "medium", labels: ["bug"],
      repo: "group/project", parent_issue_id: null, tracker: "gitlab", tracker_key: "12",
      tracker_url: "https://gitlab.example.com/group/project/-/issues/12", github_sync_enabled: false,
      github_synced_at: null, created_by: "user", created_at: NOW, updated_at: NOW, children: [], children_count: 0,
      children_closed_count: 0, jobs: [], jobs_count: 0, active_agent: null, notification_channels: ["dashboard"],
    };
    const { container } = withClient(
      <Routes>
        <Route path="/issues/:id" element={<IssueDetailPage />} />
      </Routes>,
      "/issues/iss-1",
    );
    expect(screen.getByRole("combobox", { name: "Status" })).toHaveValue("open");
    expect(screen.getByRole("button", { name: "Remove bug" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Add a label" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Priority" })).toHaveValue("medium");
    expect(screen.getByRole("link", { name: "Open #12 in GitLab" })).toHaveAttribute(
      "href",
      "https://gitlab.example.com/group/project/-/issues/12",
    );
    expect(unnamedControls(container)).toEqual([]);
  });
});
