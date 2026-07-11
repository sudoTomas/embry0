import { create } from "zustand";
import { persist } from "zustand/middleware";

interface LayoutState {
  sidebarOpen: boolean;
  densityMode: "comfortable" | "standard" | "compact";
  toggleSidebar: () => void;
  setDensityMode: (mode: "comfortable" | "standard" | "compact") => void;
}

export const useLayoutStore = create<LayoutState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      densityMode: "standard",
      toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
      setDensityMode: (densityMode) => set({ densityMode }),
    }),
    { name: "embry0-layout" }
  )
);
