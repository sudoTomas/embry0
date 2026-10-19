import { useQuery } from "@tanstack/react-query";
import { fetchTools } from "@/api/tools";

export function useTools() {
  return useQuery({ queryKey: ["tools"], queryFn: fetchTools, staleTime: 5 * 60_000 });
}
