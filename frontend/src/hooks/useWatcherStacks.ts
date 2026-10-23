import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { deleteWatcherStack, fetchWatcherStacks, putWatcherStack } from "@/api/watcher";
import type { WatcherStackWrite } from "@/lib/types";

export function useWatcherStacks() {
  return useQuery({ queryKey: ["watcher-stacks"], queryFn: fetchWatcherStacks });
}

export function useSaveWatcherStack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ repo, body }: { repo: string; body: WatcherStackWrite }) => putWatcherStack(repo, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watcher-stacks"] }),
  });
}

export function useDeleteWatcherStack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (repo: string) => deleteWatcherStack(repo),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watcher-stacks"] }),
  });
}
