import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { EffectiveSettingsView } from "@/components/settings/EffectiveSettingsView";
import { SettingsDocumentEditor } from "@/components/settings/SettingsDocumentEditor";
import { SettingsHistory } from "@/components/settings/SettingsHistory";

/**
 * The deployment's operator policy: the settings document, the value of
 * every key in effect, and the document's history with undo.
 */
export default function PolicyTab() {
  return (
    <div className="space-y-6">
      <p className="text-sm text-fg-secondary">
        Operator policy for this deployment, one YAML document with a section per feature. A key you leave
        out takes its default. A saved change applies on the next tick of each loop that reads it, with no
        restart.
      </p>
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Settings document</CardTitle>
        </CardHeader>
        <CardContent>
          <SettingsDocumentEditor />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Values in effect</CardTitle>
        </CardHeader>
        <CardContent>
          <EffectiveSettingsView />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">History</CardTitle>
        </CardHeader>
        <CardContent>
          <SettingsHistory />
        </CardContent>
      </Card>
    </div>
  );
}
