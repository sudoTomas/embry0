import { SlidersHorizontal } from "lucide-react";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useEffectiveSettings } from "@/hooks/useSettingsDocument";
import { SOURCE_LABELS, formatSettingValue, unknownParts } from "@/lib/settingsDocument";
import type { EffectiveSection, EffectiveSetting, SettingSource } from "@/lib/types/settings";
import { formatDateTime } from "@/lib/utils";

const SOURCE_TONES: Record<SettingSource, BadgeTone> = {
  default: "neutral",
  document: "info",
  bootstrap: "purple",
};

const LAYERS: Record<string, string> = { organization: "an organization", repository: "a repository" };

function sourceLabel(setting: EffectiveSetting): string {
  if (setting.source === "bootstrap" && setting.env) return `Imported from ${setting.env}`;
  return SOURCE_LABELS[setting.source] ?? setting.source;
}

/** The small print under a key: its default, who set it, the variable it replaces, who can override it. */
function details(setting: EffectiveSetting): string {
  const parts = [`Default ${formatSettingValue(setting.default)}`];
  if (setting.source !== "default" && setting.set_by) {
    parts.push(`set by ${setting.set_by}${setting.set_at ? ` on ${formatDateTime(setting.set_at)}` : ""}`);
  }
  if (setting.env && setting.source !== "bootstrap") parts.push(`replaces ${setting.env}`);
  if (setting.overridable.length > 0) {
    parts.push(`${setting.overridable.map((layer) => LAYERS[layer] ?? layer).join(" or ")} can override it`);
  }
  return parts.join(" · ");
}

function SettingRow({ setting }: { setting: EffectiveSetting }) {
  return (
    <li className="space-y-1 px-3 py-2" data-testid="effective-key">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <code className="min-w-0 font-mono text-sm text-fg [overflow-wrap:anywhere]">{setting.key}</code>
        <span className="min-w-0 font-mono text-sm text-fg [overflow-wrap:anywhere]" data-testid="effective-value">
          {formatSettingValue(setting.value)}
        </span>
        <Badge tone={SOURCE_TONES[setting.source] ?? "neutral"}>{sourceLabel(setting)}</Badge>
      </div>
      {setting.description && <p className="text-xs text-fg-secondary">{setting.description}</p>}
      <p className="text-xs text-fg-muted [overflow-wrap:anywhere]">{details(setting)}</p>
      {setting.refused && (
        <p className="text-xs text-warning" data-testid="effective-refused">
          {`The stored value is refused, so the default is read: ${setting.refused}`}
        </p>
      )}
      {setting.env_still_set && setting.env && (
        <p className="text-xs text-warning" data-testid="effective-env-still-set">
          {`${setting.env} is still set in the environment. The document governs this key now and the variable is ignored, so it can be removed.`}
        </p>
      )}
    </li>
  );
}

function SectionValues({ section }: { section: EffectiveSection }) {
  return (
    <section className="min-w-0 space-y-2" data-testid="effective-section">
      <div>
        <h3 className="font-mono text-sm font-semibold text-fg">{section.name}</h3>
        {section.summary && <p className="text-xs text-fg-muted">{section.summary}</p>}
      </div>
      <ul className="divide-y divide-border rounded-lg border border-border">
        {section.keys.map((setting) => (
          <SettingRow key={setting.key} setting={setting} />
        ))}
      </ul>
    </section>
  );
}

/**
 * The value every loop reads now, key by key, with its default and where
 * it came from: the default, the document, or the one-time import from an
 * environment variable. Read only; the document above is what changes it.
 */
export function EffectiveSettingsView() {
  const effective = useEffectiveSettings();

  if (effective.isError) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm text-fg-secondary">Could not load the values in effect.</p>
        <Button variant="outline" size="sm" onClick={() => void effective.refetch()}>
          Retry
        </Button>
      </div>
    );
  }
  if (!effective.data) return <Skeleton className="h-24 w-full" />;
  const { sections } = effective.data;
  const unknown = unknownParts(effective.data.unknown_sections, effective.data.unknown_keys);

  return (
    <div className="space-y-4">
      {sections.length === 0 ? (
        <EmptyState
          icon={SlidersHorizontal}
          title="No settings sections yet"
          description="No feature keeps its policy in the settings document yet. Each one that moves in lists its keys here, with the value in effect and where it came from."
          className="py-8"
        />
      ) : (
        sections.map((section) => <SectionValues key={section.name} section={section} />)
      )}
      {unknown && (
        <p className="text-sm text-warning [overflow-wrap:anywhere]" data-testid="effective-unknown">
          {`Stored but not read by this release: ${unknown}. A newer release wrote them; they are kept while the document leaves them as they are, and changing them is refused.`}
        </p>
      )}
    </div>
  );
}
