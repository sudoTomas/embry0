import { useEffect, useState } from "react";
import { Link } from "react-router";
import { isAxiosError } from "axios";
import { FileCode2 } from "lucide-react";
import { toast } from "sonner";
import { useQaConfig, useSaveQaConfig, useDeleteQaConfig } from "@/hooks/useQaConfig";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { Button, buttonVariants } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";
import { cn } from "@/lib/utils";

/**
 * Minimal schema-valid qa.yaml v2 skeleton for hand-written configs.
 * Mirrors the shape `POST /repos/bootstrap` generates (see
 * embry0/services/bootstrap.py::render_starter_qa_yaml).
 */
function starterTemplate(repo: string): string {
  return (
    `# qa.yaml v2 for ${repo} — stored in the embry0 DB.\n` +
    "# Reference: docs/qa-yaml-reference.md — validated on save.\n" +
    "version: 2\n" +
    "workspace_provider:\n" +
    "  type: static-apps\n" +
    "  config:\n" +
    '    apps:\n' +
    '      web: {path: "."}\n' +
    "qa_required: auto\n" +
    "apps:\n" +
    "  web:\n" +
    '    boot_command: "npm ci && npm run dev"\n' +
    '    frontend_url: "http://localhost:3000"\n' +
    "    ready_checks:\n" +
    '      - http: "http://localhost:3000"\n' +
    "        expect_status: 200\n"
  );
}

interface QaConfigEditorProps {
  /** Full repo path; may exceed two segments (GitLab subgroups, #162). */
  repo: string;
}

/** The stored-qa.yaml editor (RAV-1253) — shared by the standalone config
 * page and the unified repo detail page. */
export function QaConfigEditor({ repo }: QaConfigEditorProps) {

  const { data, isLoading, isError, refetch } = useQaConfig(repo);
  const saveMutation = useSaveQaConfig();
  const deleteMutation = useDeleteQaConfig();

  // null = viewing the server state; string = editing (dirty or template-seeded)
  const [draft, setDraft] = useState<string | null>(null);
  const text = draft ?? data ?? "";
  const dirty = draft !== null && draft !== (data ?? "");

  // A save refetches via invalidation; once the server text matches the
  // draft, drop back to the pristine server state.
  useEffect(() => {
    if (draft !== null && data === draft) setDraft(null);
  }, [data, draft]);

  useEffect(() => {
    if (!dirty) return;
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault();
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [dirty]);

  if (isError) return <PageError message="Failed to load the QA config" onRetry={() => refetch()} />;
  if (isLoading || data === undefined) return <TableSkeleton />;

  const showEditor = data !== null || draft !== null;

  const saveError = saveMutation.error;
  const validationDetail =
    isAxiosError(saveError) && saveError.response?.status === 422
      ? ((saveError.response.data as { detail?: string })?.detail ?? "validation failed")
      : null;
  const otherSaveError = saveError && !validationDetail;

  function handleSave() {
    saveMutation.mutate(
      { fullRepo: repo, yamlText: text },
      {
        onSuccess: () => toast.success("qa.yaml saved"),
        onError: (error) => {
          if (!(isAxiosError(error) && error.response?.status === 422)) {
            toast.error("Failed to save qa.yaml");
          }
        },
      },
    );
  }

  function handleDelete() {
    if (
      !window.confirm(
        `Delete the stored qa.yaml for ${repo}? QA treats the repo as unconfigured until a new config is stored (a legacy in-repo .embry0/qa.yaml would re-import as a deprecated fallback).`,
      )
    )
      return;
    deleteMutation.mutate(
      { fullRepo: repo },
      {
        onSuccess: () => {
          setDraft(null);
          toast.success("Stored config deleted");
        },
        onError: () => toast.error("Failed to remove the stored qa.yaml"),
      },
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-white/60">
        Stored qa.yaml (v2) — the embry0 DB is the source of truth; target repos carry no
        config file. Schema-validated on save. Field reference:{" "}
        <code className="text-white/70">docs/qa-yaml-reference.md</code>
      </p>

      {!showEditor ? (
        <EmptyState
          stage="qa"
          icon={FileCode2}
          title="No stored QA config"
          description={
            "embry0 has no stored qa.yaml for this repo, so QA treats it as unconfigured. " +
            "A legacy in-repo .embry0/qa.yaml still auto-imports on the next run (deprecated) — " +
            "storing one here is the supported path."
          }
        >
          <div className="flex items-center justify-center gap-3">
            <Link
              to={`/repos/onboard?repo=${encodeURIComponent(repo!)}`}
              className={cn(buttonVariants({ size: "sm" }))}
              data-testid="qa-config-bootstrap-cta"
            >
              Onboard this repo
            </Link>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setDraft(starterTemplate(repo!))}
              data-testid="qa-config-template-cta"
            >
              Start from template
            </Button>
          </div>
        </EmptyState>
      ) : (
        <div className="space-y-3">
          <Textarea
            value={text}
            onChange={(e) => {
              setDraft(e.target.value);
              if (saveMutation.error) saveMutation.reset();
            }}
            spellCheck={false}
            className="font-mono text-sm min-h-[480px]"
            data-testid="qa-config-textarea"
          />
          {validationDetail && (
            <pre
              data-testid="qa-config-validation-error"
              className="whitespace-pre-wrap rounded-lg border border-destructive/30 bg-destructive/10 p-3 font-mono text-xs text-destructive"
            >
              {validationDetail}
            </pre>
          )}
          {otherSaveError && (
            <p className="text-sm text-destructive" data-testid="qa-config-save-error">
              Save failed — {saveError instanceof Error ? saveError.message : "unknown error"}
            </p>
          )}
          <div className="flex items-center gap-3">
            <Button
              onClick={handleSave}
              disabled={!dirty || !text.trim() || saveMutation.isPending}
              data-testid="qa-config-save"
            >
              {saveMutation.isPending ? "Validating & saving…" : "Save"}
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                setDraft(null);
                saveMutation.reset();
              }}
              disabled={!dirty || saveMutation.isPending}
              data-testid="qa-config-revert"
            >
              Revert
            </Button>
            {data !== null && (
              <Button
                variant="outline"
                className="ml-auto text-destructive hover:text-destructive"
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
                data-testid="qa-config-delete"
              >
                {deleteMutation.isPending ? "Deleting…" : "Delete stored config"}
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
