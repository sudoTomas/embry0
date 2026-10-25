import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LONG_MODEL_LIST, ModelSelect } from "../ModelSelect";

const ANTHROPIC = {
  name: "anthropic",
  available: true,
  model_prefixes: ["claude-"],
  models: [{ model: "claude-opus-5", pricing_usd_per_mtok: { input: 5, output: 25 } }],
};

/** OpenRouter's live catalog: one Claude, one Gemini and enough others to make the list long. */
const OPENROUTER = {
  name: "openrouter",
  available: true,
  model_prefixes: ["openrouter/"],
  models: [
    {
      model: "openrouter/anthropic/claude-opus-5.5",
      name: "Anthropic: Claude Opus 5.5",
      vendor: "anthropic",
      pricing_usd_per_mtok: { input: 4, output: 20 },
    },
    {
      model: "openrouter/google/gemini-4-argon",
      name: "Google: Gemini 4 Argon",
      vendor: "google",
      pricing_usd_per_mtok: { input: 2, output: 10 },
    },
    ...Array.from({ length: LONG_MODEL_LIST }, (_, i) => ({
      model: `openrouter/qwen/model-${i}`,
      name: `Qwen: Model ${i}`,
      vendor: "qwen",
      pricing_usd_per_mtok: { input: 0.25, output: 1 },
    })),
  ],
};

const PROVIDERS = [ANTHROPIC, OPENROUTER];
const LONG = [...ANTHROPIC.models, ...OPENROUTER.models].map((m) => m.model);

describe("ModelSelect", () => {
  it("keeps a short list a plain select, exactly as before", () => {
    render(
      <ModelSelect
        aria-label="Model"
        models={["claude-opus-5", "grok-4.7"]}
        providers={PROVIDERS}
        leading={<option value="">Auto</option>}
        value=""
        onChange={() => {}}
      />,
    );
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    const options = within(screen.getByRole("combobox", { name: "Model" })).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["Auto", "claude-opus-5", "grok-4.7"]);
  });

  it("groups a long list by provider and maker and prices each model", () => {
    render(<ModelSelect aria-label="Model" models={LONG} providers={PROVIDERS} value="" onChange={() => {}} />);
    const select = screen.getByRole("combobox", { name: "Model" });
    const groups = [...select.querySelectorAll("optgroup")].map((g) => g.label);
    expect(groups).toEqual(["Anthropic", "OpenRouter · Anthropic", "OpenRouter · Google", "OpenRouter · Qwen"]);
    expect(within(select).getByRole("option", { name: "openrouter/anthropic/claude-opus-5.5 · $4 / $20" })).toBeInTheDocument();
    expect(within(select).getByRole("option", { name: "openrouter/qwen/model-0 · $0.25 / $1" })).toBeInTheDocument();
    expect(screen.getByText(/prices per million tokens/)).toBeInTheDocument();
  });

  it("filters by id and name, and keeps the chosen model listed", async () => {
    const onChange = vi.fn();
    render(
      <ModelSelect
        aria-label="Model"
        models={LONG}
        providers={PROVIDERS}
        value="openrouter/qwen/model-3"
        onChange={onChange}
      />,
    );
    await userEvent.type(screen.getByRole("searchbox", { name: "Filter models" }), "gemini");
    const select = screen.getByRole("combobox", { name: "Model" });
    expect(within(select).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual([
      "openrouter/google/gemini-4-argon",
      "openrouter/qwen/model-3",
    ]);
    expect(screen.getByText(/^2 of 33 models/)).toBeInTheDocument();

    await userEvent.clear(screen.getByRole("searchbox", { name: "Filter models" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "Filter models" }), "opus");
    expect(within(select).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual([
      "claude-opus-5",
      "openrouter/anthropic/claude-opus-5.5",
      "openrouter/qwen/model-3",
    ]);
    await userEvent.selectOptions(select, "openrouter/anthropic/claude-opus-5.5");
    expect(onChange).toHaveBeenCalled();
  });
});
