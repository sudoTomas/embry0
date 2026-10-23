import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfirmDialog } from "../ConfirmDialog";

describe("ConfirmDialog", () => {
  it("names itself by its title, puts focus on Cancel and confirms on the labelled button", async () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(
      <ConfirmDialog title="Remove web-1?" confirmLabel="Remove" destructive onConfirm={onConfirm} onClose={onClose}>
        Its repository links are dropped.
      </ConfirmDialog>,
    );
    const dialog = screen.getByRole("dialog", { name: "Remove web-1?" });
    expect(dialog).toHaveTextContent("Its repository links are dropped.");
    // The focus trap moves focus in on the next tick, as Dialog.test.tsx waits for too.
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus());
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("disables both buttons while pending and says so", () => {
    render(
      <ConfirmDialog title="Import" confirmLabel="Import" pending onConfirm={vi.fn()} onClose={vi.fn()}>
        body
      </ConfirmDialog>,
    );
    expect(screen.getByRole("button", { name: "Working…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  it("does not close on a backdrop click", async () => {
    const onClose = vi.fn();
    render(
      <ConfirmDialog title="Sure?" confirmLabel="Yes" onConfirm={vi.fn()} onClose={onClose}>
        body
      </ConfirmDialog>,
    );
    const dialog = screen.getByRole("dialog");
    await userEvent.click(dialog.parentElement as HTMLElement);
    expect(onClose).not.toHaveBeenCalled();
  });
});
