import { useEffect, useRef, type ReactNode } from "react";
import { useLocation } from "react-router";
import { ChevronRight } from "lucide-react";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { RISK_LABELS, SETTING_GUIDE, type SettingKey, type SettingRisk } from "@/lib/settingGuide";

const RISK_TONES: Record<SettingRisk, BadgeTone> = {
  safe: "success",
  check: "warning",
  admin: "neutral",
};

/**
 * One technical setting under the repo page's Advanced settings. A native
 * <details>, closed by default, so its controls stay in the DOM but out of
 * the way. The summary says what the setting does and how careful to be; the
 * body opens with when a change takes effect and how to undo it. A link to
 * #advanced-{setting} opens it.
 */
export function AdvancedSection({ setting, children }: { setting: SettingKey; children: ReactNode }) {
  const guide = SETTING_GUIDE[setting];
  const id = `advanced-${setting}`;
  const { hash } = useLocation();
  const ref = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    const details = ref.current;
    if (!details || hash !== `#${id}`) return;
    // The address lands on this section already open, without the opening
    // ease: while the body grows from nothing the page is too short to
    // scroll to it. A click later still eases (index.css, data-instant).
    details.setAttribute("data-instant", "");
    details.open = true;
    // The page renders after its data loads, so the browser's own jump to
    // the address's #fragment found nothing to scroll to.
    if (typeof details.scrollIntoView === "function") details.scrollIntoView({ block: "start" });
    requestAnimationFrame(() => details.removeAttribute("data-instant"));
  }, [hash, id]);

  return (
    <details ref={ref} id={id} data-testid={id} className="advanced-section group rounded-lg border border-border">
      <summary className="flex cursor-pointer list-none gap-3 px-4 py-3 [&::-webkit-details-marker]:hidden">
        <ChevronRight
          className="mt-0.5 h-4 w-4 shrink-0 text-fg-muted group-open:rotate-90 motion-safe:transition-transform motion-safe:duration-(--motion-base)"
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1 space-y-0.5">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-fg">{guide.title}</h3>
            <Badge tone={RISK_TONES[guide.risk]}>{RISK_LABELS[guide.risk]}</Badge>
          </div>
          <p className="text-sm text-fg-secondary">{guide.what}</p>
        </div>
      </summary>
      <div className="space-y-3 border-t border-border px-4 py-3">
        <div className="space-y-0.5 text-sm text-fg-secondary">
          <p>
            <span className="font-medium text-fg">Takes effect:</span> {guide.takesEffect}
          </p>
          <p>
            <span className="font-medium text-fg">Undo:</span> {guide.undo}
          </p>
          {guide.note && <p>{guide.note}</p>}
        </div>
        {children}
      </div>
    </details>
  );
}
