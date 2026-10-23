import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const create = vi.fn();
const update = vi.fn();
vi.mock("@/hooks/useHosts", () => ({
  useCreateHost: () => ({ mutate: create, isPending: false }),
  useUpdateHost: () => ({ mutate: update, isPending: false }),
}));

import { HostFormDialog } from "../HostFormDialog";

const HOST = {
  id: "host-1", name: "web-1", address: "100.64.0.10", transport: "tailnet" as const, roles: ["app"],
  log_source: { kind: "docker" as const, selector: "widgets-app" }, tags: ["tag:server"], source: "manual" as const,
  last_seen_at: null, status: "active" as const, created_at: "2026-10-16T12:00:00Z", updated_at: "2026-10-16T12:00:00Z",
  repos: [{ repo: "acme/widgets", role: "app" }],
};
const KINDS = ["journald", "docker", "loki"] as const;

beforeEach(() => {
  create.mockReset();
  update.mockReset();
});

describe("HostFormDialog", () => {
  it("creates a host from the form, with the log source and the repositories it names", async () => {
    render(<HostFormDialog logSourceKinds={[...KINDS]} onClose={vi.fn()} />);
    expect(screen.getByRole("dialog", { name: "Add host" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "SSH" })).toHaveValue("ssh");
    expect(screen.getByRole("option", { name: "Docker" })).toHaveValue("docker");
    // The focus trap moves focus in on the next tick, as ConfirmDialog.test.tsx waits for too.
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Name" })).toHaveFocus());
    await userEvent.type(screen.getByRole("textbox", { name: "Name" }), "web-1");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Transport" }), "ssh");
    await userEvent.type(screen.getByRole("textbox", { name: "Address" }), "web-1.internal:22");
    await userEvent.type(screen.getByRole("textbox", { name: "Roles" }), "app{enter}");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Log source" }), "journald");
    await userEvent.type(screen.getByRole("textbox", { name: "Selector" }), "widgets.service");
    await userEvent.type(screen.getByRole("textbox", { name: "Repositories" }), "acme/widgets{enter}");
    await userEvent.click(screen.getByRole("button", { name: "Add host" }));
    expect(create).toHaveBeenCalledWith(
      {
        name: "web-1", address: "web-1.internal:22", transport: "ssh", roles: ["app"],
        log_source: { kind: "journald", selector: "widgets.service" }, tags: [], repos: [{ repo: "acme/widgets", role: "app" }],
      },
      expect.anything(),
    );
  });

  it("edits a host with its values filled in and sends the whole editable record", async () => {
    render(<HostFormDialog host={HOST} logSourceKinds={[...KINDS]} onClose={vi.fn()} />);
    expect(screen.getByRole("dialog", { name: "Edit web-1" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Selector" })).toHaveValue("widgets-app");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Log source" }), "");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(update).toHaveBeenCalledWith(
      { id: "host-1", patch: { name: "web-1", address: "100.64.0.10", transport: "tailnet", roles: ["app"], log_source: null, tags: ["tag:server"], repos: [{ repo: "acme/widgets", role: "app" }] } },
      expect.anything(),
    );
  });

  it("keeps each linked repository's own role on edit and links a new one as app", async () => {
    const host = { ...HOST, repos: [{ repo: "acme/widgets", role: "db" }] };
    render(<HostFormDialog host={host} logSourceKinds={[...KINDS]} onClose={vi.fn()} />);
    await userEvent.type(screen.getByRole("textbox", { name: "Repositories" }), "acme/api{enter}");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(update).toHaveBeenCalledWith(
      expect.objectContaining({ patch: expect.objectContaining({ repos: [{ repo: "acme/widgets", role: "db" }, { repo: "acme/api", role: "app" }] }) }),
      expect.anything(),
    );
  });

  it("refuses an empty name before asking the server", async () => {
    render(<HostFormDialog logSourceKinds={[...KINDS]} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Add host" }));
    expect(create).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Name the host.");
  });

  it("keeps the form open and shows the server's refusal", async () => {
    create.mockImplementation((_body: unknown, opts: { onError: (e: unknown) => void }) =>
      opts.onError({ response: { status: 422, data: { detail: "ssh host: expected host or host:port" } } }),
    );
    render(<HostFormDialog logSourceKinds={[...KINDS]} onClose={vi.fn()} />);
    await userEvent.type(screen.getByRole("textbox", { name: "Name" }), "web-1");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Transport" }), "ssh");
    await userEvent.type(screen.getByRole("textbox", { name: "Address" }), "ssh://nope");
    await userEvent.click(screen.getByRole("button", { name: "Add host" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("ssh host: expected host or host:port"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Address" })).toHaveValue("ssh://nope");
  });
});
