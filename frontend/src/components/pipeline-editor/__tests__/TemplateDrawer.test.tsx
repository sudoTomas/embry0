import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const TEMPLATES = [
  { template_id: "t1", name: "quick-fix", description: "One developer, one reviewer", created_at: "2026-10-01T00:00:00Z" },
  { template_id: "t2", name: "research", description: "", created_at: "2026-10-02T00:00:00Z" },
];
let askedFor: string | null = null;
const rename = vi.fn();
const remove = vi.fn();
const create = vi.fn();
vi.mock("@/hooks/usePipelines", () => ({
  useTemplates: () => ({ data: TEMPLATES, isLoading: false }),
  useTemplate: (id: string | null) => {
    askedFor = id;
    return { data: undefined };
  },
  useRenameTemplate: () => ({ mutate: rename, isPending: false }),
  useCreateTemplate: () => ({ mutate: create, isPending: false }),
  useDeleteTemplate: () => ({ mutate: remove, isPending: false }),
}));
vi.mock("@/api/pipelines", () => ({ fetchTemplate: vi.fn() }));
vi.mock("@/api/errorToast", () => ({ toastApiError: vi.fn() }));

import { fetchTemplate } from "@/api/pipelines";
import { toastApiError } from "@/api/errorToast";
import { TemplateDrawer } from "../TemplateDrawer";

beforeEach(() => {
  askedFor = null;
  rename.mockReset();
  remove.mockReset();
  create.mockReset();
  vi.mocked(fetchTemplate).mockReset();
  vi.mocked(toastApiError).mockReset();
});

describe("TemplateDrawer", () => {
  it("is a drawer dialog named Templates at the left edge", () => {
    render(<TemplateDrawer onSelect={vi.fn()} onClose={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: "Templates" });
    expect(dialog).toHaveClass("dialog-in-left");
    expect(dialog).not.toHaveClass("animate-in");
    expect(dialog, "a flex column, so the header stays while the list scrolls").toHaveClass("flex", "flex-col");
    expect(screen.getByText("quick-fix")).toBeInTheDocument();
  });

  it("loads a template from its card", () => {
    render(<TemplateDrawer onSelect={vi.fn()} onClose={vi.fn()} />);
    fireEvent.click(screen.getByText("quick-fix"));
    expect(askedFor, "the card asks for the full template").toBe("t1");
  });

  it("opens a row's menu without loading the template", async () => {
    const user = userEvent.setup();
    render(<TemplateDrawer onSelect={vi.fn()} onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Actions for quick-fix" }));
    expect(screen.getAllByRole("menuitem").map((el) => el.textContent)).toEqual(["Rename", "Duplicate", "Delete"]);
    expect(askedFor, "the menu click never reached the card").toBeNull();
  });

  it("Escape in the rename field leaves rename mode and keeps the drawer open", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<TemplateDrawer onSelect={vi.fn()} onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "Actions for quick-fix" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    const field = await screen.findByRole("textbox", { name: "Template name" });
    await waitFor(() => expect(document.activeElement).toBe(field));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("textbox", { name: "Template name" })).toBeNull();
    expect(onClose, "the field owned that Escape").not.toHaveBeenCalled();
    expect(rename).not.toHaveBeenCalled();
  });

  it("renames on Enter", async () => {
    const user = userEvent.setup();
    render(<TemplateDrawer onSelect={vi.fn()} onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Actions for quick-fix" }));
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    const field = await screen.findByRole("textbox", { name: "Template name" });
    await user.clear(field);
    await user.type(field, "quick-fix-2{Enter}");
    expect(rename).toHaveBeenCalledWith({ templateId: "t1", name: "quick-fix-2" }, expect.anything());
  });

  it("tells the person when the template to duplicate cannot be fetched", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchTemplate).mockRejectedValueOnce(new Error("offline"));
    render(<TemplateDrawer onSelect={vi.fn()} onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Actions for quick-fix" }));
    await user.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    await waitFor(() =>
      expect(toastApiError, "a failed fetch is a toast, not an unhandled rejection").toHaveBeenCalledWith(
        expect.any(Error),
        "Failed to duplicate",
      ),
    );
    expect(create).not.toHaveBeenCalled();
  });

  it("deletes after asking", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<TemplateDrawer onSelect={vi.fn()} onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Actions for research" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(confirm).toHaveBeenCalled();
    expect(remove).toHaveBeenCalledWith("t2", expect.anything());
    confirm.mockRestore();
  });
});
