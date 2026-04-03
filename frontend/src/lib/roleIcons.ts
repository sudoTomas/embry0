import { Code, ShieldCheck, Compass, Layers } from "lucide-react";

export const ROLE_ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  developer: Code,
  validator: ShieldCheck,
  explorer: Compass,
  triage: Layers,
};
