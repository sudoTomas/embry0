import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockUseWebhook = vi.fn();
const update = vi.fn();
const setStatus = vi.fn();
const install = vi.fn();
vi.mock("@/hooks/useOrgs", () => ({
  useOrganizationWebhook: (owner: string, enabled: boolean) => mockUseWebhook(owner, enabled),
  useUpdateOrganization: () => ({ mutate: update, isPending: false }),
  useSetOrganizationStatus: () => ({ mutate: setStatus, isPending: false }),
  useInstallOrganizationWebhook: () => ({ mutate: install, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { OrgSettingsTab } from "../OrgSettingsTab";
import { org } from "./fixtures";

const WEBHOOK = {
  owner: "acme",
  installed: true,
  hook: { id: 7, url: "https://hooks.example.test/webhooks/github", events: ["issues", "pull_request"], active: true },
  target_url: "https://hooks.example.test/webhooks/github",
  hooks_visible: true,
  hooks: [
    { id: 7, url: "https://hooks.example.test/webhooks/github", events: ["issues", "pull_request"], active: true },
    { id: 9, url: "https://other.example.test", events: ["push"], active: false },
  ],
};

function ready<T>(data: T) {
  return { data, isPending: false, isError: false, refetch: vi.fn() };
}

beforeEach(() => {
  [update, setStatus, install].forEach((m) => m.mockReset());
  mockUseWebhook.mockReset().mockReturnValue(ready(WEBHOOK));
});

describe("OrgSettingsTab", () => {
  it("saves a display name, and sends an empty one to clear it", async () => {
    render(<OrgSettingsTab org={org({ display_name: "Acme Inc" })} isAdmin />);
    const name = screen.getByRole("textbox", { name: "Display name" });
    expect(name).toHaveValue("Acme Inc");
    await userEvent.clear(name);
    await userEvent.type(name, "Acme");
    await userEvent.click(screen.getByRole("button", { name: "Save name" }));
    expect(update).toHaveBeenCalledWith({ display_name: "Acme" }, expect.anything());
    await userEvent.clear(name);
    await userEvent.click(screen.getByRole("button", { name: "Save name" }));
    // The server keeps a null name (COALESCE); only an empty string clears it.
    expect(update).toHaveBeenLastCalledWith({ display_name: "" }, expect.anything());
  });

  it("says Saved after a name save and re-syncs the field from the stored row", async () => {
    update.mockImplementation((_body: unknown, opts: { onSuccess: () => void }) => opts.onSuccess());
    const view = render(<OrgSettingsTab org={org({ display_name: "Acme Inc" })} isAdmin />);
    await userEvent.click(screen.getByRole("button", { name: "Save name" }));
    expect(toast.success).toHaveBeenCalledWith("Saved");
    view.rerender(<OrgSettingsTab org={org({ display_name: "Widgets Co" })} isAdmin />);
    expect(screen.getByRole("textbox", { name: "Display name" })).toHaveValue("Widgets Co");
  });

  it("switches automatic registration on through the settings patch", async () => {
    render(<OrgSettingsTab org={org()} isAdmin />);
    const auto = screen.getByRole("switch", { name: "Register repositories automatically" });
    expect(auto).toHaveAttribute("aria-checked", "false");
    await userEvent.click(auto);
    expect(update).toHaveBeenCalledWith({ settings: { auto_register: true } }, expect.anything());
  });

  it("shows whether the webhook points here, lists the hooks the credential sees, and installs one", async () => {
    install.mockImplementation((_v: unknown, opts: { onSuccess: (r: unknown) => void }) =>
      opts.onSuccess({ owner: "acme", target_url: WEBHOOK.target_url, outcome: "already", hook: WEBHOOK.hook, detail: "" }),
    );
    render(<OrgSettingsTab org={org()} isAdmin />);
    const section = screen.getByTestId("org-webhook");
    expect(within(section).getByText("Installed")).toBeInTheDocument();
    expect(within(section).getAllByRole("listitem")).toHaveLength(2);
    expect(within(section).getByText("https://other.example.test")).toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "Install webhook" })).toBeNull();
    await userEvent.click(within(section).getByRole("button", { name: "Install again" }));
    expect(install).toHaveBeenCalled();
    expect(toast.info).toHaveBeenCalledWith("A webhook already points at this deployment.");
  });

  it("does not read or offer the webhook for a GitLab group", () => {
    render(<OrgSettingsTab org={org({ forge: "gitlab" })} isAdmin />);
    expect(mockUseWebhook).toHaveBeenLastCalledWith("acme", false);
    expect(screen.getByTestId("org-webhook")).toHaveTextContent("Organization webhooks are a GitHub feature.");
  });

  it("pauses and resumes without a confirm, and removes behind one", async () => {
    render(<OrgSettingsTab org={org()} isAdmin />);
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(setStatus).toHaveBeenCalledWith("paused", expect.anything());
    await userEvent.click(screen.getByRole("button", { name: "Remove organization" }));
    const dialog = screen.getByRole("dialog", { name: "Remove acme?" });
    expect(dialog).toHaveTextContent(/repositories stay registered/i);
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(setStatus).toHaveBeenLastCalledWith("removed", expect.anything());
  });

  it("offers Resume to a paused owner and Restore to a removed one", async () => {
    const paused = render(<OrgSettingsTab org={org({ status: "paused" })} isAdmin />);
    await userEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(setStatus).toHaveBeenCalledWith("active", expect.anything());
    // The paused owner's buttons leave the document before the removed one's are counted.
    paused.unmount();
    render(<OrgSettingsTab org={org({ status: "removed" })} isAdmin />);
    expect(screen.getByRole("button", { name: "Restore" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove organization" })).toBeNull();
  });

  it("offers every write disabled below admin and spends no gold, the switch on included", () => {
    mockUseWebhook.mockReturnValue(ready({ ...WEBHOOK, installed: false, hook: null }));
    render(<OrgSettingsTab org={org({ settings: { auto_register: true } })} isAdmin={false} />);
    expect(screen.getByRole("button", { name: "Save name" })).toBeDisabled();
    const auto = screen.getByRole("switch", { name: "Register repositories automatically" });
    expect(auto).toBeDisabled();
    expect(auto).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("button", { name: "Pause" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Install webhook" })).toBeDisabled();
    const gold = [...document.querySelectorAll("*")].filter((el) => /(^|\s)(bg-primary|bg-primary-dim|text-primary)(\s|$)/.test(el.getAttribute("class") ?? ""));
    expect(gold).toHaveLength(0);
  });
});
