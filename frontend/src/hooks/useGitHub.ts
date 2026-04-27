import { useQuery } from "@tanstack/react-query";
import { fetchGitHubRepos } from "@/api/github";

export function useGitHubRepos() {
  return useQuery({
    queryKey: ["github", "repos"],
    queryFn: fetchGitHubRepos,
    staleTime: 5 * 60_000,
  });
}
