import { Link } from "react-router";
import { useOrgContextAudit } from "@/hooks/useOrgContextAudit";

const daysText = (days: number) => `${days} ${days === 1 ? "day" : "days"}`;

/**
 * How often embry0 audits the files that steer a coding agent (CLAUDE.md,
 * AGENTS.md, skills, rules) in the owner's registered repositories, as the
 * organization reads it now: the settings document's `context_audit.days`,
 * its layer over the deployment's (#170). A line, not a field: the schedule
 * is changed with the organization's other context audit settings, on its
 * Settings tab, where each says where its value comes from.
 */
export function ContextAuditSetting({ owner }: { owner: string }) {
  const { days, settings } = useOrgContextAudit(owner);
  if (!settings) return null;
  return (
    <p className="text-sm text-fg-secondary" data-testid="org-context-audit-setting">
      {days === null ? "The context audit is off." : `Audits agent instructions every ${daysText(days)}.`}{" "}
      <Link to={{ search: "?tab=settings" }} className="text-fg hover:text-primary hover:underline">
        Context audit settings
      </Link>
    </p>
  );
}
