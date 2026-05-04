import { Outlet } from "react-router";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { useLayoutStore } from "@/stores/layoutStore";

export function AppLayout() {
  const densityMode = useLayoutStore((s) => s.densityMode);
  return (
    <div className="flex h-screen bg-background text-foreground" data-density={densityMode}>
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar />
        <main className="flex-1 overflow-auto p-[var(--density-padding)]">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
