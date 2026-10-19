import type { ReactNode } from "react";
import { KeyRound, Link2, Power, RefreshCw, ShieldCheck, X } from "lucide-react";
import { StatusBadge } from "@/components/credentials/StatusBadge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Tooltip } from "@/components/ui/Tooltip";
import type { Credential, CredentialKind } from "@/lib/types/credentials";
import { cn, formatDate } from "@/lib/utils";
import { sentenceCase } from "@/lib/status";

export interface CredentialTableProps {
  credentials: Credential[];
  isAdmin: boolean;
  onRotate(c: Credential): void;
  onVerify(c: Credential): void;
  onToggleDisabled(c: Credential): void;
  onDelete(c: Credential): void;
  onBindings(c: Credential): void;
  verifyingId?: string | null;
}

/** Below sm only the label (with its provider under it), the status and the
 * actions stay, so a phone shows what matters without a sideways scroll. */
const HEADERS: readonly { label: string; phone: boolean }[] = [
  { label: "Label", phone: true },
  { label: "Provider", phone: false },
  { label: "Preview", phone: false },
  { label: "Author", phone: false },
  { label: "Status", phone: true },
  { label: "Bindings", phone: false },
  { label: "Last used", phone: false },
];

/** The credential kind in words, never the stored identifier. */
const KIND_LABELS: Record<CredentialKind, string> = {
  forge_token: "Forge token",
  llm_oauth: "OAuth",
  llm_api_key: "API key",
  ssh_key: "SSH key",
};

function authorLabel(credential: Credential): string {
  const name = credential.git_author_name;
  const email = credential.git_author_email;
  if (name && email) return `${name} <${email}>`;
  if (name) return name;
  if (email) return `<${email}>`;
  return "branding default";
}

/**
 * Mutating controls are disabled for non-admins. The tooltip sits on a
 * wrapper because a disabled button does not receive pointer events.
 */
function GuardedAction({
  isAdmin,
  label,
  testId,
  onClick,
  disabled = false,
  destructive = false,
  children,
}: {
  isAdmin: boolean;
  label: string;
  testId: string;
  onClick: () => void;
  disabled?: boolean;
  destructive?: boolean;
  children: ReactNode;
}) {
  const button = (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className={cn(
        "h-8 w-8 text-muted-foreground",
        destructive && "hover:bg-destructive/10 hover:text-destructive",
      )}
      data-testid={testId}
      aria-label={label}
      title={isAdmin ? label : undefined}
      disabled={!isAdmin || disabled}
      onClick={onClick}
    >
      {children}
    </Button>
  );
  if (isAdmin) return button;
  return <Tooltip content="Admin scope required">{button}</Tooltip>;
}

export function CredentialTable({
  credentials,
  isAdmin,
  onRotate,
  onVerify,
  onToggleDisabled,
  onDelete,
  onBindings,
  verifyingId,
}: CredentialTableProps) {
  if (credentials.length === 0) {
    return (
      <Card className="p-0">
        <EmptyState
          icon={KeyRound}
          title="No credentials yet"
          description="Run embry0 credentials import-env to import forge tokens from the orchestrator environment."
        />
      </Card>
    );
  }

  return (
    <Card className="p-0">
      <div className="overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr className="text-[11px] font-medium">
              {HEADERS.map(({ label, phone }) => (
                <th key={label} scope="col" className={phone ? undefined : "hidden sm:table-cell"}>
                  {label}
                </th>
              ))}
              <th scope="col" className="text-right">
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {credentials.map((credential) => {
              const verifying = verifyingId === credential.id;
              const author = authorLabel(credential);
              return (
                <tr
                  key={credential.id}
                  data-testid="credential-row"
                >
                  <td>
                    <div className="text-foreground">{credential.label}</div>
                    <div className="text-xs text-muted-foreground sm:hidden">{credential.provider}</div>
                    {credential.verified_login && (
                      <div className="text-xs text-muted-foreground">{credential.verified_login}</div>
                    )}
                  </td>
                  <td className="hidden sm:table-cell">
                    <div>{credential.provider}</div>
                    <div className="text-xs text-muted-foreground">
                      {credential.provider === "anthropic" || credential.provider === "xai"
                        ? KIND_LABELS[credential.kind] ?? sentenceCase(credential.kind)
                        : credential.host}
                    </div>
                  </td>
                  <td className="hidden font-mono text-xs text-muted-foreground sm:table-cell">
                    {credential.masked_preview}
                  </td>
                  <td
                    className={cn(
                      "hidden px-4 py-3 text-xs sm:table-cell",
                      author === "branding default" && "text-muted-foreground",
                    )}
                  >
                    {author}
                  </td>
                  <td>
                    <StatusBadge credential={credential} />
                  </td>
                  <td className="hidden sm:table-cell">
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      className="h-8 px-2 font-mono text-xs"
                      aria-label={`Show ${credential.binding_count} bindings`}
                      onClick={() => onBindings(credential)}
                    >
                      {credential.binding_count}
                    </Button>
                  </td>
                  <td className="hidden whitespace-nowrap text-xs text-muted-foreground sm:table-cell">
                    {credential.last_used_at ? formatDate(credential.last_used_at) : "never"}
                  </td>
                  <td className="text-right">
                    {/* Three to a line below sm, one line above. */}
                    <div className="ml-auto flex max-w-[6.5rem] flex-wrap items-center justify-end gap-1 sm:max-w-none sm:flex-nowrap">
                      <GuardedAction
                        isAdmin={isAdmin}
                        label="Rotate credential"
                        testId="credential-rotate"
                        onClick={() => onRotate(credential)}
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                      </GuardedAction>
                      <GuardedAction
                        isAdmin={isAdmin}
                        label={verifying ? "Verifying credential" : "Verify credential"}
                        testId="credential-verify"
                        disabled={verifying}
                        onClick={() => onVerify(credential)}
                      >
                        <ShieldCheck className="h-3.5 w-3.5" />
                      </GuardedAction>
                      <GuardedAction
                        isAdmin={isAdmin}
                        label={credential.status === "disabled" ? "Enable credential" : "Disable credential"}
                        testId="credential-toggle"
                        onClick={() => onToggleDisabled(credential)}
                      >
                        <Power className="h-3.5 w-3.5" />
                      </GuardedAction>
                      <GuardedAction
                        isAdmin={isAdmin}
                        label="Delete credential"
                        testId="credential-delete"
                        destructive
                        onClick={() => onDelete(credential)}
                      >
                        <X className="h-3.5 w-3.5" />
                      </GuardedAction>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        className="h-8 w-8 text-muted-foreground"
                        data-testid="credential-bindings"
                        aria-label="Show bindings"
                        title="Bindings"
                        onClick={() => onBindings(credential)}
                      >
                        <Link2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
