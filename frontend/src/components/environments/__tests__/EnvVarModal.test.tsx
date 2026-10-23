import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { EnvVarModal } from "../EnvVarModal";

/** The 422 the API answers for a reserved key: a pydantic error list. */
function refusal(key: string, value: string) {
  return Object.assign(new Error("Request failed with status code 422"), {
    response: {
      status: 422,
      data: {
        detail: [
          {
            type: "value_error",
            loc: ["body", "variables", 0, "key"],
            msg: `Value error, Key '${key}' is reserved for embry0 infrastructure.`,
            input: value,
          },
        ],
      },
    },
  });
}

async function fillAndSubmit(key: string, value: string) {
  await userEvent.type(screen.getByLabelText("Key"), key);
  await userEvent.type(screen.getByLabelText("Value"), value);
  await userEvent.click(screen.getByRole("button", { name: "Add" }));
}

describe("EnvVarModal — reserved keys come from the API (#543)", () => {
  it("shows the API's refusal under the Key field and stays open", async () => {
    const onSave = vi.fn().mockRejectedValue(refusal("ANTHROPIC_BASE_URL", "https://example.invalid"));
    const onClose = vi.fn();
    render(<EnvVarModal variable={null} onSave={onSave} onClose={onClose} />);

    await fillAndSubmit("ANTHROPIC_BASE_URL", "https://example.invalid");

    const alert = await screen.findByRole("alert");
    expect(alert, "the API's reason must be shown in the modal").toHaveTextContent(
      "Key 'ANTHROPIC_BASE_URL' is reserved for embry0 infrastructure.",
    );
    expect(alert.textContent, "the rejected value must never be echoed").not.toContain("example.invalid");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("has no client-side copy of the reserved list: every key goes to the API", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<EnvVarModal variable={null} onSave={onSave} onClose={vi.fn()} />);

    await fillAndSubmit("GITHUB_TOKEN", "x");

    expect(onSave, "the modal must not pre-empt the API with its own (drifting) list").toHaveBeenCalledWith(
      expect.objectContaining({ key: "GITHUB_TOKEN" }),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("EnvVarModal — on a phone", () => {
  it("stops short of the screen's edges", () => {
    render(<EnvVarModal variable={null} onSave={vi.fn()} onClose={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: "Add variable" });
    // jsdom has no layout, so this pins the classes: the dialog is as wide as
    // a phone, and the layer under it keeps a 16 px gutter on each side.
    expect(dialog).toHaveClass("w-full");
    expect(dialog.parentElement).toHaveClass("p-4");
  });
});
