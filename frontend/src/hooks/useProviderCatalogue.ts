import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { isConflict } from "@/api/errorDetail";
import {
  fetchProviderCatalogue,
  previewProviderCatalogue,
  saveProviderCatalogue,
} from "@/api/providerCatalogue";

/** The provider catalogue's document: the overlay, the catalogue in force and the one embry0 ships. */
export const PROVIDER_CATALOGUE_KEY = ["config", "providers", "catalog", "document"] as const;

/** A write changes what every view of the providers shows: the document, the models catalog, the tiers' options. */
function refresh(qc: QueryClient) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: PROVIDER_CATALOGUE_KEY }),
    qc.invalidateQueries({ queryKey: ["config"] }),
  ]);
}

export function useProviderCatalogue() {
  return useQuery({ queryKey: PROVIDER_CATALOGUE_KEY, queryFn: fetchProviderCatalogue });
}

/** The dry run: it stores nothing, so only a 409 (the catalogue moved since the edit started) refreshes the views. */
export function usePreviewProviderCatalogue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ text, generation }: { text: string; generation: number }) =>
      previewProviderCatalogue(text, generation),
    onError: (error) => {
      if (isConflict(error)) void refresh(qc);
    },
  });
}

export function useSaveProviderCatalogue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ text, generation, reason }: { text: string; generation: number; reason?: string }) =>
      saveProviderCatalogue(text, generation, reason),
    onSettled: () => refresh(qc),
  });
}
