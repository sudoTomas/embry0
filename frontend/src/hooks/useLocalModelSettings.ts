import { useMemo } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { setSettingsKeys } from "@/api/settings";
import { SETTINGS_KEY, useEffectiveSettings } from "@/hooks/useSettingsDocument";
import type { EffectiveSettings } from "@/lib/types/settings";

/** The section of the settings document the limits of the operator's own model servers live in (#174). */
export const LOCAL_MODELS_SECTION = "local_models";

/** How many calls each model server takes at once, as every run reads it now; undefined until read, or without the
 * section (an older backend). */
export function localMaxRuns(effective: EffectiveSettings | undefined): number | undefined {
  const section = effective?.sections.find((s) => s.name === LOCAL_MODELS_SECTION);
  const value = section?.keys.find((key) => key.key === "max_runs")?.value;
  return typeof value === "number" ? value : undefined;
}

/** The deployment's `local_models.max_runs`, from `GET /config/settings/effective`. */
export function useLocalMaxRuns() {
  const query = useEffectiveSettings();
  const data = useMemo(() => localMaxRuns(query.data), [query.data]);
  return { data, isLoading: query.isLoading, isError: query.isError };
}

/**
 * Change how many calls each model server takes at once: `PATCH /config/settings/local_models`, recorded as a
 * revision of the settings document. Admin only; a 422 names the refused key.
 */
export function useSetLocalMaxRuns() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (maxRuns: number) => setSettingsKeys({ section: LOCAL_MODELS_SECTION, values: { max_runs: maxRuns } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: SETTINGS_KEY }),
  });
}
