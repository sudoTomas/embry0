import { create } from "zustand";
import { persist } from "zustand/middleware";

interface LayoutState {
  /** Desktop: full column (true) or icon rail (false). Persisted. */
  sidebarOpen: boolean;
  /** Phone: the sidebar drawer is open over the content. Not persisted, so
   * a reload never opens on top of the page. */
  drawerOpen: boolean;
  densityMode: "comfortable" | "standard" | "compact";
  toggleSidebar: () => void;
  toggleDrawer: () => void;
  setDrawerOpen: (open: boolean) => void;
  setDensityMode: (mode: "comfortable" | "standard" | "compact") => void;
}

export const useLayoutStore = create<LayoutState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      drawerOpen: false,
      densityMode: "standard",
      toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
      toggleDrawer: () => set((s) => ({ drawerOpen: !s.drawerOpen })),
      setDrawerOpen: (drawerOpen) => set({ drawerOpen }),
      setDensityMode: (densityMode) => set({ densityMode }),
    }),
    {
      name: "embry0-layout",
      partialize: (s) => ({ sidebarOpen: s.sidebarOpen, densityMode: s.densityMode }),
    }
  )
);
