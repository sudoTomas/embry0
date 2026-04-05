import { create } from "zustand";
import { persist } from "zustand/middleware";

interface IssuesState {
  viewMode: "list" | "board";
  setViewMode: (mode: "list" | "board") => void;
}

export const useIssuesStore = create<IssuesState>()(
  persist(
    (set) => ({
      viewMode: "list",
      setViewMode: (viewMode) => set({ viewMode }),
    }),
    { name: "legion-issues-view" },
  ),
);
