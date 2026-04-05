import { create } from "zustand";
import { persist } from "zustand/middleware";

interface RepoState {
  selectedRepo: string | null;
  recentRepos: string[];
  setSelectedRepo: (repo: string | null) => void;
}

export const useRepoStore = create<RepoState>()(
  persist(
    (set) => ({
      selectedRepo: null,
      recentRepos: [],
      setSelectedRepo: (repo) =>
        set((state) => ({
          selectedRepo: repo,
          recentRepos: repo
            ? [repo, ...state.recentRepos.filter((r) => r !== repo)].slice(0, 10)
            : state.recentRepos,
        })),
    }),
    { name: "legion-repo" }
  )
);
