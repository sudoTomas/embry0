import { SlidersHorizontal } from "lucide-react";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useEffectiveSettings } from "@/hooks/useSettingsDocument";
import { actorInText } from "@/lib/actors";
import { SOURCE_LABELS, formatSettingValue, unknownParts } from "@/lib/settingsDocument";
import { DEPLOYMENT_LAYER, LAYER_NOUNS, layerName, type PolicyLayer } from "@/lib/settingsLayer";
import type { EffectiveLayerEntry, EffectiveSection, EffectiveSetting, SettingSource } from "@/lib/types/settings";
import { cn, formatDateTime } from "@/lib/utils";

const SOURCE_TONES: Record<SettingSource, BadgeTone> = {
  default: "neutral",
  document: "info",
  bootstrap: "purple",
  organization: "success",
  repository: "success",
};

const LAYERS: Record<string, string> = { organization: "an organization", repository: "a repository" };

/** Where the value comes from, in the deployment's view: the default, its document, or the import. */
function sourceLabel(setting: EffectiveSetting): string {
  if (setting.source === "bootstrap" && setting.env) return `Imported from ${setting.env}`;
  return SOURCE_LABELS[setting.source] ?? setting.source;
}

/** Where the value comes from, in a layer's view: the layer that sets it, by name, or the deployment. */
function layerSourceLabel(setting: EffectiveSetting): string {
  if (setting.source === "organization" || setting.source === "repository") {
    const supplier = setting.layers?.find((entry) => entry.layer === setting.source);
    return supplier ? `Set by ${layerName(supplier)}` : SOURCE_LABELS[setting.source];
  }
  if (setting.source === "document") return "Set by the deployment";
  return sourceLabel(setting);
}

/**
 * Who set it and when. The API hides another person's name from anyone but
 * an admin (`set_by` null with `set_at` kept), which reads as "someone
 * else"; with neither, the change is older than the kept history.
 */
function setBy(setting: EffectiveSetting): string | null {
  if (setting.source === "default" || (!setting.set_by && !setting.set_at)) return null;
  return `set by ${actorInText(setting.set_by)}${setting.set_at ? ` on ${formatDateTime(setting.set_at)}` : ""}`;
}

/**
 * The small print under a key: its default, who set it, the variable it
 * replaces, who can override it. A layer's view says the last two in its
 * own words, so it keeps the first two.
 */
function details(setting: EffectiveSetting, inLayer = false): string {
  const parts = [`Default ${formatSettingValue(setting.default)}`];
  const who = setBy(setting);
  if (who) parts.push(who);
  if (inLayer) return parts.join(" · ");
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

/**
 * Where a key's value comes from, seen from the layer viewed: what its own
 * value overrides, what it inherits and from whom, or that it may not set
 * the key at all and who does. `entries` are the documents in the view, most
 * general first; the last is the layer viewed.
 */
function provenance(setting: EffectiveSetting, view: Exclude<PolicyLayer, { layer: "deployment" }>): {
  settable: boolean;
  text: string;
} {
  const entries = setting.layers ?? [];
  const own: EffectiveLayerEntry | undefined = entries[entries.length - 1];
  const settable = own?.settable ?? setting.overridable.includes(view.layer);
  if (!settable) {
    const perOrganization = view.layer === "repository" && setting.overridable.includes("organization");
    return {
      settable,
      text: `${LAYER_NOUNS[view.layer]} cannot set this key: ${perOrganization ? "it is set per organization" : "it is the deployment's"}.`,
    };
  }
  // The most specific document under the layer viewed that supplies a value; none means the default.
  const supplier = [...entries.slice(0, -1)].reverse().find((entry) => entry.set);
  const whence = supplier ? (supplier.layer === "deployment" ? "the deployment" : layerName(supplier)) : null;
  if (own?.set) {
    const inherited = formatSettingValue(setting.inherited);
    return { settable, text: whence ? `Overrides ${inherited} from ${whence}.` : `Overrides the default ${inherited}.` };
  }
  return { settable, text: whence ? `Inherited from ${whence}.` : "The default: nothing sets it." };
}

/** Why a document's stored value is not read, in the view of a layer. */
function refusalText(entry: EffectiveLayerEntry): string {
  if (entry.layer === "deployment") return `The deployment's stored value is refused, so the default is read: ${entry.refused}`;
  return `The value ${layerName(entry)} stores is refused, so the inherited value is read: ${entry.refused}`;
}

function LayerSettingRow({ setting, view }: { setting: EffectiveSetting; view: Exclude<PolicyLayer, { layer: "deployment" }> }) {
  const { settable, text } = provenance(setting, view);
  const refused = (setting.layers ?? []).filter((entry) => entry.refused);
  return (
    <li
      className={cn("space-y-1 px-3 py-2", !settable && "opacity-60")}
      data-testid="effective-key"
      data-settable={settable ? "true" : "false"}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <code className="min-w-0 font-mono text-sm text-fg [overflow-wrap:anywhere]">{setting.key}</code>
        <span className="min-w-0 font-mono text-sm text-fg [overflow-wrap:anywhere]" data-testid="effective-value">
          {formatSettingValue(setting.value)}
        </span>
        {/* A badge never wraps; a repository's name can be longer than a phone is wide, so these words may. */}
        <Badge tone={SOURCE_TONES[setting.source] ?? "neutral"} className="max-w-full">
          <span className="min-w-0 whitespace-normal [overflow-wrap:anywhere]" data-testid="effective-source">
            {layerSourceLabel(setting)}
          </span>
        </Badge>
        {!settable && <Badge tone="neutral">{`Not settable by ${LAYER_NOUNS[view.layer].toLowerCase()}`}</Badge>}
      </div>
      <p className="text-xs text-fg-secondary [overflow-wrap:anywhere]" data-testid="effective-provenance">
        {text}
      </p>
      {setting.description && <p className="text-xs text-fg-secondary">{setting.description}</p>}
      <p className="text-xs text-fg-muted [overflow-wrap:anywhere]">{details(setting, true)}</p>
      {refused.map((entry) => (
        <p key={entry.layer} className="text-xs text-warning [overflow-wrap:anywhere]" data-testid="effective-refused">
          {refusalText(entry)}
        </p>
      ))}
    </li>
  );
}

function SectionValues({ section, view }: { section: EffectiveSection; view: PolicyLayer }) {
  return (
    <section className="min-w-0 space-y-2" data-testid="effective-section">
      <div>
        <h3 className="font-mono text-sm font-semibold text-fg">{section.name}</h3>
        {section.summary && <p className="text-xs text-fg-muted">{section.summary}</p>}
      </div>
      <ul className="divide-y divide-border rounded-lg border border-border">
        {section.keys.map((setting) =>
          view.layer === "deployment" ? (
            <SettingRow key={setting.key} setting={setting} />
          ) : (
            <LayerSettingRow key={setting.key} setting={setting} view={view} />
          ),
        )}
      </ul>
    </section>
  );
}

/**
 * The value every loop reads now, key by key, with its default and where
 * it came from: the default, the document, or the one-time import from an
 * environment variable. For an organization's or a repository's layer, as
 * that organization or repository reads it: each value with the layer it
 * comes from, what the layer's own value overrides, and the keys the layer
 * may not set, dimmed. Read only; the document above is what changes it.
 */
export function EffectiveSettingsView({ layer = DEPLOYMENT_LAYER }: { layer?: PolicyLayer }) {
  const effective = useEffectiveSettings(layer);

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
      {layer.layer !== "deployment" && sections.length > 0 && (
        <p className="text-sm text-fg-secondary [overflow-wrap:anywhere]" data-testid="effective-layer-intro">
          {`As ${layerName(layer)} reads them: each value, the layer it comes from, and what ${layerName(layer)}'s own value overrides.`}
        </p>
      )}
      {sections.length === 0 ? (
        <EmptyState
          icon={SlidersHorizontal}
          title="No settings sections yet"
          description="No feature keeps its policy in the settings document yet. Each one that moves in lists its keys here, with the value in effect and where it came from."
          className="py-8"
        />
      ) : (
        sections.map((section) => <SectionValues key={section.name} section={section} view={layer} />)
      )}
      {unknown && (
        <p className="text-sm text-warning [overflow-wrap:anywhere]" data-testid="effective-unknown">
          {`Stored but not read by this release: ${unknown}. A newer release wrote them; they are kept while the document leaves them as they are, and changing them is refused.`}
        </p>
      )}
    </div>
  );
}
