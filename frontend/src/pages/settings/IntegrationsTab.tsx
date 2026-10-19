import { useState, useEffect, useMemo } from "react";
import { useIntegrationConfig, useUpdateIntegrationConfig } from "@/hooks/useIntegrations";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { AdminButton } from "@/components/auth/AdminButton";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { TagInput } from "@/components/ui/TagInput";
import { MaskedSecretInput } from "@/components/ui/MaskedSecretInput";
import { PageError } from "@/components/PageError";
import { toast } from "sonner";
import { Copy } from "lucide-react";
import type { IntegrationConfigUpdate } from "@/lib/types/integrations";
import { toastApiError } from "@/api/errorToast";

export default function IntegrationsTab() {
  const { data: integrationConfig, isLoading, isError, refetch } = useIntegrationConfig();
  const updateIntegration = useUpdateIntegrationConfig();

  const [form, setForm] = useState<{
    trigger_labels: string[];
    webhook_secret: string;
    slack_webhook_url: string;
    telegram_bot_token: string;
    telegram_chat_id: string;
    webhook_notify_url: string;
    webhook_notify_secret: string;
    linear_api_key: string;
    linear_webhook_secret: string;
    linear_repo_map: string;
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
        webhook_notify_url: integrationConfig.webhook_notify_url,
        webhook_notify_secret: "",
        linear_api_key: "",
        linear_webhook_secret: "",
        linear_repo_map: integrationConfig.linear_repo_map,
      });
    }
  }, [integrationConfig]);

  const hasChanges = useMemo(() => {
    if (!form || !integrationConfig) return false;
    const labelsChanged =
      JSON.stringify(form.trigger_labels) !== JSON.stringify(integrationConfig.trigger_labels);
    const chatIdChanged = form.telegram_chat_id !== integrationConfig.telegram_chat_id;
    const notifyUrlChanged = form.webhook_notify_url !== integrationConfig.webhook_notify_url;
    const repoMapChanged = form.linear_repo_map !== integrationConfig.linear_repo_map;
    return labelsChanged || chatIdChanged || notifyUrlChanged || repoMapChanged || changedSecrets.size > 0;
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
    if (form.webhook_notify_url !== integrationConfig.webhook_notify_url) {
      changes.webhook_notify_url = form.webhook_notify_url;
    }
    if (changedSecrets.has("webhook_notify_secret") && form.webhook_notify_secret) {
      changes.webhook_notify_secret = form.webhook_notify_secret;
    }
    if (changedSecrets.has("linear_api_key") && form.linear_api_key) {
      changes.linear_api_key = form.linear_api_key;
    }
    if (changedSecrets.has("linear_webhook_secret") && form.linear_webhook_secret) {
      changes.linear_webhook_secret = form.linear_webhook_secret;
    }
    if (form.linear_repo_map !== integrationConfig.linear_repo_map) {
      changes.linear_repo_map = form.linear_repo_map;
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
      onError: (e) => toastApiError(e, "Failed"),
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
        webhook_notify_url: integrationConfig.webhook_notify_url,
        webhook_notify_secret: "",
        linear_api_key: "",
        linear_webhook_secret: "",
        linear_repo_map: integrationConfig.linear_repo_map,
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
          <AdminButton onClick={handleSave} disabled={updateIntegration.isPending || !hasChanges}>
            {updateIntegration.isPending ? "Saving..." : "Save changes"}
          </AdminButton>
        </div>
      </div>

      {hasChanges && (
        <div className="text-sm text-warning">You have unsaved changes.</div>
      )}

      {/* Webhook Configuration */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Webhook configuration</CardTitle>
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
              <Label>Trigger labels</Label>
              <div className="mt-1">
                <TagInput
                  value={form.trigger_labels}
                  onChange={(tags) => setForm((prev) => (prev ? { ...prev, trigger_labels: tags } : prev))}
                  placeholder="Add trigger label..."
                  suggestions={["agent", "embry0", "bot", "autofix"]}
                />
              </div>
              <p className="text-xs text-white/30 mt-1">
                Issues/PRs with these labels will trigger agent runs.
              </p>
            </div>
            <div>
              <Label htmlFor="webhook_secret">HMAC secret</Label>
              <div className="mt-1">
                <MaskedSecretInput
                  id="webhook_secret"
                  isSet={integrationConfig.webhook_secret_set}
                  onChange={(v) => { setForm((prev) => (prev ? { ...prev, webhook_secret: v } : prev)); markSecretChanged("webhook_secret", v); }}
                  placeholder="Enter webhook HMAC secret"
                />
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Outbound event webhooks (RAV-1052) */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Outbound events</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div>
              <Label htmlFor="webhook_notify_url">Webhook URL</Label>
              <Input
                id="webhook_notify_url"
                value={form.webhook_notify_url}
                onChange={(e) => setForm((prev) => (prev ? { ...prev, webhook_notify_url: e.target.value } : prev))}
                placeholder="https://consumer.example.com/webhooks/embry0"
                className="font-mono text-xs"
              />
              <p className="text-xs text-white/30 mt-1">
                Signed POSTs for job.completed / job.failed, watcher.proposed, and agent.question events.
                Leave empty to disable.
              </p>
            </div>
            <div>
              <Label htmlFor="webhook_notify_secret">Signing secret</Label>
              <div className="mt-1">
                <MaskedSecretInput
                  id="webhook_notify_secret"
                  isSet={integrationConfig.webhook_notify_secret_set}
                  maskedValue={integrationConfig.webhook_notify_secret_masked}
                  onChange={(v) => { setForm((prev) => (prev ? { ...prev, webhook_notify_secret: v } : prev)); markSecretChanged("webhook_notify_secret", v); }}
                  placeholder="Enter outbound HMAC secret"
                />
              </div>
              <p className="text-xs text-white/30 mt-1">
                HMAC-SHA256 — X-Webhook-Signature-V2 over {"{timestamp}.{body}"} (Hermes Generic V2 compatible).
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Linear (#157) */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Linear</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div>
              <Label htmlFor="linear_api_key">API key</Label>
              <div className="mt-1">
                <MaskedSecretInput
                  id="linear_api_key"
                  isSet={integrationConfig.linear_api_key_set}
                  maskedValue={integrationConfig.linear_api_key_masked}
                  onChange={(v) => { setForm((prev) => (prev ? { ...prev, linear_api_key: v } : prev)); markSecretChanged("linear_api_key", v); }}
                  placeholder="Enter Linear API key"
                />
              </div>
              <p className="text-xs text-white/30 mt-1">
                Personal API key used to read trigger issues and post job outcomes back.
                Leave empty to fall back to LINEAR_API_KEY.
              </p>
            </div>
            <div>
              <Label htmlFor="linear_webhook_secret">Webhook secret</Label>
              <div className="mt-1">
                <MaskedSecretInput
                  id="linear_webhook_secret"
                  isSet={integrationConfig.linear_webhook_secret_set}
                  maskedValue={integrationConfig.linear_webhook_secret_masked}
                  onChange={(v) => { setForm((prev) => (prev ? { ...prev, linear_webhook_secret: v } : prev)); markSecretChanged("linear_webhook_secret", v); }}
                  placeholder="Enter Linear webhook secret"
                />
              </div>
              <p className="text-xs text-white/30 mt-1">
                Linear signs deliveries with a bare hex HMAC-SHA256 in Linear-Signature — no sha256= prefix, unlike GitHub.
              </p>
            </div>
            <div>
              <Label htmlFor="linear_repo_map">Project / team → repo map</Label>
              <Input
                id="linear_repo_map"
                value={form.linear_repo_map}
                onChange={(e) => setForm((prev) => (prev ? { ...prev, linear_repo_map: e.target.value } : prev))}
                placeholder={'{"My Project": "owner/repo", "ENG": "owner/other"}'}
                className="font-mono text-xs"
              />
              <p className="text-xs text-white/30 mt-1">
                JSON keyed by Linear project name or team key. An issue whose project is unmapped is ignored.
              </p>
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
            <Label htmlFor="slack_webhook_url">Webhook URL</Label>
            <div className="mt-1">
              <MaskedSecretInput
                id="slack_webhook_url"
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
              <Label htmlFor="telegram_bot_token">Bot token</Label>
              <div className="mt-1">
                <MaskedSecretInput
                  id="telegram_bot_token"
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
