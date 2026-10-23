import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Kbd } from "../Kbd";

describe("Kbd", () => {
  it("renders a kbd element in the mono face", () => {
    render(<Kbd>Esc</Kbd>);
    const kbd = screen.getByText("Esc");
    expect(kbd.tagName).toBe("KBD");
    expect(kbd).toHaveClass("font-mono");
  });
});
