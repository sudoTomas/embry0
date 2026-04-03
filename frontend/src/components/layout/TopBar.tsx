import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useLayoutStore } from "@/stores/layoutStore";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { APP_NAME } from "@/lib/branding";

export function TopBar() {
  const { sidebarOpen, toggleSidebar, densityMode, setDensityMode } = useLayoutStore();

  return (
    <header
      className="flex h-14 items-center gap-4 border-b border-border px-4"
      style={{ background: 'linear-gradient(180deg, rgba(15,17,23,0.9) 0%, rgba(9,9,11,0.9) 100%)' }}
    >
      <Button variant="ghost" size="icon" onClick={toggleSidebar} aria-label={sidebarOpen ? "Close sidebar" : "Open sidebar"}>
        {sidebarOpen ? (
          <PanelLeftClose className="h-4 w-4" />
        ) : (
          <PanelLeftOpen className="h-4 w-4" />
        )}
      </Button>
      <span className="text-sm font-medium text-muted-foreground">{APP_NAME}</span>
      <Select
        value={densityMode}
        onChange={(e) => setDensityMode(e.target.value as "comfortable" | "standard" | "compact")}
        className="h-7 w-auto ml-auto text-xs"
        aria-label="Display density"
      >
        <option value="comfortable">Comfortable</option>
        <option value="standard">Standard</option>
        <option value="compact">Compact</option>
      </Select>
    </header>
  );
}
