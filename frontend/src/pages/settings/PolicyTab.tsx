import { useCallback, useRef } from "react";
import { useSearchParams } from "react-router";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { EffectiveSettingsView } from "@/components/settings/EffectiveSettingsView";
import { LayerPicker } from "@/components/settings/LayerPicker";
import { SettingsDocumentEditor } from "@/components/settings/SettingsDocumentEditor";
import { SettingsHistory } from "@/components/settings/SettingsHistory";
import { documentName, layerFromParams, layerKey, withLayer, type PolicyLayer } from "@/lib/settingsLayer";

/** What the tab is for, per layer. */
function intro(layer: PolicyLayer): string {
  if (layer.layer === "deployment") {
    return "Operator policy for this deployment, one YAML document with a section per feature. A key you leave out takes its default. A saved change applies on the next tick of each loop that reads it, with no restart.";
  }
  return `Operator policy ${
    layer.layer === "organization" ? "for one organization" : "for one repository"
  }: a document of the same shape that holds only the keys it sets. A key it leaves out is inherited, and a key it sets replaces the inherited value whole. A saved change applies on the next tick of each loop that reads it, with no restart.`;
}

/**
 * Operator policy: one settings document at a time, the deployment's or an
 * organization's or a repository's layer, picked above the cards and kept
 * in the URL (`?tab=policy&owner=acme`, `&repo=acme/widgets`). Below it the
 * document, the value of every key in effect as that layer reads it, and the
 * document's history with undo. Each card is mounted per layer, so nothing
 * of one layer's edit leaks into the next; leaving an open edit asks first.
 */
export default function PolicyTab() {
  const [params, setParams] = useSearchParams();
  const layer = layerFromParams(params);
  const key = layerKey(layer);
  const dirty = useRef(false);
  const onDirtyChange = useCallback((value: boolean) => {
    dirty.current = value;
  }, []);

  function pick(next: PolicyLayer) {
    if (layerKey(next) === key) return;
    if (dirty.current && !window.confirm(`Leave your unsaved edit of ${documentName(layer)}? It is not kept.`)) return;
    dirty.current = false;
    setParams(withLayer(params, next), { replace: true });
  }

  return (
    <div className="space-y-6">
      <LayerPicker layer={layer} onPick={pick} />
      <p className="text-sm text-fg-secondary">{intro(layer)}</p>
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Settings document</CardTitle>
        </CardHeader>
        <CardContent>
          <SettingsDocumentEditor key={key} layer={layer} onDirtyChange={onDirtyChange} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Values in effect</CardTitle>
        </CardHeader>
        <CardContent>
          <EffectiveSettingsView key={key} layer={layer} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">History</CardTitle>
        </CardHeader>
        <CardContent>
          <SettingsHistory key={key} layer={layer} />
        </CardContent>
      </Card>
    </div>
  );
}
