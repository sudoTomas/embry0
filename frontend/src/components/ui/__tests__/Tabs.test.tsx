import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Tabs, TabsList, TabsTrigger } from "../Tabs";

function renderTabs() {
  return render(
    <Tabs defaultValue="general">
      <TabsList>
        <TabsTrigger value="general">General</TabsTrigger>
        <TabsTrigger value="keys">API keys</TabsTrigger>
      </TabsList>
    </Tabs>,
  );
}

describe("TabsTrigger", () => {
  it("marks the selected tab with the body colour on the overlay, not the gold", async () => {
    renderTabs();
    const general = screen.getByRole("tab", { name: "General" });
    expect(general).toHaveAttribute("aria-selected", "true");
    expect(general.className).toContain("bg-overlay");
    expect(general.className).toMatch(/(^| )text-fg( |$)/);
    expect(general.className).not.toContain("text-primary");

    await userEvent.click(screen.getByRole("tab", { name: "API keys" }));
    expect(screen.getByRole("tab", { name: "API keys" }).className).not.toContain("text-primary");
    expect(general.className).toContain("text-fg-muted");
  });
});
