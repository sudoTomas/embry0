import { useMemo } from "react";
import { useEffectiveSettings } from "@/hooks/useSettingsDocument";

/** The settings document's section of the models background features run on (#164). */
export const BACKGROUND_MODELS_SECTION = "background_models";

/**
 * Every model id the deployment's `background_models` section names: its
 * default and each feature's own. A model of the operator's own server lists
 * no catalogue, so a list of models (the fallback order) offers these too.
 */
export function useBackgroundModelIds(): string[] {
  const effective = useEffectiveSettings();
  return useMemo(() => {
    const section = effective.data?.sections.find((s) => s.name === BACKGROUND_MODELS_SECTION);
    const named = (section?.keys ?? []).map((k) => k.value);
    return [...new Set(named.filter((v): v is string => typeof v === "string" && v !== ""))];
  }, [effective.data]);
}
