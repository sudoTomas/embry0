import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Copy, FolderGit2, KeyRound, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useApiKeys } from "@/hooks/useApiKeys";
import {
  createApiKey,
  revokeApiKey,
  setApiKeyAllowedRepos,
  type ApiKey,
  type CreatedApiKey,
} from "@/api/apiKeys";
import { useAuthStore } from "@/stores/authStore";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { parseRepoList } from "@/lib/repoList";
import { formatDateTime } from "@/lib/utils";

// The admin scope stands out in the standard's fifth status hue; gold stays
// for the page's primary action.
const SCOPE_TONES: Record<ApiKey["scope"], BadgeTone> = {
  admin: "purple",
  operate: "neutral",
  merge: "warning",
  read: "neutral",
};

/**
 * Self-service API keys (RAV-1056 Phase B). Session users mint keys scoped
 * at or below their own tier; admins additionally see everyone's keys and
 * can mint unowned service keys. The plaintext token is shown exactly once
 * after creation — copy it or lose it.
 */

/** The `claude mcp add` command for this deployment, with the fresh key inline.
 * Points at the hosted MCP endpoint (/mcp) — nothing to clone or install.
 * Origin comes from the browser so the snippet is correct on any deployment —
 * no environment-specific strings live in the repo. */
function mcpAddCommand(key: string): string {
  return [
    "claude mcp add --transport http embry0 \\",
    `  ${window.location.origin}/mcp \\`,
    `  --header "Authorization: Bearer ${key}"`,
  ].join("\n");
}

export default function ApiKeysTab() {
  const keys = useApiKeys();
  const queryClient = useQueryClient();
  const authMode = useAuthStore((s) => s.authMode);
  const userTier = useAuthStore((s) => s.user?.tier);
  const isAdmin = authMode === "key" || userTier === "admin";

  const [name, setName] = useState("");
  const [scope, setScope] = useState("read");
  const [expiresDays, setExpiresDays] = useState("");
  const [service, setService] = useState(false);
  const [allowedRepos, setAllowedRepos] = useState("");
  const [minted, setMinted] = useState<CreatedApiKey | null>(null);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["api-keys"] });

  const createMut = useMutation({
    mutationFn: (body: Parameters<typeof createApiKey>[0]) => createApiKey(body),
    onSuccess: (row) => {
      setMinted(row);
      setName("");
      setExpiresDays("");
      setService(false);
      setAllowedRepos("");
      invalidate();
    },
    onError: () => toast.error("Key creation failed"),
  });

  const reposMut = useMutation({
    mutationFn: ({ id, repos }: { id: string; repos: string[] }) => setApiKeyAllowedRepos(id, repos),
    onSuccess: (row) => {
      toast.success(
        row.allowed_repos ? `Key "${row.name}" limited to ${row.allowed_repos.length} repo(s)` : `Key "${row.name}" unrestricted`,
      );
      invalidate();
    },
    onError: () => toast.error("Could not change the key's repositories"),
  });

  const revokeMut = useMutation({
    mutationFn: (id: string) => revokeApiKey(id),
    onSuccess: (row) => {
      toast.success(`Key "${row.name}" revoked`);
      invalidate();
    },
    onError: () => toast.error("Revoke failed"),
  });

  // "merge" is the chat relay's narrow scope (read + merge decisions + agent
  // answers); only an admin may mint one.
  const scopeOptions = isAdmin ? ["read", "operate", "admin", "merge"] : ["read", "operate"];

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound size={16} /> Create key
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Keys authenticate MCP/CLI clients with the <code>Authorization: Bearer</code> header on
            any origin. Scope is capped at your tier; <code>read</code> keys can only perform
            read-only requests.
          </p>
          <div className="grid gap-3 sm:grid-cols-4">
            <div className="sm:col-span-2">
              <Label htmlFor="key-name">Name</Label>
              <Input
                id="key-name"
                placeholder="e.g. laptop-cli"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="key-scope">Scope</Label>
              <Select id="key-scope" value={scope} onChange={(e) => setScope(e.target.value)}>
                {scopeOptions.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="key-expiry">Expires (days)</Label>
              <Input
                id="key-expiry"
                placeholder="never"
                inputMode="numeric"
                value={expiresDays}
                onChange={(e) => setExpiresDays(e.target.value.replace(/[^0-9]/g, ""))}
              />
            </div>
          </div>
          <div>
            <Label htmlFor="key-repos">Repositories (optional)</Label>
            <Input
              id="key-repos"
              placeholder="all repositories — or e.g. acme/widgets, acme/docs"
              value={allowedRepos}
              onChange={(e) => setAllowedRepos(e.target.value)}
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Limits the work this key may start (jobs, issues, triage, resumes) to these repositories.
              A limited key cannot mint other keys.
            </p>
          </div>
          {isAdmin && (
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <input type="checkbox" checked={service} onChange={(e) => setService(e.target.checked)} />
              Service key (unowned — for agents/infra such as a chat relay)
            </label>
          )}
          <Button
            disabled={!name.trim() || createMut.isPending}
            onClick={() =>
              createMut.mutate({
                name: name.trim(),
                scope,
                expires_days: expiresDays ? Number(expiresDays) : undefined,
                service: service || undefined,
                allowed_repos: parseRepoList(allowedRepos).length ? parseRepoList(allowedRepos) : undefined,
              })
            }
          >
            {createMut.isPending ? "Creating…" : "Create key"}
          </Button>
        </CardContent>
      </Card>

      {minted && (
        <Card className="border-amber-500/40">
          <CardContent className="space-y-2 pt-4">
            <p className="text-sm font-medium text-foreground">
              Copy this key now — it will not be shown again.
            </p>
            <div className="flex items-center gap-2">
              <code
                data-testid="minted-key"
                className="flex-1 overflow-x-auto rounded bg-black/40 px-3 py-2 font-mono text-xs"
              >
                {minted.key}
              </code>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  void navigator.clipboard.writeText(minted.key);
                  toast.success("Copied");
                }}
              >
                <Copy size={14} />
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setMinted(null)}>
                Done
              </Button>
            </div>
            <div className="space-y-2 border-t border-border pt-3">
              <p className="text-sm font-medium text-foreground">Connect Claude Code</p>
              <p className="text-xs text-muted-foreground">
                Registers this deployment&apos;s hosted MCP server — nothing to install:
              </p>
              <div className="flex items-start gap-2">
                <pre
                  data-testid="mcp-command"
                  className="flex-1 overflow-x-auto whitespace-pre rounded bg-black/40 px-3 py-2 font-mono text-xs"
                >
                  {mcpAddCommand(minted.key)}
                </pre>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    void navigator.clipboard.writeText(mcpAddCommand(minted.key));
                    toast.success("Copied");
                  }}
                >
                  <Copy size={14} />
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                Then ask Claude Code to list embry0 jobs to verify the connection.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {keys.isLoading && <TableSkeleton rows={3} />}
      {keys.isError && <PageError message="Could not load keys" onRetry={() => keys.refetch()} />}
      {keys.data && keys.data.length === 0 && (
        <EmptyState title="No API keys" description="Create one above to authenticate MCP/CLI clients." />
      )}

      {keys.data && keys.data.length > 0 && (
        <div className="embry0-card overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Key</th>
                <th>Scope</th>
                <th>Repositories</th>
                {isAdmin && <th>Owner</th>}
                <th>Last used</th>
                <th>Expires</th>
                <th>Status</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {keys.data.map((k) => (
                <tr key={k.id}>
                  <td>{k.name}</td>
                  <td className="font-mono text-xs text-muted-foreground">{k.prefix}…</td>
                  <td>
                    <Badge tone={SCOPE_TONES[k.scope]}>{k.scope}</Badge>
                  </td>
                  <td className="text-xs" data-testid={`key-repos-${k.id}`}>
                    {k.allowed_repos ? (
                      <span className="font-mono">{k.allowed_repos.join(", ")}</span>
                    ) : (
                      <span className="text-muted-foreground">all</span>
                    )}
                  </td>
                  {isAdmin && (
                    <td className="text-xs text-muted-foreground">
                      {k.service ? <Badge tone="neutral">Service</Badge> : (k.user_id ?? "—")}
                    </td>
                  )}
                  <td className="text-xs text-muted-foreground">
                    {k.last_used_at ? formatDateTime(k.last_used_at) : "never"}
                  </td>
                  <td className="text-xs text-muted-foreground">
                    {k.expires_at ? formatDateTime(k.expires_at) : "—"}
                  </td>
                  <td>
                    {k.revoked_at ? <Badge tone="error">Revoked</Badge> : <Badge tone="success">Active</Badge>}
                  </td>
                  <td>
                    <div className="flex justify-end">
                      {!k.revoked_at && (
                        <Button
                          variant="ghost"
                          size="xs"
                          title="Change the repositories this key may start work on"
                          disabled={reposMut.isPending}
                          onClick={() => {
                            const answer = window.prompt(
                              `Repositories key "${k.name}" may start work on, comma-separated. Leave empty for all.`,
                              (k.allowed_repos ?? []).join(", "),
                            );
                            if (answer !== null) {
                              reposMut.mutate({ id: k.id, repos: parseRepoList(answer) });
                            }
                          }}
                        >
                          <FolderGit2 size={14} />
                        </Button>
                      )}
                      {!k.revoked_at && (
                        <Button
                          variant="ghost"
                          size="xs"
                          title="Revoke this key"
                          disabled={revokeMut.isPending}
                          onClick={() => {
                            if (window.confirm(`Revoke key "${k.name}"? Clients using it will get 401s.`)) {
                              revokeMut.mutate(k.id);
                            }
                          }}
                        >
                          <Trash2 size={14} className="text-destructive" />
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
