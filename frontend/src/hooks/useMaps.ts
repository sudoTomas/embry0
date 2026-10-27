import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchMap,
  fetchMaps,
  fetchMarks,
  fetchPart,
  fetchWork,
  markPart,
  sendParts,
  type SendKind,
  type Verdict,
} from "@/api/maps";
import type { RunWith } from "@/api/runWith";

const KEY = ["maps"];

export function useMaps() {
  return useQuery({ queryKey: KEY, queryFn: fetchMaps, refetchInterval: 300_000 });
}

/** The whole map: big, changes once an hour, so it is fetched once and kept. */
export function useMap(slug: string | undefined) {
  return useQuery({
    queryKey: [...KEY, slug, "graph"],
    queryFn: () => fetchMap(slug as string),
    enabled: !!slug,
    staleTime: 600_000,
  });
}

export function usePart(slug: string | undefined, nodeId: string | null) {
  return useQuery({
    queryKey: [...KEY, slug, "part", nodeId],
    queryFn: () => fetchPart(slug as string, nodeId as string),
    enabled: !!slug && !!nodeId,
  });
}

export function useMapMarks(slug: string | undefined, by: string) {
  return useQuery({
    queryKey: [...KEY, slug, "marks", by],
    queryFn: () => fetchMarks(slug as string, by),
    enabled: !!slug,
    refetchInterval: 60_000,
  });
}

/** Each part's newest send and its job; polled, so a running job turns into its pull request. */
export function useMapWork(slug: string | undefined) {
  return useQuery({
    queryKey: [...KEY, slug, "work"],
    queryFn: () => fetchWork(slug as string),
    enabled: !!slug,
    refetchInterval: 20_000,
  });
}

export function useMarkPart(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { node_id: string; verdict: Verdict | "clear"; note?: string; by: string }) => markPart(slug, body),
    onSuccess: (_, body) => {
      qc.invalidateQueries({ queryKey: [...KEY, slug, "marks"] });
      qc.invalidateQueries({ queryKey: [...KEY, slug, "part", body.node_id] });
    },
  });
}

export function useSendParts(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { node_ids: string[]; kind?: SendKind; note?: string; by: string; run_with?: RunWith }) =>
      sendParts(slug, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...KEY, slug, "work"] });
      qc.invalidateQueries({ queryKey: [...KEY, slug, "marks"] });
      qc.invalidateQueries({ queryKey: [...KEY, slug, "part"] });
      qc.invalidateQueries({ queryKey: ["owner-proposals"] });
    },
  });
}
