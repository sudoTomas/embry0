import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as skillsApi from "@/api/skills";
import type { SkillSource, SkillSourceCreate, SkillSourceUpdate } from "@/lib/types";

const SYNCING = new Set(["pending", "syncing"]);
export const anySourceSyncing = (rows: SkillSource[] | undefined) =>
  (rows ?? []).some((s) => SYNCING.has(s.status) || s.syncing === true);

export function useSkills() { return useQuery({ queryKey: ["skills"], queryFn: skillsApi.fetchSkills }); }
export function useSkillSources() {
  return useQuery({
    queryKey: ["skills", "sources"],
    queryFn: skillsApi.fetchSkillSources,
    refetchInterval: (query) => (anySourceSyncing(query.state.data) ? 5000 : false),
  });
}
export function useInlineSkills() { return useQuery({ queryKey: ["skills", "inline"], queryFn: skillsApi.fetchInlineSkills }); }

/** A mutation that refreshes the whole `["skills"]` tree (catalog, sources, inline) on success. */
function useInvalidating<TArg, TOut>(fn: (arg: TArg) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }) });
}
export function useCreateSkillSource() { return useInvalidating((body: SkillSourceCreate) => skillsApi.createSkillSource(body)); }
export function useUpdateSkillSource() {
  return useInvalidating(({ id, body }: { id: string; body: SkillSourceUpdate }) => skillsApi.updateSkillSource(id, body));
}
export function useSyncSkillSource() { return useInvalidating((id: string) => skillsApi.syncSkillSource(id)); }
export function useDeleteSkillSource() { return useInvalidating((id: string) => skillsApi.deleteSkillSource(id)); }
export function usePutInlineSkill() {
  return useInvalidating(({ name, body }: { name: string; body: { description: string; body: string } }) => skillsApi.putInlineSkill(name, body));
}
export function useDeleteInlineSkill() { return useInvalidating((name: string) => skillsApi.deleteInlineSkill(name)); }
