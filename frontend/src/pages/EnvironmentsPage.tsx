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
import { TableSkeleton } from "@/components/ui/PageSkeleton";
import { FolderOpen, Plus } from "lucide-react";
import { toast } from "sonner";
import type { EnvVar, DetectedEnvVar } from "@/lib/types/environment";

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
  const [modalScope, setModalScope] = useState<"global" | "repo">("global");

  const handleReveal = useCallback(
    async (scope: "global" | "repo", key: string) => {
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
          scope,
          key,
          owner: scope === "repo" ? owner : undefined,
          repo: scope === "repo" ? repoName : undefined,
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

  const openGlobalAdd = () => {
    setModalScope("global");
    setEditVar(null);
  };

  const openGlobalEdit = (v: EnvVar) => {
    setModalScope("global");
    setEditVar(v);
  };

  const handleGlobalSave = (v: EnvVar) => {
    const existing = globalVars.filter((e) => e.key !== v.key);
    setGlobalEnv.mutate([...existing, v], {
      onSuccess: () => {
        toast.success(editVar ? `Updated "${v.key}"` : `Added "${v.key}"`);
        setEditVar(undefined);
      },
      onError: (e) => toast.error(`Failed: ${e.message}`),
    });
  };

  const handleGlobalDelete = (key: string) => {
    const remaining = globalVars.filter((v) => v.key !== key);
    setGlobalEnv.mutate(remaining, {
      onSuccess: () => toast.success(`Deleted "${key}"`),
      onError: (e) => toast.error(`Failed: ${e.message}`),
    });
  };

  const openRepoAdd = () => {
    setModalScope("repo");
    setEditVar(null);
  };

  const openRepoEdit = (v: EnvVar) => {
    setModalScope("repo");
    setEditVar(v);
  };

  const handleRepoSave = (v: EnvVar) => {
    const existing = repoVars.filter((e) => e.key !== v.key);
    setRepoEnv.mutate(
      { owner, repo: repoName, variables: [...existing, v] },
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
    const remaining = repoVars.filter((v) => v.key !== key);
    setRepoEnv.mutate(
      { owner, repo: repoName, variables: remaining },
      {
        onSuccess: () => toast.success(`Deleted "${key}"`),
        onError: (e) => toast.error(`Failed: ${e.message}`),
      },
    );
  };

  const handleImport = (detected: DetectedEnvVar[]) => {
    const newVars: EnvVar[] = detected.map((d) => ({
      key: d.key,
      value: d.default_value ?? "",
      var_type: d.suggested_type,
      description: d.description,
      required: false,
    }));
    const existingKeys = new Set(repoVars.map((v) => v.key));
    const toAdd = newVars.filter((v) => !existingKeys.has(v.key));
    if (toAdd.length === 0) {
      toast.info("All variables are already configured");
      return;
    }
    setRepoEnv.mutate(
      { owner, repo: repoName, variables: [...repoVars, ...toAdd] },
      {
        onSuccess: () => toast.success(`Imported ${toAdd.length} variable${toAdd.length !== 1 ? "s" : ""}`),
        onError: (e) => toast.error(`Failed: ${e.message}`),
      },
    );
  };

  const resolvedData = useMemo(() => {
    if (!showResolved) return { variables: repoVars, sourceMap: {}, overrides: {} };

    const merged = new Map<string, EnvVar>();
    const sourceMap: Record<string, "global" | "repo"> = {};
    const overrides: Record<string, string> = {};

    for (const gv of globalVars) {
      merged.set(gv.key, gv);
      sourceMap[gv.key] = "global";
    }

    for (const rv of repoVars) {
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
  }, [globalVars, repoVars, showResolved]);

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
          <div className="space-y-4 mt-4">
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                Global variables are available to all repositories unless overridden.
              </p>
              <Button size="sm" onClick={openGlobalAdd}>
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
                  variables={globalVars}
                  onEdit={openGlobalEdit}
                  onDelete={handleGlobalDelete}
                  onReveal={(key) => handleReveal("global", key)}
                  revealedValues={revealedValues}
                  showRequired
                />
              )}
            </Card>
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
                <>
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
                      Show resolved
                    </label>
                  </div>

                  <div className="flex-1" />

                  <Button size="sm" onClick={openRepoAdd} type="button">
                    <Plus className="h-4 w-4 mr-1" />
                    Add Variable
                  </Button>
                </>
              )}
            </form>

            {validRepo && <DetectBanner owner={owner} repo={repoName} onImport={handleImport} />}

            {validRepo && <RepoPreferencesSection owner={owner} repo={repoName} />}

            {validRepo ? (
              <Card className="p-0">
                {repoLoading ? (
                  <div className="p-4">
                    <TableSkeleton rows={3} />
                  </div>
                ) : (
                  <EnvVarTable
                    variables={showResolved ? resolvedData.variables : repoVars}
                    onEdit={openRepoEdit}
                    onDelete={handleRepoDelete}
                    onReveal={(key) => handleReveal("repo", key)}
                    revealedValues={revealedValues}
                    showSource={showResolved}
                    showRequired
                    sourceMap={showResolved ? resolvedData.sourceMap : undefined}
                    overrides={showResolved ? resolvedData.overrides : undefined}
                    emptyState={
                      <div className="flex flex-col items-center justify-center py-16 text-center">
                        <div className="relative">
                          <div className="absolute inset-0 bg-blue-500/5 blur-2xl rounded-full scale-150" />
                          <FolderOpen size={40} className="text-white/10 relative" />
                        </div>
                        <p className="text-white/25 text-sm mt-4 font-medium">
                          No variables for this repository
                        </p>
                        <p className="text-white/[0.12] text-xs mt-1">
                          Click &ldquo;Add Variable&rdquo; to set repository-specific overrides
                        </p>
                      </div>
                    }
                  />
                )}
              </Card>
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
          onSave={modalScope === "global" ? handleGlobalSave : handleRepoSave}
          onClose={() => setEditVar(undefined)}
        />
      )}
    </div>
  );
}
