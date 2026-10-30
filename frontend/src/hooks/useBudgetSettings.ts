import { useMemo } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { setSettingsKeys } from "@/api/settings";
import { SETTINGS_KEY, useEffectiveSettings } from "@/hooks/useSettingsDocument";
import type { BudgetLimits } from "@/lib/types/config";
import type { EffectiveSetting, EffectiveSettings } from "@/lib/types/settings";

/** The section the budget lives in, in the settings document (#171). */
export const BUDGET_SECTION = "budget";

const LIMITS: readonly (keyof BudgetLimits)[] = [
  "max_budget_per_job_usd",
  "daily_cap_usd",
  "monthly_cap_usd",
  "rate_limit_per_author_per_hour",
  "overrun_mode",
];

/** The fleet limits in effect, from `GET /config/settings/effective`; undefined until it is read, or without the section. */
export function budgetLimits(effective: EffectiveSettings | undefined): BudgetLimits | undefined {
  const section = effective?.sections.find((s) => s.name === BUDGET_SECTION);
  if (!section) return undefined;
  const values = Object.fromEntries(section.keys.map((key) => [key.key, key.value]));
  if (!LIMITS.every((key) => key in values)) return undefined;
  return Object.fromEntries(LIMITS.map((key) => [key, values[key]])) as unknown as BudgetLimits;
}

/** Where each limit's value comes from (its default, the import at upgrade, or a person's change). */
export type BudgetProvenance = Partial<Record<keyof BudgetLimits, EffectiveSetting>>;

/** Each fleet limit's effective setting, for saying where its value comes from. */
export function budgetProvenance(effective: EffectiveSettings | undefined): BudgetProvenance {
  const section = effective?.sections.find((s) => s.name === BUDGET_SECTION);
  const out: BudgetProvenance = {};
  for (const key of section?.keys ?? []) {
    if ((LIMITS as readonly string[]).includes(key.key)) out[key.key as keyof BudgetLimits] = key;
  }
  return out;
}

/** The deployment's budget limits as every job reads them now, and where each one comes from. */
export function useBudgetSettings() {
  const query = useEffectiveSettings();
  const data = useMemo(() => budgetLimits(query.data), [query.data]);
  const provenance = useMemo(() => budgetProvenance(query.data), [query.data]);
  return { data, provenance, isLoading: query.isLoading, isError: query.isError, refetch: query.refetch };
}

/**
 * Change some budget limits: `PATCH /config/settings/budget`, recorded as a
 * revision of the settings document. Admin only; a 422 names each refused key.
 */
export function useSetBudgetLimits() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (values: Partial<BudgetLimits>) => setSettingsKeys({ section: BUDGET_SECTION, values }),
    onSuccess: () => qc.invalidateQueries({ queryKey: SETTINGS_KEY }),
  });
}
