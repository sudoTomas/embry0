import { useQuery } from "@tanstack/react-query";
import { fetchQueue } from "@/api/queue";

export function useQueue() {
  return useQuery({
    queryKey: ["queue"],
    queryFn: fetchQueue,
    refetchInterval: 5_000,
  });
}
