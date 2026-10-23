import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const duplicate = vi.fn();
const setDefault = vi.fn();
vi.mock("@/hooks/usePipelines", () => ({
  useDuplicateTemplate: () => ({ mutate: duplicate, isPending: false }),
  useSetTemplateDefault: () => ({ mutate: setDefault, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { TemplateActions } from "../TemplateActions";

const TEMPLATE = { template_id: "t1", name: "Issue to PR", description: "", created_at: "2026-10-01T00:00:00Z", is_builtin: false, default_for_kind: null, sandbox_profile: null };

beforeEach(() => {
  duplicate.mockReset();
  setDefault.mockReset();
  vi.mocked(toast.error).mockReset();
});

describe("TemplateActions", () => {
  it("offers Duplicate and a default for each kind, naming the one it holds", async () => {
    render(<TemplateActions template={{ ...TEMPLATE, default_for_kind: "code" }} />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for Issue to PR" }));
    const names = screen.getAllByRole("menuitem").map((i) => i.textContent);
    expect(names).toEqual(["Duplicate…", "Clear default (code)", "Make default for research", "Make default for analysis", "Make default for ops"]);
  });

  it("makes the template the default for a kind", async () => {
    render(<TemplateActions template={TEMPLATE} />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for Issue to PR" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Make default for research" }));
    expect(setDefault).toHaveBeenCalledWith({ templateId: "t1", kind: "research" }, expect.anything());
  });

  it("duplicates through a dialog that proposes a name", async () => {
    duplicate.mockImplementation((_vars: unknown, opts: { onSuccess: (t: unknown) => void }) => opts.onSuccess({ template_id: "t2", name: "Nightly" }));
    render(<TemplateActions template={TEMPLATE} />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for Issue to PR" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Duplicate…" }));
    const name = screen.getByRole("textbox", { name: "Name" });
    expect(name).toHaveValue("Issue to PR (copy)");
    expect(name).toHaveFocus();
    await userEvent.clear(name);
    await userEvent.type(name, "Nightly");
    await userEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(duplicate).toHaveBeenCalledWith({ templateId: "t1", name: "Nightly" }, expect.anything());
    expect(toast.success).toHaveBeenCalledWith("Duplicated as Nightly");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("refuses an empty name before asking the server", async () => {
    render(<TemplateActions template={TEMPLATE} />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for Issue to PR" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Duplicate…" }));
    await userEvent.clear(screen.getByRole("textbox", { name: "Name" }));
    await userEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(duplicate).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Name the copy.");
  });

  it("shows a taken name's refusal in the dialog and keeps what was typed", async () => {
    duplicate.mockImplementation((_vars: unknown, opts: { onError: (e: unknown) => void }) =>
      opts.onError({ response: { status: 409, data: { detail: "a template named 'Nightly' already exists" } } }),
    );
    render(<TemplateActions template={TEMPLATE} />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for Issue to PR" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Duplicate…" }));
    const name = screen.getByRole("textbox", { name: "Name" });
    await userEvent.clear(name);
    await userEvent.type(name, "Nightly");
    await userEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(screen.getByRole("alert")).toHaveTextContent("a template named 'Nightly' already exists");
    expect(name, "the person corrects the name in place; the dialog stays").toHaveValue("Nightly");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(toast.error, "a refusal shown in the dialog is not toasted as well").not.toHaveBeenCalled();
  });
});
