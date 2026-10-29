import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchMap,
  fetchMaps,
  fetchMarks,
  fetchPart,
  fetchWork,
  markPart,
  sendParts,
  setMapOwner,
  type MapOwner,
  type SendKind,
  type Verdict,
} from "@/api/maps";
import type { RunWith } from "@/api/runWith";

const KEY = ["maps"];

/** One map's cache key: its slug and the owner the page names it by ("*": the one map of the slug, unnamed). */
function mapKey(slug: string | undefined, owner: MapOwner) {
  return [...KEY, slug, owner ?? "*"];
}

export function useMaps() {
  return useQuery({ queryKey: KEY, queryFn: fetchMaps, refetchInterval: 300_000 });
}

/** The whole map: big, changes once an hour, so it is fetched once and kept. */
export function useMap(slug: string | undefined, owner?: MapOwner) {
  return useQuery({
    queryKey: [...mapKey(slug, owner), "graph"],
    queryFn: () => fetchMap(slug as string, owner),
    enabled: !!slug,
    staleTime: 600_000,
  });
}

export function usePart(slug: string | undefined, nodeId: string | null, owner?: MapOwner) {
  return useQuery({
    queryKey: [...mapKey(slug, owner), "part", nodeId],
    queryFn: () => fetchPart(slug as string, nodeId as string, owner),
    enabled: !!slug && !!nodeId,
  });
}

export function useMapMarks(slug: string | undefined, by: string, owner?: MapOwner) {
  return useQuery({
    queryKey: [...mapKey(slug, owner), "marks", by],
    queryFn: () => fetchMarks(slug as string, by, owner),
    enabled: !!slug,
    refetchInterval: 60_000,
  });
}

/** Each part's newest send and its job; polled, so a running job turns into its pull request. */
export function useMapWork(slug: string | undefined, owner?: MapOwner) {
  return useQuery({
    queryKey: [...mapKey(slug, owner), "work"],
    queryFn: () => fetchWork(slug as string, owner),
    enabled: !!slug,
    refetchInterval: 20_000,
  });
}

export function useMarkPart(slug: string, owner?: MapOwner) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { node_id: string; verdict: Verdict | "clear"; note?: string; by: string }) =>
      markPart(slug, body, owner),
    onSuccess: (_, body) => {
      qc.invalidateQueries({ queryKey: [...mapKey(slug, owner), "marks"] });
      qc.invalidateQueries({ queryKey: [...mapKey(slug, owner), "part", body.node_id] });
    },
  });
}

export function useSendParts(slug: string, owner?: MapOwner) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      node_ids: string[];
      kind?: SendKind;
      note?: string;
      by: string;
      run_with?: RunWith;
      together?: boolean;
    }) =>
      sendParts(slug, body, owner),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...mapKey(slug, owner), "work"] });
      qc.invalidateQueries({ queryKey: [...mapKey(slug, owner), "marks"] });
      qc.invalidateQueries({ queryKey: [...mapKey(slug, owner), "part"] });
      qc.invalidateQueries({ queryKey: ["owner-proposals"] });
    },
  });
}

/** Admin: give the map to another owner (or to none); every cached view of the map is stale after it. */
export function useSetMapOwner(slug: string, owner?: MapOwner) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (to: string | null) => setMapOwner(slug, owner, to),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
