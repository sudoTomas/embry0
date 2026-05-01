import { useState, useMemo, useCallback } from "react";
import {
  useGlobalEnv,
  useSetGlobalEnv,
  useRepoEnv,
  useSetRepoEnv,
  useRevealSecret,
} from "@/hooks/useEnvironments";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card } from "@/components/ui/Card";
import { EnvVarTable } from "@/components/environments/EnvVarTable";
import { EnvVarModal } from "@/components/environments/EnvVarModal";
import { DetectBanner } from "@/components/environments/DetectBanner";
import { RepoPreferencesSection } from "@/components/environments/RepoPreferencesSection";
import { TableSkeleton } from "@/components/TableSkeleton";
import { FolderOpen, Plus } from "lucide-react";
import { toast } from "sonner";
import type { EnvVar, EnvVarScope, DetectedEnvVar } from "@/lib/types/environment";

type LocationScope = "global" | "repo";

/**
 * Default an env var to scope=app for backwards compat with rows persisted
 * before Task 11 introduced the scope column.
 */
function effectiveScope(v: EnvVar): EnvVarScope {
  return v.scope ?? "app";
}

function partitionByScope(vars: EnvVar[]): { app: EnvVar[]; qa: EnvVar[] } {
  const app: EnvVar[] = [];
  const qa: EnvVar[] = [];
  for (const v of vars) {
    if (effectiveScope(v) === "qa") {
      qa.push(v);
    } else {
      app.push(v);
    }
  }
  return { app, qa };
}

/**
 * Apply an upsert/delete to the variable list while preserving the explicit
 * scope on every row. The PUT contract is "replace all variables", so we
 * always send a single merged array (App scope first, then QA scope).
 */
function mergeForSave(
  existing: EnvVar[],
  changed: EnvVar | null,
  removeKey: string | null,
): EnvVar[] {
  const withDefaults: EnvVar[] = existing.map((v) => ({ ...v, scope: effectiveScope(v) }));
  let next: EnvVar[] = withDefaults;
  if (removeKey !== null) {
    next = next.filter((v) => v.key !== removeKey);
  }
  if (changed !== null) {
    const stamped: EnvVar = { ...changed, scope: changed.scope ?? "app" };
    next = next.filter((v) => v.key !== stamped.key);
    next.push(stamped);
  }
  // App-scoped first, then QA-scoped.
  return [
    ...next.filter((v) => effectiveScope(v) === "app"),
    ...next.filter((v) => effectiveScope(v) === "qa"),
  ];
}

const APP_DESCRIPTION =
  "Application configuration and secrets injected into all sandboxes.";
const QA_DESCRIPTION =
  "Credentials and tokens used only by the QA agent during validation runs. Never injected into developer or review jobs.";

export function EnvironmentsPage() {
  const [activeTab, setActiveTab] = useState("global");

  const { data: globalVars = [], isLoading: globalLoading } = useGlobalEnv();
  const setGlobalEnv = useSetGlobalEnv();

  const [selectedRepo, setSelectedRepo] = useState("");
  const [repoInput, setRepoInput] = useState("");
  const [owner, repoName] = selectedRepo ? selectedRepo.split("/") : ["", ""];
  const validRepo = !!owner && !!repoName;
  const { data: repoVars = [], isLoading: repoLoading } = useRepoEnv(owner, repoName);
  const setRepoEnv = useSetRepoEnv();
  const [showResolved, setShowResolved] = useState(false);

  const revealMutation = useRevealSecret();
  const [revealedValues, setRevealedValues] = useState<Record<string, string>>({});

  const [editVar, setEditVar] = useState<EnvVar | null | undefined>(undefined);
  const [modalLocation, setModalLocation] = useState<LocationScope>("global");
  const [modalEnvScope, setModalEnvScope] = useState<EnvVarScope>("app");

  const globalParts = useMemo(() => partitionByScope(globalVars), [globalVars]);
  const repoParts = useMemo(() => partitionByScope(repoVars), [repoVars]);

  const handleReveal = useCallback(
    async (location: LocationScope, key: string) => {
      if (key in revealedValues) {
        setRevealedValues((prev) => {
          const next = { ...prev };
          delete next[key];
          return next;
        });
        return;
      }

      try {
        const value = await revealMutation.mutateAsync({
          scope: location,
          key,
          owner: location === "repo" ? owner : undefined,
          repo: location === "repo" ? repoName : undefined,
        });
        setRevealedValues((prev) => ({ ...prev, [key]: value }));
        setTimeout(() => {
          setRevealedValues((prev) => {
            const next = { ...prev };
            delete next[key];
            return next;
          });
        }, 5000);
      } catch {
        toast.error(`Failed to reveal secret "${key}"`);
      }
    },
    [revealMutation, revealedValues, owner, repoName],
  );

  const openGlobalAdd = (envScope: EnvVarScope) => {
    setModalLocation("global");
    setModalEnvScope(envScope);
    setEditVar(null);
  };

  const openGlobalEdit = (v: EnvVar) => {
    setModalLocation("global");
    setModalEnvScope(effectiveScope(v));
    setEditVar(v);
  };

  const handleGlobalSave = (v: EnvVar) => {
    const merged = mergeForSave(globalVars, v, null);
    setGlobalEnv.mutate(merged, {
      onSuccess: () => {
        toast.success(editVar ? `Updated "${v.key}"` : `Added "${v.key}"`);
        setEditVar(undefined);
      },
      onError: (e) => toast.error(`Failed: ${e.message}`),
    });
  };

  const handleGlobalDelete = (key: string) => {
    const merged = mergeForSave(globalVars, null, key);
    setGlobalEnv.mutate(merged, {
      onSuccess: () => toast.success(`Deleted "${key}"`),
      onError: (e) => toast.error(`Failed: ${e.message}`),
    });
  };

  const openRepoAdd = (envScope: EnvVarScope) => {
    setModalLocation("repo");
    setModalEnvScope(envScope);
    setEditVar(null);
  };

  const openRepoEdit = (v: EnvVar) => {
    setModalLocation("repo");
    setModalEnvScope(effectiveScope(v));
    setEditVar(v);
  };

  const handleRepoSave = (v: EnvVar) => {
    const merged = mergeForSave(repoVars, v, null);
    setRepoEnv.mutate(
      { owner, repo: repoName, variables: merged },
      {
        onSuccess: () => {
          toast.success(editVar ? `Updated "${v.key}"` : `Added "${v.key}"`);
          setEditVar(undefined);
        },
        onError: (e) => toast.error(`Failed: ${e.message}`),
      },
    );
  };

  const handleRepoDelete = (key: string) => {
    const merged = mergeForSave(repoVars, null, key);
    setRepoEnv.mutate(
      { owner, repo: repoName, variables: merged },
      {
        onSuccess: () => toast.success(`Deleted "${key}"`),
        onError: (e) => toast.error(`Failed: ${e.message}`),
      },
    );
  };

  const handleImport = (detected: DetectedEnvVar[]) => {
    // Detected env vars are application-config by definition (scanned out of .env*
    // files in the repo). They never land in the QA section.
    const newVars: EnvVar[] = detected.map((d) => ({
      key: d.key,
      value: d.default_value ?? "",
      var_type: d.suggested_type,
      description: d.description,
      required: false,
      scope: "app",
    }));
    const existingKeys = new Set(repoVars.map((v) => v.key));
    const toAdd = newVars.filter((v) => !existingKeys.has(v.key));
    if (toAdd.length === 0) {
      toast.info("All variables are already configured");
      return;
    }
    const stampedExisting = repoVars.map((v) => ({ ...v, scope: effectiveScope(v) }));
    const combined = [...stampedExisting, ...toAdd];
    const ordered = [
      ...combined.filter((v) => effectiveScope(v) === "app"),
      ...combined.filter((v) => effectiveScope(v) === "qa"),
    ];
    setRepoEnv.mutate(
      { owner, repo: repoName, variables: ordered },
      {
        onSuccess: () =>
          toast.success(`Imported ${toAdd.length} variable${toAdd.length !== 1 ? "s" : ""}`),
        onError: (e) => toast.error(`Failed: ${e.message}`),
      },
    );
  };

  /**
   * Resolved view: merge global + repo (only the App-config slice). QA
   * credentials are always evaluated per-location and never participate in
   * the global/repo override chain visualisation.
   */
  const resolvedAppData = useMemo(() => {
    if (!showResolved) {
      return { variables: repoParts.app, sourceMap: {}, overrides: {} };
    }

    const merged = new Map<string, EnvVar>();
    const sourceMap: Record<string, "global" | "repo"> = {};
    const overrides: Record<string, string> = {};

    for (const gv of globalParts.app) {
      merged.set(gv.key, gv);
      sourceMap[gv.key] = "global";
    }

    for (const rv of repoParts.app) {
      if (merged.has(rv.key)) {
        const globalVal = merged.get(rv.key)!;
        if (globalVal.var_type !== "secret") {
          overrides[rv.key] = globalVal.value;
        }
      }
      merged.set(rv.key, rv);
      sourceMap[rv.key] = "repo";
    }

    return {
      variables: Array.from(merged.values()),
      sourceMap,
      overrides,
    };
  }, [globalParts.app, repoParts.app, showResolved]);

  const handleRepoSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = repoInput.trim();
    if (trimmed && trimmed.includes("/")) {
      setSelectedRepo(trimmed);
    } else {
      toast.error("Repository must be in owner/repo format");
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Environments</h1>
      </div>

      <Tabs defaultValue="global" value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="global">Global</TabsTrigger>
          <TabsTrigger value="repo">Repository</TabsTrigger>
        </TabsList>

        <TabsContent value="global">
          <div className="space-y-6 mt-4">
            {/* App Config section */}
            <section className="space-y-3">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-lg font-semibold">App Config</h2>
                  <p className="text-sm text-muted-foreground">{APP_DESCRIPTION}</p>
                </div>
                <Button size="sm" onClick={() => openGlobalAdd("app")}>
                  <Plus className="h-4 w-4 mr-1" />
                  Add Variable
                </Button>
              </div>
              <Card className="p-0">
                {globalLoading ? (
                  <div className="p-4">
                    <TableSkeleton rows={3} />
                  </div>
                ) : (
                  <EnvVarTable
                    variables={globalParts.app}
                    onEdit={openGlobalEdit}
                    onDelete={handleGlobalDelete}
                    onReveal={(key) => handleReveal("global", key)}
                    revealedValues={revealedValues}
                    showRequired
                  />
                )}
              </Card>
            </section>

            {/* QA Test Credentials section */}
            <section className="space-y-3">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-lg font-semibold">QA Test Credentials</h2>
                  <p className="text-sm text-muted-foreground">{QA_DESCRIPTION}</p>
                </div>
                <Button size="sm" onClick={() => openGlobalAdd("qa")}>
                  <Plus className="h-4 w-4 mr-1" />
                  Add QA Variable
                </Button>
              </div>
              <Card className="p-0">
                {globalLoading ? (
                  <div className="p-4">
                    <TableSkeleton rows={2} />
                  </div>
                ) : (
                  <EnvVarTable
                    variables={globalParts.qa}
                    onEdit={openGlobalEdit}
                    onDelete={handleGlobalDelete}
                    onReveal={(key) => handleReveal("global", key)}
                    revealedValues={revealedValues}
                    showRequired
                    emptyState={
                      <div className="flex flex-col items-center justify-center py-12 text-center">
                        <p className="text-white/25 text-sm font-medium">
                          No QA test credentials configured
                        </p>
                        <p className="text-white/[0.12] text-xs mt-1">
                          Add credentials with keys like{" "}
                          <code className="font-mono">QA_TEST_USER</code> for QA agent runs
                        </p>
                      </div>
                    }
                  />
                )}
              </Card>
            </section>
          </div>
        </TabsContent>

        <TabsContent value="repo">
          <div className="space-y-4 mt-4">
            <form onSubmit={handleRepoSubmit} className="flex items-center gap-4 flex-wrap">
              <Input
                value={repoInput}
                onChange={(e) => setRepoInput(e.target.value)}
                placeholder="owner/repo"
                className="max-w-sm"
              />
              <Button type="submit" size="sm" variant="outline">
                Load
              </Button>

              {validRepo && (
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    id="show-resolved"
                    checked={showResolved}
                    onChange={(e) => setShowResolved(e.target.checked)}
                    className="h-4 w-4 rounded border-input"
                  />
                  <label
                    htmlFor="show-resolved"
                    className="text-sm text-muted-foreground cursor-pointer"
                  >
                    Show resolved (App Config)
                  </label>
                </div>
              )}
            </form>

            {validRepo && <DetectBanner owner={owner} repo={repoName} onImport={handleImport} />}

            {validRepo && <RepoPreferencesSection owner={owner} repo={repoName} />}

            {validRepo ? (
              <div className="space-y-6">
                {/* App Config section */}
                <section className="space-y-3">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <h2 className="text-lg font-semibold">App Config</h2>
                      <p className="text-sm text-muted-foreground">{APP_DESCRIPTION}</p>
                    </div>
                    <Button size="sm" onClick={() => openRepoAdd("app")} type="button">
                      <Plus className="h-4 w-4 mr-1" />
                      Add Variable
                    </Button>
                  </div>
                  <Card className="p-0">
                    {repoLoading ? (
                      <div className="p-4">
                        <TableSkeleton rows={3} />
                      </div>
                    ) : (
                      <EnvVarTable
                        variables={
                          showResolved ? resolvedAppData.variables : repoParts.app
                        }
                        onEdit={openRepoEdit}
                        onDelete={handleRepoDelete}
                        onReveal={(key) => handleReveal("repo", key)}
                        revealedValues={revealedValues}
                        showSource={showResolved}
                        showRequired
                        sourceMap={showResolved ? resolvedAppData.sourceMap : undefined}
                        overrides={showResolved ? resolvedAppData.overrides : undefined}
                        emptyState={
                          <div className="flex flex-col items-center justify-center py-16 text-center">
                            <div className="relative">
                              <div className="absolute inset-0 bg-blue-500/5 blur-2xl rounded-full scale-150" />
                              <FolderOpen size={40} className="text-white/10 relative" />
                            </div>
                            <p className="text-white/25 text-sm mt-4 font-medium">
                              No app config variables for this repository
                            </p>
                            <p className="text-white/[0.12] text-xs mt-1">
                              Click &ldquo;Add Variable&rdquo; to set repository-specific overrides
                            </p>
                          </div>
                        }
                      />
                    )}
                  </Card>
                </section>

                {/* QA Test Credentials section */}
                <section className="space-y-3">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <h2 className="text-lg font-semibold">QA Test Credentials</h2>
                      <p className="text-sm text-muted-foreground">{QA_DESCRIPTION}</p>
                    </div>
                    <Button size="sm" onClick={() => openRepoAdd("qa")} type="button">
                      <Plus className="h-4 w-4 mr-1" />
                      Add QA Variable
                    </Button>
                  </div>
                  <Card className="p-0">
                    {repoLoading ? (
                      <div className="p-4">
                        <TableSkeleton rows={2} />
                      </div>
                    ) : (
                      <EnvVarTable
                        variables={repoParts.qa}
                        onEdit={openRepoEdit}
                        onDelete={handleRepoDelete}
                        onReveal={(key) => handleReveal("repo", key)}
                        revealedValues={revealedValues}
                        showRequired
                        emptyState={
                          <div className="flex flex-col items-center justify-center py-12 text-center">
                            <p className="text-white/25 text-sm font-medium">
                              No QA test credentials configured
                            </p>
                            <p className="text-white/[0.12] text-xs mt-1">
                              Add credentials with keys like{" "}
                              <code className="font-mono">QA_TEST_USER</code> for QA agent runs on
                              this repository
                            </p>
                          </div>
                        }
                      />
                    )}
                  </Card>
                </section>
              </div>
            ) : (
              <Card className="p-0">
                <div className="flex flex-col items-center justify-center py-16 text-center">
                  <div className="relative">
                    <div className="absolute inset-0 bg-white/[0.03] blur-2xl rounded-full scale-150" />
                    <FolderOpen size={40} className="text-white/10 relative" />
                  </div>
                  <p className="text-white/25 text-sm mt-4 font-medium">No repository selected</p>
                  <p className="text-white/[0.12] text-xs mt-1">
                    Enter a repository above to manage its environment variables
                  </p>
                </div>
              </Card>
            )}
          </div>
        </TabsContent>
      </Tabs>

      {editVar !== undefined && (
        <EnvVarModal
          variable={editVar}
          envScope={modalEnvScope}
          onSave={modalLocation === "global" ? handleGlobalSave : handleRepoSave}
          onClose={() => setEditVar(undefined)}
        />
      )}
    </div>
  );
}
