import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type RunWith, runWithBody } from "@/api/runWith";

const keysFor = vi.fn();
vi.mock("@/hooks/useRunWith", () => ({
  useRunWithKeys: (repo?: string | null) => {
    keysFor(repo);
    return {
      data: [
        { id: "k-team", label: "team router", provider: "openrouter", scope: "repo", status: "ok" },
        { id: "k-claude", label: "claude key", provider: "anthropic", scope: "global", status: "ok" },
        { id: "k-off", label: "retired key", provider: "anthropic", scope: "owner", status: "disabled" },
      ],
    };
  },
}));
vi.mock("@/hooks/useModels", () => ({
  useModels: () => ({
    models: ["claude-sonnet-5", "openrouter/qwen3"],
    providers: [
      { name: "anthropic", base_url: "", available: true, model_prefixes: ["claude-"], models: [] },
      { name: "openrouter", base_url: "", available: true, model_prefixes: ["openrouter/"], models: [] },
    ],
    isLoading: false,
  }),
}));

import { RunWithControl } from "../RunWithControl";

function Harness({ onChange }: { onChange: (next: RunWith) => void }) {
  const [value, setValue] = useState<RunWith>({});
  return (
    <RunWithControl
      value={value}
      repo="acme/app"
      onChange={(next) => {
        setValue(next);
        onChange(next);
      }}
    />
  );
}

describe("RunWithControl (run with, spec 2026-10-25)", () => {
  it("is one line until it opens, and reads no keys before", async () => {
    render(<Harness onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Run with: each agent's model, its usual key" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(keysFor).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /Run with/ }));
    expect(keysFor).toHaveBeenCalledWith("acme/app");
  });

  it("offers the keys of the chosen model's provider and says what it will run with", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /Run with/ }));
    await userEvent.selectOptions(screen.getByLabelText("Model"), "openrouter/qwen3");
    const keys = within(screen.getByLabelText("Key")).getAllByRole("option").map((o) => o.textContent);
    expect(keys).toEqual(["The key its bindings pick", "team router · OpenRouter · this repository"]);
    await userEvent.selectOptions(screen.getByLabelText("Key"), "k-team");
    expect(onChange).toHaveBeenLastCalledWith({ model: "openrouter/qwen3", credential_id: "k-team" });
    expect(screen.getByRole("button", { name: "Run with: openrouter/qwen3 · team router" })).toBeInTheDocument();
  });

  it("drops a key the new model's provider cannot use, and never offers a disabled one", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /Run with/ }));
    await userEvent.selectOptions(screen.getByLabelText("Key"), "k-team");
    await userEvent.selectOptions(screen.getByLabelText("Model"), "claude-sonnet-5");
    expect(onChange).toHaveBeenLastCalledWith({ model: "claude-sonnet-5" });
    expect(screen.getByRole("option", { name: /retired key/ })).toBeDisabled();
  });

  it("sends nothing for the usual run", () => {
    expect(runWithBody({})).toEqual({});
    expect(runWithBody(undefined)).toEqual({});
    expect(runWithBody({ credential_id: "k-team" })).toEqual({ run_with: { credential_id: "k-team" } });
  });
});
