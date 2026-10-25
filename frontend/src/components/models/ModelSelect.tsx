import { type ReactNode, type SelectHTMLAttributes, useMemo, useState } from "react";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { providerForModel, type RoutableProvider } from "@/lib/providers";
import { providerLabel } from "@/lib/status";

/** A list longer than this gets a filter box, groups by provider and maker, and prices (OpenRouter's catalog). */
export const LONG_MODEL_LIST = 30;

interface PricedModel {
  model: string;
  name?: string;
  vendor?: string;
  pricing_usd_per_mtok?: { input: number | null; output: number | null };
}

export interface ModelSelectProvider extends RoutableProvider {
  name: string;
  models: PricedModel[];
}

export interface ModelSelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "children"> {
  /** The model ids to offer, in order. */
  models: readonly string[];
  /** The models catalog, to group, name and price the models. */
  providers?: readonly ModelSelectProvider[];
  /** Options before the models ("Each agent's own model", "(none)"). */
  leading?: ReactNode;
  /** Options after the models ("Custom..."). */
  trailing?: ReactNode;
  /** An option's text; the model id by default. */
  labelOf?: (model: string) => string;
  /** Whether a model's option is disabled. */
  disabledOf?: (model: string) => boolean;
  /** The filter box's accessible name. */
  filterLabel?: string;
}

function price(n: number | null | undefined): string {
  if (n == null) return "?";
  return `$${n < 1 ? n.toFixed(2) : Number(n.toFixed(2)).toString()}`;
}

/** The maker in an OpenRouter catalog entry: the part of its name before ":", else its vendor. */
function makerOf(entry: PricedModel | undefined, model: string): string {
  const named = entry?.name?.includes(":") ? entry.name.split(":")[0].trim() : "";
  if (named) return named;
  const vendor = entry?.vendor ?? model.replace(/^openrouter\//, "").split("/")[0];
  return providerLabel(vendor);
}

/**
 * A model selector. Short lists are a plain select, exactly as before. A long
 * one (OpenRouter's live catalog has hundreds) gets a filter box over the id
 * and name, options grouped by provider (and, for OpenRouter, by maker), and
 * each option's input/output price per million tokens. The chosen model stays
 * listed whatever the filter.
 */
export function ModelSelect({
  models,
  providers = [],
  leading,
  trailing,
  labelOf,
  disabledOf,
  filterLabel = "Filter models",
  value,
  ...selectProps
}: ModelSelectProps) {
  const [query, setQuery] = useState("");
  const long = models.length > LONG_MODEL_LIST;

  const entries = useMemo(() => {
    const byModel = new Map<string, PricedModel>();
    for (const p of providers) for (const m of p.models) byModel.set(m.model, m);
    return byModel;
  }, [providers]);

  const groups = useMemo(() => {
    if (!long) return [];
    const needle = query.trim().toLowerCase();
    const chosen = typeof value === "string" ? value : "";
    const grouped = new Map<string, string[]>();
    for (const model of models) {
      const entry = entries.get(model);
      const matches =
        !needle || model.toLowerCase().includes(needle) || (entry?.name ?? "").toLowerCase().includes(needle);
      if (!matches && model !== chosen) continue;
      const provider = providerForModel(model, providers);
      const label = !provider
        ? "Other"
        : provider.name === "openrouter"
          ? `${providerLabel("openrouter")} · ${makerOf(entry, model)}`
          : providerLabel(provider.name);
      grouped.set(label, [...(grouped.get(label) ?? []), model]);
    }
    return [...grouped.entries()];
  }, [long, query, value, models, entries, providers]);

  const option = (model: string, priced: boolean) => {
    const pricing = entries.get(model)?.pricing_usd_per_mtok;
    const text = labelOf ? labelOf(model) : model;
    const suffix = priced && pricing ? ` · ${price(pricing.input)} / ${price(pricing.output)}` : "";
    return (
      <option key={model} value={model} disabled={disabledOf?.(model)}>
        {text}
        {suffix}
      </option>
    );
  };

  if (!long) {
    return (
      <Select value={value} {...selectProps}>
        {leading}
        {models.map((m) => option(m, false))}
        {trailing}
      </Select>
    );
  }

  const shown = groups.reduce((n, [, ms]) => n + ms.length, 0);
  return (
    <div className="space-y-1">
      <Input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={`${filterLabel} (${models.length})`}
        aria-label={filterLabel}
        className="h-8 text-xs"
      />
      <Select value={value} {...selectProps}>
        {leading}
        {groups.map(([label, ms]) => (
          <optgroup key={label} label={label}>
            {ms.map((m) => option(m, true))}
          </optgroup>
        ))}
        {trailing}
      </Select>
      <p className="text-xs text-fg-muted">
        {query.trim() ? `${shown} of ${models.length} models` : `${models.length} models`} · prices per million tokens,
        input / output
      </p>
    </div>
  );
}
