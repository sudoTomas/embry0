import {
  Bot,
  Building2,
  CircleDot,
  Container,
  Cpu,
  Eye,
  FolderGit2,
  Gauge,
  GitMerge,
  KeyRound,
  ListChecks,
  ScrollText,
  Server,
  Settings,
  ShieldCheck,
  Sparkles,
  SquareTerminal,
  Variable,
  Workflow,
  Zap,
  UsersRound,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

/** The sidebar's sections and entries, shared with the breadcrumb line so a
 * top-level page can name its section instead of repeating its own title. */

// Nav is lucide-only by decision: the alchemical
// sigils that used to override some entries live on in the divine layer
// elsewhere (Agents, job detail, About), not in the nav rail.
export interface NavItem {
  path: string;
  label: string;
  icon: LucideIcon;
  accentColor?: string;
  /** Render only for admin-tier sessions (or API-key mode —
   *  the master key is admin-equivalent). Backend enforces regardless. */
  adminOnly?: boolean;
}

export interface NavGroup {
  label: string;
  items: readonly NavItem[];
}

// Unified IA:
// Overview, Work, Pipelines & QA, Infra, Settings — embry0-backend only.
export const NAV_GROUPS: readonly NavGroup[] = [
  {
    label: "Overview",
    items: [
      { path: "/", label: "Overview", icon: Gauge },
    ],
  },
  {
    label: "Work",
    items: [
      { path: "/console", label: "Console", icon: SquareTerminal },
      { path: "/issues", label: "Issues", icon: CircleDot },
      { path: "/jobs", label: "Jobs", icon: Zap },
      { path: "/merge-approvals", label: "Merge approvals", icon: GitMerge },
    ],
  },
  {
    label: "Pipelines & QA",
    items: [
      { path: "/pipelines", label: "Pipelines", icon: Workflow },
      // Central registry + token-discovered Add-repo flow.
      { path: "/repos", label: "Repos", icon: FolderGit2 },
      // ADR 0010: a forge owner maintained as a whole, next to the per-repo registry.
      { path: "/orgs", label: "Organizations", icon: Building2 },
      { path: "/qa/repos", label: "QA", icon: ShieldCheck },
    ],
  },
  {
    label: "Operations",
    items: [
      { path: "/watcher", label: "Watcher", icon: Eye },
      { path: "/logs", label: "Logs", icon: ScrollText },
      // Global audit log. adminOnly gates NAV only —
      // operators reaching /admin/audit by URL get their own rows (backend
      // enforces row scoping).
      { path: "/admin/audit", label: "Audit", icon: ListChecks, adminOnly: true },
    ],
  },
  {
    label: "Infra",
    items: [
      { path: "/sandboxes", label: "Sandboxes", icon: Container },
      { path: "/hosts", label: "Hosts", icon: Server },
      { path: "/agents", label: "Agents", icon: Bot },
      { path: "/skills", label: "Skills", icon: Sparkles, adminOnly: true },
      { path: "/environments", label: "Environments", icon: Variable },
      { path: "/credentials", label: "Credentials", icon: KeyRound },
    ],
  },
  {
    label: "Settings",
    items: [
      { path: "/settings", label: "Settings", icon: Settings },
      // The LLM provider catalog — availability, connection
      // tests, per-model enablement, tier assignment.
      { path: "/providers", label: "LLM providers", icon: Cpu },
      { path: "/admin/users", label: "Users", icon: UsersRound, adminOnly: true },
    ],
  },
];
