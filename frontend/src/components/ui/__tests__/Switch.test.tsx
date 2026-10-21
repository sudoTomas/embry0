import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Label } from "../Label";
import { Switch } from "../Switch";

describe("Switch", () => {
  it("is a switch that reports the flipped value", async () => {
    const user = userEvent.setup();
    const onCheckedChange = vi.fn();
    render(<Switch checked={false} onCheckedChange={onCheckedChange} label="Review pull requests" />);
    const sw = screen.getByRole("switch", { name: "Review pull requests" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    expect(sw).toHaveClass("bg-white/20");
    await user.click(sw);
    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });

  it("shows the on state and does not flip while disabled", async () => {
    const user = userEvent.setup();
    const onCheckedChange = vi.fn();
    render(<Switch checked onCheckedChange={onCheckedChange} label="Review pull requests" disabled />);
    const sw = screen.getByRole("switch");
    expect(sw).toHaveAttribute("aria-checked", "true");
    expect(sw).toHaveClass("bg-primary");
    expect(sw).toBeDisabled();
    await user.click(sw);
    expect(onCheckedChange).not.toHaveBeenCalled();
  });

  it("takes its name from a label pointed at its id", () => {
    render(
      <>
        <Label htmlFor="show-advanced">Advanced options</Label>
        <Switch id="show-advanced" checked={false} onCheckedChange={vi.fn()} />
      </>,
    );
    expect(screen.getByLabelText("Advanced options")).toHaveAttribute("role", "switch");
  });
});
