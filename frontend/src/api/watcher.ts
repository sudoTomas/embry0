import { api } from "./client";
import type { WatcherRun, WatcherStackList, WatcherStackSaved, WatcherStackWrite, WatcherTickResult } from "@/lib/types";

export async function fetchWatcherRuns(limit = 10): Promise<WatcherRun[]> {
  const { data } = await api.get<WatcherRun[]>("/watcher/runs", { params: { limit } });
  return data;
}

/**
 * Run one watcher tick synchronously. A tick that finds signal spawns an
 * analysis job inline, so this can take minutes — hence the long timeout
 * (the client default is 30 s).
 */
export async function triggerWatcherRun(): Promise<WatcherTickResult> {
  const { data } = await api.post<WatcherTickResult>("/watcher/run", undefined, {
    timeout: 300_000,
  });
  return data;
}

export async function fetchWatcherStacks(): Promise<WatcherStackList> {
  const { data } = await api.get<WatcherStackList>("/watcher/stacks");
  return data;
}

function stackPath(repo: string): string {
  const [owner, ...rest] = repo.split("/");
  return `/watcher/stacks/${encodeURIComponent(owner)}/${encodeURIComponent(rest.join("/"))}`;
}

/** Create or replace a watched stack (admin). A refusal (403, 422) is shown in the editor. */
export async function putWatcherStack(repo: string, body: WatcherStackWrite): Promise<WatcherStackSaved> {
  const { data } = await api.put<WatcherStackSaved>(stackPath(repo), body, { suppressValidationToast: true });
  return data;
}

/** Stop watching a stack; its runs, signatures and cases stay. */
export async function deleteWatcherStack(repo: string): Promise<void> {
  await api.delete(stackPath(repo));
}
