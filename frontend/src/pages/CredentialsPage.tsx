import { useState } from "react";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import { BindingsDrawer } from "@/components/credentials/BindingsDrawer";
import { CredentialModal } from "@/components/credentials/CredentialModal";
import { CredentialTable } from "@/components/credentials/CredentialTable";
import { PageError } from "@/components/PageError";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Tooltip } from "@/components/ui/Tooltip";
import {
  useCreateCredential,
  useCredentials,
  useDeleteCredential,
  useImportEnvCredentials,
  useRotateCredential,
  useUpdateCredential,
  useVerifyCredential,
} from "@/hooks/useCredentials";
import type { Credential, CredentialCreate, ImportEnvReport } from "@/lib/types/credentials";
import { useAuthStore } from "@/stores/authStore";

const DOCS_HREF = "https://github.com/sudoTomas/embry0/blob/main/docs/credentials.md";

type OpenModal = { mode: "create" } | { mode: "rotate"; credential: Credential };

function importSummary(report: ImportEnvReport): string {
  return `Imported ${report.created.length} created, ${report.bound.length} bound, ${report.skipped.length} skipped.`;
}

function ReportList({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h3 className="label-caps">{title}</h3>
      {items.length === 0 ? (
        <p className="mt-1 text-sm text-muted-foreground">None</p>
      ) : (
        <ul className="mt-1 space-y-1 text-sm">
          {items.map((item, index) => (
            <li key={`${title}-${index}`}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AdminButton({
  isAdmin,
  disabled = false,
  onClick,
  variant = "default",
  children,
}: {
  isAdmin: boolean;
  disabled?: boolean;
  onClick: () => void;
  /** One primary per header: the second action is secondary. */
  variant?: "default" | "secondary";
  children: string;
}) {
  const button = (
    <Button
      type="button"
      size="sm"
      variant={variant}
      disabled={!isAdmin || disabled}
      className={!isAdmin ? "pointer-events-none" : undefined}
      onClick={onClick}
    >
      {children}
    </Button>
  );
  if (isAdmin) return button;
  return <Tooltip content="Admin scope required">{button}</Tooltip>;
}

export function CredentialsPage() {
  const authMode = useAuthStore((s) => s.authMode);
  const userTier = useAuthStore((s) => s.user?.tier);
  const isAdmin = authMode === "key" || userTier === "admin";

  const credentialsQuery = useCredentials();
  const createCredential = useCreateCredential();
  const updateCredential = useUpdateCredential();
  const rotateCredential = useRotateCredential();
  const verifyCredential = useVerifyCredential();
  const deleteCredential = useDeleteCredential();
  const importEnv = useImportEnvCredentials();

  const [modal, setModal] = useState<OpenModal | null>(null);
  const [bindingsFor, setBindingsFor] = useState<Credential | null>(null);
  const [verifyingId, setVerifyingId] = useState<string | null>(null);
  const [replaceSecrets, setReplaceSecrets] = useState(false);
  const [importReport, setImportReport] = useState<ImportEnvReport | null>(null);

  const credentials = credentialsQuery.data ?? [];

  async function runImport(replace: boolean) {
    try {
      const report = await importEnv.mutateAsync(replace);
      setImportReport(report);
      toast.success(importSummary(report));
    } catch (error) {
      toastApiError(error);
    }
  }

  async function onModalSubmit(payload: CredentialCreate | { secret: string }) {
    if (modal?.mode === "rotate") {
      await rotateCredential.mutateAsync({ id: modal.credential.id, secret: payload.secret });
      toast.success(`Rotated "${modal.credential.label}"`);
      setModal(null);
      return;
    }
    const body = payload as CredentialCreate;
    await createCredential.mutateAsync(body);
    toast.success(`Created "${body.label}"`);
    setModal(null);
  }

  function onVerify(credential: Credential) {
    setVerifyingId(credential.id);
    verifyCredential.mutate(credential.id, {
      onSuccess: (row) => {
        toast.success(row.verified_login ? `Verified as ${row.verified_login}` : "Verified");
      },
      onError: (error) => {
        toastApiError(error);
      },
      onSettled: () => setVerifyingId((current) => (current === credential.id ? null : current)),
    });
  }

  function onToggleDisabled(credential: Credential) {
    const status = credential.status === "disabled" ? "ok" : "disabled";
    updateCredential.mutate(
      { id: credential.id, body: { status } },
      {
        onSuccess: () => {
          toast.success(status === "ok" ? `Enabled "${credential.label}"` : `Disabled "${credential.label}"`);
        },
        onError: (error) => toastApiError(error),
      },
    );
  }

  function onDelete(credential: Credential) {
    if (!window.confirm(`Delete credential "${credential.label}"?`)) return;
    deleteCredential.mutate(credential.id, {
      onSuccess: () => toast.success(`Deleted "${credential.label}"`),
      onError: (error) => toastApiError(error),
    });
  }

  const replaceControl = (
    <label
      htmlFor="replace-differing-secrets"
      className={
        isAdmin
          ? "flex items-center gap-2 text-sm text-muted-foreground"
          : "pointer-events-none flex items-center gap-2 text-sm text-muted-foreground opacity-50"
      }
    >
      <input
        id="replace-differing-secrets"
        type="checkbox"
        checked={replaceSecrets}
        disabled={!isAdmin || importEnv.isPending}
        onChange={(event) => {
          const next = event.target.checked;
          setReplaceSecrets(next);
          // The box is the replace path: checking it re-runs import with true.
          if (next) void runImport(true);
        }}
      />
      Replace differing secrets
    </label>
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold">Credentials</h1>
          <p className="mt-1 max-w-xl text-sm text-muted-foreground">
            Accounts embry0 acts as. Secrets are write-only; the env tokens are an import source.{" "}
            <a
              href={DOCS_HREF}
              target="_blank"
              rel="noreferrer"
              className="text-primary underline-offset-4 hover:underline"
            >
              docs/credentials.md
            </a>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {isAdmin ? replaceControl : <Tooltip content="Admin scope required">{replaceControl}</Tooltip>}
          <AdminButton
            isAdmin={isAdmin}
            variant="secondary"
            disabled={importEnv.isPending}
            onClick={() => {
              void runImport(replaceSecrets);
            }}
          >
            Import from environment
          </AdminButton>
          <AdminButton isAdmin={isAdmin} onClick={() => setModal({ mode: "create" })}>
            Add credential
          </AdminButton>
        </div>
      </div>

      {importReport && (
        <Card className="space-y-3 p-4" data-testid="import-report">
          <h2 className="text-sm font-medium">Import report</h2>
          <ReportList title="Created" items={importReport.created} />
          <ReportList title="Bound" items={importReport.bound} />
          <ReportList title="Skipped" items={importReport.skipped} />
        </Card>
      )}

      {credentialsQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading credentials...</p>
      ) : credentialsQuery.isError ? (
        <PageError
          message="Failed to load credentials"
          onRetry={() => {
            void credentialsQuery.refetch();
          }}
        />
      ) : (
        <CredentialTable
          credentials={credentials}
          isAdmin={isAdmin}
          onRotate={(credential) => setModal({ mode: "rotate", credential })}
          onVerify={onVerify}
          onToggleDisabled={onToggleDisabled}
          onDelete={onDelete}
          onBindings={setBindingsFor}
          verifyingId={verifyingId}
        />
      )}

      {modal && (
        <CredentialModal
          modeSpec={modal}
          gitlabHost={null}
          onSubmit={onModalSubmit}
          onDeviceLoginDone={() => {
            void credentialsQuery.refetch();
          }}
          onClose={() => setModal(null)}
        />
      )}

      {bindingsFor && (
        <BindingsDrawer
          credential={bindingsFor}
          isAdmin={isAdmin}
          onClose={() => setBindingsFor(null)}
        />
      )}
    </div>
  );
}
