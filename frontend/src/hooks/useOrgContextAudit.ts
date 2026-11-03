import { useMemo } from "react";
import {
  useEffectiveSettings,
  useSetSettingsKeys,
  useSettingsDocument,
  useSettingsWriteAccess,
} from "@/hooks/useSettingsDocument";
import {
  BUDGET_SECTION,
  CAP_FIELDS,
  CONTEXT_AUDIT_SECTION,
  ORG_CONTEXT_AUDIT_KEYS,
  auditDays,
  type OrgContextAuditKey,
} from "@/lib/contextAuditSettings";
import type { PolicyLayer } from "@/lib/settingsLayer";
import type { EffectiveSetting } from "@/lib/types/settings";

/** The organization's layer of the settings document. */
export function orgLayer(owner: string): PolicyLayer {
  return { layer: "organization", owner };
}

/**
 * An organization's `context_audit` keys as it reads them now
 * (`GET /config/settings/effective?owner=`), each with where its value comes
 * from, and the change of some of them (`PATCH /config/settings/context_audit?owner=`)
 * against the generation of its layer as read. Who may change them is the
 * server's answer for that layer: an admin, with a key whose allowlist does
 * not stop it.
 */
export function useOrgContextAudit(owner: string) {
  const layer = useMemo(() => orgLayer(owner), [owner]);
  const effective = useEffectiveSettings(layer);
  const document = useSettingsDocument(layer);
  const access = useSettingsWriteAccess(layer);
  const save = useSetSettingsKeys(layer);
  const settings = useMemo(() => {
    const section = effective.data?.sections.find((s) => s.name === CONTEXT_AUDIT_SECTION);
    const out: Partial<Record<OrgContextAuditKey, EffectiveSetting>> = {};
    for (const key of section?.keys ?? []) {
      if ((ORG_CONTEXT_AUDIT_KEYS as readonly string[]).includes(key.key)) out[key.key as OrgContextAuditKey] = key;
    }
    return section ? out : undefined;
  }, [effective.data]);
  const caps = useMemo(() => {
    const section = effective.data?.sections.find((s) => s.name === BUDGET_SECTION);
    const out: Partial<Record<string, EffectiveSetting>> = {};
    for (const key of section?.keys ?? []) {
      if (CAP_FIELDS.some((field) => field.key === key.key)) out[key.key] = key;
    }
    return section ? out : undefined;
  }, [effective.data]);
  return {
    layer,
    /** Each key the organization may set, as it reads it; undefined until read, or from a server without them. */
    settings,
    /** Its context audits' caps, keys of the `budget` section, as it reads them; undefined until read. */
    caps,
    /** The days between its repositories' scheduled audits as it reads them; null while off or unknown. */
    days: auditDays(settings?.days?.value),
    /** The generation of the organization's layer the change is made against; 0 for a layer nobody wrote. */
    generation: document.data?.generation,
    access,
    save,
    isLoading: effective.isLoading,
    isError: effective.isError,
    refetch: effective.refetch,
  };
}
