import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useAuthStore } from "@/stores/authStore";

const mockUseWatcherStacks = vi.fn();
const save = vi.fn();
const del = vi.fn();
vi.mock("@/hooks/useWatcherStacks", () => ({
  useWatcherStacks: () => mockUseWatcherStacks(),
  useSaveWatcherStack: () => ({ mutate: save, isPending: false }),
  useDeleteWatcherStack: () => ({ mutate: del, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { WatcherStacksTab } from "../WatcherStacksTab";

const STACK = {
  repo: "acme/widgets",
  logql: '{app="widgets"}',
  tracker: "linear",
  tracker_target: { team_id: "team-1", project_id: "proj-1" },
  settings: { interval_seconds: 600 },
  enabled: true,
};
const LIST = { stacks: [STACK], overridable_settings: ["interval_seconds", "min_log_lines"] };

function ready<T>(data: T) {
  return { data, isPending: false, isError: false, refetch: vi.fn() };
}

beforeEach(() => {
  save.mockReset();
  del.mockReset();
  mockUseWatcherStacks.mockReset().mockReturnValue(ready(LIST));
  useAuthStore.setState({ authMode: "key", user: null });
});

describe("WatcherStacksTab", () => {
  it("lists each stack with its tracker, its state and how many settings it overrides", () => {
    render(<WatcherStacksTab />);
    const row = screen.getByTestId("watcher-stack-row");
    expect(within(row).getByText("acme/widgets")).toBeInTheDocument();
    expect(within(row).getByText("Linear")).toBeInTheDocument();
    expect(within(row).getByText("Enabled")).toBeInTheDocument();
    expect(within(row).getByText("1 override")).toBeInTheDocument();
  });

  it("adds a stack through the editor, sending the contract's body", async () => {
    save.mockImplementation((_v: unknown, opts: { onSuccess: (r: unknown) => void }) => opts.onSuccess({ ...STACK, repo: "acme/gadgets", ignored_settings: [] }));
    render(<WatcherStacksTab />);
    await userEvent.click(screen.getByRole("button", { name: "Add stack" }));
    expect(screen.getByRole("dialog", { name: "Add stack" })).toBeInTheDocument();
    await userEvent.type(screen.getByRole("textbox", { name: "Repository" }), "acme/gadgets");
    await userEvent.type(screen.getByRole("textbox", { name: "LogQL selector" }), '{{app="gadgets"}');
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Tracker" }), "github");
    await userEvent.clear(screen.getByRole("textbox", { name: "Settings overrides" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Settings overrides" }), '{{"interval_seconds": 300}');
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(save).toHaveBeenCalledWith(
      { repo: "acme/gadgets", body: { logql: '{app="gadgets"}', tracker: "github", tracker_target: {}, settings: { interval_seconds: 300 }, enabled: true } },
      expect.anything(),
    );
    expect(toast.success).toHaveBeenCalledWith("Saved acme/gadgets");
  });

  it("refuses to add a repository that is already watched, so the stored stack is not overwritten", async () => {
    render(<WatcherStacksTab />);
    await userEvent.click(screen.getByRole("button", { name: "Add stack" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Repository" }), "Acme/Widgets");
    await userEvent.type(screen.getByRole("textbox", { name: "LogQL selector" }), '{{app="widgets"}');
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(save).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Already watched. Edit it instead.");
    expect(screen.getByRole("dialog", { name: "Add stack" })).toBeInTheDocument();
  });

  it("edits a stack with the repository fixed and the linear target fields filled", async () => {
    render(<WatcherStacksTab />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for acme/widgets" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    expect(screen.getByRole("dialog", { name: "Edit acme/widgets" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Repository" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Linear team id" })).toHaveValue("team-1");
    expect(screen.getByRole("textbox", { name: "Linear project id" })).toHaveValue("proj-1");
  });

  it("refuses settings that are not an object", async () => {
    render(<WatcherStacksTab />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for acme/widgets" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    const settings = screen.getByRole("textbox", { name: "Settings overrides" });
    await userEvent.clear(settings);
    await userEvent.type(settings, "[[1, 2]");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(save).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Settings overrides must be a JSON object, such as {\"interval_seconds\": 600}.");
  });

  it("names the overrides the server will ignore before saving, and after", async () => {
    save.mockImplementation((_v: unknown, opts: { onSuccess: (r: unknown) => void }) => opts.onSuccess({ ...STACK, ignored_settings: ["colour"] }));
    render(<WatcherStacksTab />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for acme/widgets" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    const settings = screen.getByRole("textbox", { name: "Settings overrides" });
    await userEvent.clear(settings);
    await userEvent.type(settings, '{{"colour": 1}');
    expect(screen.getByText("Not a watcher setting, will be dropped: colour")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(toast.info).toHaveBeenCalledWith("Dropped settings the watcher does not know: colour");
  });

  it("disables and enables from the row's menu, and deletes behind a confirm", async () => {
    render(<WatcherStacksTab />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for acme/widgets" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Disable" }));
    expect(save).toHaveBeenCalledWith(
      { repo: "acme/widgets", body: { logql: STACK.logql, tracker: STACK.tracker, tracker_target: STACK.tracker_target, settings: STACK.settings, enabled: false } },
      expect.anything(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Actions for acme/widgets" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    const dialog = screen.getByRole("dialog", { name: "Stop watching acme/widgets?" });
    expect(dialog).toHaveTextContent(/runs, signatures and cases stay/i);
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("acme/widgets", expect.anything());
  });

  it("enables a disabled stack from its menu and says so", async () => {
    mockUseWatcherStacks.mockReturnValue(ready({ ...LIST, stacks: [{ ...STACK, enabled: false }] }));
    save.mockImplementation((_v: unknown, opts: { onSuccess: (r: unknown) => void }) => opts.onSuccess({ ...STACK, ignored_settings: [] }));
    render(<WatcherStacksTab />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for acme/widgets" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Enable" }));
    expect(save).toHaveBeenCalledWith(
      { repo: "acme/widgets", body: { logql: STACK.logql, tracker: STACK.tracker, tracker_target: STACK.tracker_target, settings: STACK.settings, enabled: true } },
      expect.anything(),
    );
    expect(toast.success).toHaveBeenCalledWith("Enabled acme/widgets");
  });

  it("shows the editor's 403 under the form instead of toasting it", async () => {
    save.mockImplementation((_v: unknown, opts: { onError: (e: unknown) => void }) => opts.onError({ response: { status: 403, data: { detail: "Admin scope required" } } }));
    render(<WatcherStacksTab />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for acme/widgets" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Admin scope required.");
    expect(toast.error).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Edit acme/widgets" })).toBeInTheDocument();
  });

  it("offers the writes disabled to an operator session", async () => {
    useAuthStore.setState({ authMode: "session", user: { email: "ops@example.test", name: "Ops", tier: "operator" } });
    render(<WatcherStacksTab />);
    expect(screen.getByRole("button", { name: "Add stack" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Actions for acme/widgets" }));
    expect(screen.getByRole("menuitem", { name: "Edit" })).toBeDisabled();
  });

  it("shows honest empty and error states", () => {
    mockUseWatcherStacks.mockReturnValue(ready({ stacks: [], overridable_settings: [] }));
    render(<WatcherStacksTab />);
    expect(screen.getByText("No watched stacks")).toBeInTheDocument();
    mockUseWatcherStacks.mockReturnValue({ data: undefined, isPending: false, isError: true, refetch: vi.fn() });
    render(<WatcherStacksTab />);
    expect(screen.getByText("Failed to load watched stacks")).toBeInTheDocument();
  });
});
