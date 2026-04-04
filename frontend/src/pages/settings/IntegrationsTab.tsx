import { useState, useEffect, useMemo } from "react";
import { useIntegrationConfig, useUpdateIntegrationConfig } from "@/hooks/useIntegrations";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { TagInput } from "@/components/ui/TagInput";
import { MaskedSecretInput } from "@/components/ui/MaskedSecretInput";
import { PageError } from "@/components/PageError";
import { toast } from "sonner";
import { Copy } from "lucide-react";
import type { IntegrationConfig, IntegrationConfigUpdate } from "@/lib/types/integrations";

export default function IntegrationsTab() {
  const { data: integrationConfig, isLoading, isError, refetch } = useIntegrationConfig();
  const updateIntegration = useUpdateIntegrationConfig();

  const [form, setForm] = useState<{
    trigger_labels: string[];
    webhook_secret: string;
    slack_webhook_url: string;
    telegram_bot_token: string;
    telegram_chat_id: string;
  } | null>(null);

  // Track which secrets the user has actively changed
  const [changedSecrets, setChangedSecrets] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (integrationConfig) {
      setForm((prev) => prev ?? {
        trigger_labels: [...integrationConfig.trigger_labels],
        webhook_secret: "",
        slack_webhook_url: "",
        telegram_bot_token: "",
        telegram_chat_id: integrationConfig.telegram_chat_id,
      });
    }
  }, [integrationConfig]);

  const hasChanges = useMemo(() => {
    if (!form || !integrationConfig) return false;
    const labelsChanged =
      JSON.stringify(form.trigger_labels) !== JSON.stringify(integrationConfig.trigger_labels);
    const chatIdChanged = form.telegram_chat_id !== integrationConfig.telegram_chat_id;
    return labelsChanged || chatIdChanged || changedSecrets.size > 0;
  }, [form, integrationConfig, changedSecrets]);

  if (isError) {
    return <PageError message="Failed to load integration settings" onRetry={() => refetch()} />;
  }

  if (isLoading || !form || !integrationConfig) {
    return <div className="text-muted-foreground">Loading...</div>;
  }

  const webhookUrl = `${window.location.origin}/webhook`;

  const handleCopyWebhook = async () => {
    try {
      await navigator.clipboard.writeText(webhookUrl);
      toast.success("Webhook URL copied");
    } catch {
      toast.error("Failed to copy");
    }
  };

  const markSecretChanged = (key: string, value: string) => {
    setChangedSecrets((prev) => {
      const next = new Set(prev);
      if (value) {
        next.add(key);
      } else {
        next.delete(key);
      }
      return next;
    });
  };

  const handleSave = () => {
    if (!form) return;
    const changes: IntegrationConfigUpdate = {};

    if (JSON.stringify(form.trigger_labels) !== JSON.stringify(integrationConfig.trigger_labels)) {
      changes.trigger_labels = form.trigger_labels;
    }
    if (changedSecrets.has("webhook_secret") && form.webhook_secret) {
      changes.webhook_secret = form.webhook_secret;
    }
    if (changedSecrets.has("slack_webhook_url") && form.slack_webhook_url) {
      changes.slack_webhook_url = form.slack_webhook_url;
    }
    if (changedSecrets.has("telegram_bot_token") && form.telegram_bot_token) {
      changes.telegram_bot_token = form.telegram_bot_token;
    }
    if (form.telegram_chat_id !== integrationConfig.telegram_chat_id) {
      changes.telegram_chat_id = form.telegram_chat_id;
    }

    if (Object.keys(changes).length === 0) {
      toast.info("No changes to save");
      return;
    }

    updateIntegration.mutate(changes, {
      onSuccess: () => {
        setForm(null);
        setChangedSecrets(new Set());
        toast.success("Integration settings saved");
      },
      onError: (e) => toast.error(`Failed: ${e.message}`),
    });
  };

  const handleReset = () => {
    if (integrationConfig) {
      setForm({
        trigger_labels: [...integrationConfig.trigger_labels],
        webhook_secret: "",
        slack_webhook_url: "",
        telegram_bot_token: "",
        telegram_chat_id: integrationConfig.telegram_chat_id,
      });
      setChangedSecrets(new Set());
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div />
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleReset} disabled={!hasChanges}>
            Reset
          </Button>
          <Button onClick={handleSave} disabled={updateIntegration.isPending || !hasChanges}>
            {updateIntegration.isPending ? "Saving..." : "Save Changes"}
          </Button>
        </div>
      </div>

      {hasChanges && (
        <div className="text-sm text-warning">You have unsaved changes.</div>
      )}

      {/* Webhook Configuration */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Webhook Configuration</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div>
              <Label>Webhook URL</Label>
              <div className="flex items-center gap-2 mt-1">
                <Input value={webhookUrl} readOnly className="font-mono text-xs" />
                <Button variant="outline" size="sm" onClick={handleCopyWebhook}>
                  <Copy className="w-4 h-4" />
                </Button>
              </div>
            </div>
            <div>
              <Label>Trigger Labels</Label>
              <div className="mt-1">
                <TagInput
                  value={form.trigger_labels}
                  onChange={(tags) => setForm((prev) => (prev ? { ...prev, trigger_labels: tags } : prev))}
                  placeholder="Add trigger label..."
                  suggestions={["agent", "legion", "bot", "autofix"]}
                />
              </div>
              <p className="text-xs text-white/30 mt-1">
                Issues/PRs with these labels will trigger agent runs.
              </p>
            </div>
            <div>
              <Label>HMAC Secret</Label>
              <div className="mt-1">
                <MaskedSecretInput
                  isSet={integrationConfig.webhook_secret_set}
                  onChange={(v) => { setForm((prev) => (prev ? { ...prev, webhook_secret: v } : prev)); markSecretChanged("webhook_secret", v); }}
                  placeholder="Enter webhook HMAC secret"
                />
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Slack */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Slack</CardTitle>
        </CardHeader>
        <CardContent>
          <div>
            <Label>Webhook URL</Label>
            <div className="mt-1">
              <MaskedSecretInput
                isSet={integrationConfig.slack_webhook_url_set}
                maskedValue={integrationConfig.slack_webhook_url_masked}
                onChange={(v) => { setForm((prev) => (prev ? { ...prev, slack_webhook_url: v } : prev)); markSecretChanged("slack_webhook_url", v); }}
                placeholder="https://hooks.slack.com/services/..."
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Telegram */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Telegram</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div>
              <Label>Bot Token</Label>
              <div className="mt-1">
                <MaskedSecretInput
                  isSet={integrationConfig.telegram_bot_token_set}
                  maskedValue={integrationConfig.telegram_bot_token_masked}
                  onChange={(v) => { setForm((prev) => (prev ? { ...prev, telegram_bot_token: v } : prev)); markSecretChanged("telegram_bot_token", v); }}
                  placeholder="123456:ABC-DEF..."
                />
              </div>
            </div>
            <div>
              <Label htmlFor="telegram_chat_id">Chat ID</Label>
              <Input
                id="telegram_chat_id"
                value={form.telegram_chat_id}
                onChange={(e) => setForm((prev) => (prev ? { ...prev, telegram_chat_id: e.target.value } : prev))}
                placeholder="-1001234567890"
              />
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
