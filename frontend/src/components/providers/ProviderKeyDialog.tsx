import { useState } from "react";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { useAddProviderKey } from "@/hooks/useProvider";
import type { LlmProviderName } from "@/lib/llmProviders";
import { providerLabel } from "@/lib/status";

/**
 * A provider's key, entered on its card: stored in the credential registry and made the
 * provider's global binding, so every job without a repository binding uses it. Replacing
 * a key binds the new one in the old one's place; the old credential stays on the
 * Credentials page until someone deletes it.
 */
export function ProviderKeyDialog({
  provider,
  replacing,
  onClose,
}: {
  provider: LlmProviderName;
  replacing: boolean;
  onClose: () => void;
}) {
  const add = useAddProviderKey();
  const stamp = new Date().toISOString().slice(0, 10);
  const [secret, setSecret] = useState("");
  const [label, setLabel] = useState(replacing ? `api-key-${stamp}` : "api-key");
  const [baseUrl, setBaseUrl] = useState("");
  const name = providerLabel(provider);
  const needsBaseUrl = provider === "custom";

  const save = () =>
    add.mutate(
      { provider, secret: secret.trim(), label: label.trim(), ...(needsBaseUrl ? { baseUrl: baseUrl.trim() } : {}) },
      {
        onSuccess: () => {
          toast.success(`${name} key saved and used for every repository`);
          onClose();
        },
        onError: (e: Error) => toastApiError(e, "Saving the key failed"),
      },
    );

  return (
    <Dialog onClose={onClose} title={`${replacing ? "Replace" : "Add"} the ${name} key`}>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
      >
        <div className="space-y-1">
          <Label htmlFor="provider-key-secret">API key</Label>
          <Input
            id="provider-key-secret"
            type="password"
            autoComplete="off"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            Stored encrypted in the credential registry and never shown again. Jobs in every repository use it unless a
            repository binds a key of its own.
          </p>
        </div>
        {needsBaseUrl && (
          <div className="space-y-1">
            <Label htmlFor="provider-key-base-url">API root</Label>
            <Input
              id="provider-key-base-url"
              placeholder="http://10.0.0.5:11434/v1"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </div>
        )}
        <div className="space-y-1">
          <Label htmlFor="provider-key-label">Label</Label>
          <Input id="provider-key-label" value={label} onChange={(e) => setLabel(e.target.value)} />
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            disabled={add.isPending || !secret.trim() || !label.trim() || (needsBaseUrl && !baseUrl.trim())}
          >
            {add.isPending ? "Saving..." : "Save key"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
