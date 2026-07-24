import { useQuery } from "@tanstack/react-query";
import { fetchDeliverables } from "@/api/deliverables";

export function useDeliverables(jobId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ["deliverables", jobId],
    queryFn: () => fetchDeliverables(jobId!),
    enabled: !!jobId && enabled,
  });
}
