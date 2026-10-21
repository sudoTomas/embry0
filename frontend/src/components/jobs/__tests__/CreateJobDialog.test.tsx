import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockMutate = vi.fn();
vi.mock("@/hooks/useJobs", () => ({
  useCreateJob: () => ({ mutate: mockMutate, isPending: false }),
}));
vi.mock("@/hooks/usePipelines", () => ({ useTemplates: () => ({ data: [] }) }));
vi.mock("@/hooks/useSandboxProfiles", () => ({ useSandboxProfiles: () => ({ data: [] }) }));
vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [] }) }));
vi.mock("@/hooks/useModels", () => ({ useModels: () => ({ models: [], isLoading: false }) }));

import { CreateJobDialog } from "../CreateJobDialog";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("CreateJobDialog", () => {
  it("submits a git context with repo, ref, and issue number", async () => {
    const user = userEvent.setup();
    render(<CreateJobDialog onClose={vi.fn()} />);

    await user.type(screen.getByLabelText("Repository"), "acme/widgets");
    await user.type(screen.getByLabelText("Branch / Ref (optional)"), "develop");
    await user.type(screen.getByLabelText("Issue Number (optional)"), "42");
    await user.type(screen.getByLabelText("Task"), "Fix the flux capacitor");
    await user.click(screen.getByRole("button", { name: "Create Job" }));

    expect(mockMutate).toHaveBeenCalledWith(
      {
        task: "Fix the flux capacitor",
        context: { type: "git", repo: "acme/widgets", ref: "develop" },
        issue_number: 42,
      },
      expect.anything(),
    );
  });

  it("switches to an http context and submits the url", async () => {
    const user = userEvent.setup();
    render(<CreateJobDialog onClose={vi.fn()} />);

    await user.selectOptions(screen.getByLabelText("Context"), "http");
    expect(screen.queryByLabelText("Repository")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Source URL"), "https://example.com/report.csv");
    await user.type(screen.getByLabelText("Task"), "Summarize this dataset");
    await user.click(screen.getByRole("button", { name: "Create Job" }));

    expect(mockMutate).toHaveBeenCalledWith(
      {
        task: "Summarize this dataset",
        context: { type: "http", url: "https://example.com/report.csv" },
      },
      expect.anything(),
    );
  });

  it("rejects a non-absolute local path client-side", async () => {
    const user = userEvent.setup();
    render(<CreateJobDialog onClose={vi.fn()} />);

    await user.selectOptions(screen.getByLabelText("Context"), "local");
    await user.type(screen.getByLabelText("Host Path"), "relative/path");
    await user.type(screen.getByLabelText("Task"), "Analyze");
    await user.click(screen.getByRole("button", { name: "Create Job" }));

    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("submits a none context with no fields", async () => {
    const user = userEvent.setup();
    render(<CreateJobDialog onClose={vi.fn()} />);

    await user.selectOptions(screen.getByLabelText("Context"), "none");
    await user.type(screen.getByLabelText("Task"), "Research MCP servers");
    await user.click(screen.getByRole("button", { name: "Create Job" }));

    expect(mockMutate).toHaveBeenCalledWith(
      { task: "Research MCP servers", context: { type: "none" } },
      expect.anything(),
    );
  });

  it("folds advanced options into the request", async () => {
    const user = userEvent.setup();
    render(<CreateJobDialog onClose={vi.fn()} />);

    await user.type(screen.getByLabelText("Repository"), "acme/widgets");
    await user.type(screen.getByLabelText("Task"), "Do the thing");
    await user.click(screen.getByLabelText("Advanced options"));
    await user.type(screen.getByLabelText("Max Budget (USD)"), "25");
    await user.click(screen.getByRole("button", { name: "Create Job" }));

    expect(mockMutate).toHaveBeenCalledWith(
      expect.objectContaining({ max_budget_usd: 25 }),
      expect.anything(),
    );
  });

  it("is a modal dialog named for what it does, and Cancel closes it", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<CreateJobDialog onClose={onClose} />);
    const dialog = screen.getByRole("dialog", { name: "Create New Job" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog.parentElement?.parentElement, "a Dialog over the page, not a card in it").toBe(document.body);
    expect(screen.getByLabelText("Advanced options")).toHaveAttribute("role", "switch");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
