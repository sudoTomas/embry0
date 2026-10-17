import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { isAxiosError } from "axios";
import { Check, Plus } from "lucide-react";
import { useBootstrapRepo } from "@/hooks/useBootstrap";
import { useGitHubRepos } from "@/hooks/useGitHub";
import { useSandboxProfiles } from "@/hooks/useSandboxProfiles";
import type { BootstrapRequest, BootstrapResult } from "@/api/bootstrap";
import { RepoSelector } from "@/components/issues/RepoSelector";
import { OnboardAgentForm } from "@/components/repos/OnboardAgentForm";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonVariants } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { cn } from "@/lib/utils";

// Mirrors the backend's BootstrapRequest validators (embry0/api/v1/bootstrap.py)
// so shape errors surface inline instead of as a 422 round-trip.
const REPO_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*$/;
const APP_NAME_PATTERN = /^[a-z0-9][a-z0-9_-]*$/;

type PageState = { kind: "form" } | { kind: "success"; result: BootstrapResult };

export function OnboardRepoPage() {
  const [params] = useSearchParams();
  const bootstrap = useBootstrapRepo();
  const { data: githubRepos } = useGitHubRepos();
  const { data: profiles = [] } = useSandboxProfiles();

  const [state, setState] = useState<PageState>({ kind: "form" });
  // Held here, not in <Tabs>, so "Onboard another repo" returns to the manual tab.
  const [mode, setMode] = useState(() => (params.get("mode") === "manual" ? "manual" : "agent"));
  const [repo, setRepo] = useState(() => params.get("repo") ?? "");
  const [app, setApp] = useState("");
  const [bootCommand, setBootCommand] = useState("");
  const [frontendUrl, setFrontendUrl] = useState("");
  const [readyCheckUrl, setReadyCheckUrl] = useState("");
  const [sandboxProfile, setSandboxProfile] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [forceDetail, setForceDetail] = useState<string | null>(null);

  const repoOptions = githubRepos?.repos.map((r) => r.full_name) ?? [];
  const pending = bootstrap.isPending;

  function buildRequest(force: boolean): BootstrapRequest {
    return {
      repo: repo.trim(),
      app: app.trim(),
      boot_command: bootCommand.trim(),
      frontend_url: frontendUrl.trim(),
      ready_check_url: readyCheckUrl.trim(),
      sandbox_profile: sandboxProfile,
      force,
    };
  }

  function submit(force: boolean) {
    setFieldError(null);
    setServerError(null);
    if (!force) setForceDetail(null);

    if (!REPO_PATTERN.test(repo.trim())) {
      setFieldError("Repo must be in owner/name form.");
      return;
    }
    if (app.trim() && !APP_NAME_PATTERN.test(app.trim())) {
      setFieldError("App must be a lowercase slug (a-z, 0-9, -, _).");
      return;
    }

    bootstrap.mutate(buildRequest(force), {
      onSuccess: (result) => {
        setForceDetail(null);
        setState({ kind: "success", result });
      },
      onError: (error) => {
        const detail =
          isAxiosError(error) && typeof error.response?.data?.detail === "string"
            ? error.response.data.detail
            : null;
        if (detail && detail.includes("already exists")) {
          setForceDetail(detail);
        } else {
          setServerError(detail ?? (error instanceof Error ? error.message : "Bootstrap failed"));
        }
      },
    });
  }

  function resetForm() {
    setState({ kind: "form" });
    setRepo("");
    setApp("");
    setBootCommand("");
    setFrontendUrl("");
    setReadyCheckUrl("");
    setSandboxProfile("");
    setFieldError(null);
    setServerError(null);
    setForceDetail(null);
    bootstrap.reset();
  }

  if (state.kind === "success") {
    const { result } = state;
    const enc = encodeURIComponent(result.repo);
    return (
      <div className="space-y-4 p-6 max-w-2xl">
        <h1 className="text-2xl font-bold">Repo onboarded</h1>
        <Card className="p-4 space-y-4" data-testid="bootstrap-success">
          <div className="space-y-1 text-sm">
            <p className="font-medium">{result.repo}</p>
            <p className="text-white/60">
              {result.default_branch && (
                <>
                  default branch <code className="text-white/80">{result.default_branch}</code>
                  {" · "}
                </>
              )}
              {result.private !== null && (
                <Badge>{result.private ? "private" : "public"}</Badge>
              )}{" "}
              <Badge tone="info">qa_required: {result.qa_required}</Badge>
              {result.app && <> · app <code className="text-white/80">{result.app}</code></>}
              {result.sandbox_profile && (
                <> · profile <code className="text-white/80">{result.sandbox_profile}</code></>
              )}
            </p>
            <p className="font-mono text-xs text-white/50">{result.qa_config_path}</p>
          </div>
          <ul className="space-y-1.5" data-testid="bootstrap-steps">
            {result.steps.map((step) => (
              <li key={step} className="flex items-start gap-2 text-sm text-white/80">
                <Check className="h-4 w-4 mt-0.5 shrink-0 text-primary" />
                {step}
              </li>
            ))}
          </ul>
        </Card>
        <div className="flex items-center gap-3">
          <Link
            to={`/qa/repos/${enc}/config`}
            className={cn(buttonVariants({ size: "sm" }))}
            data-testid="bootstrap-edit-config-cta"
          >
            Edit QA config
          </Link>
          <Link
            to={`/qa/repos/${enc}`}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
            data-testid="bootstrap-view-qa-cta"
          >
            View QA runs
          </Link>
          <Link
            to={`/repos/${result.repo}`}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
            data-testid="bootstrap-repo-overview-cta"
          >
            Repo overview — preferences & environment
          </Link>
          <Button variant="ghost" size="sm" onClick={resetForm}>
            <Plus className="h-4 w-4" /> Onboard another repo
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 p-6 max-w-2xl">
      <Link to="/repos" className="text-sm text-white/50 hover:underline">
        ← Repos
      </Link>
      <h1 className="text-2xl font-bold">Onboard a repo</h1>
      <p className="text-sm text-white/60">
        Stores the repo&apos;s QA config (qa.yaml v2) in embry0. Let the onboarding agent work it out
        from the code, or write a starter config yourself. Everything is refinable in the QA config
        editor afterward.
      </p>

      <Tabs defaultValue="agent" value={mode} onValueChange={setMode} className="space-y-3">
        <TabsList>
          <TabsTrigger value="agent">Analyze with the onboarding agent</TabsTrigger>
          <TabsTrigger value="manual">Configure manually</TabsTrigger>
        </TabsList>
        <TabsContent value="agent">
          <OnboardAgentForm initialRepo={params.get("repo") ?? ""} />
        </TabsContent>
        <TabsContent value="manual">
          <p className="text-sm text-white/60 mb-4">
            Verifies the repo, registers a starter qa.yaml (v2), and optionally seeds sandbox
            preferences.
          </p>
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              submit(false);
            }}
          >
            <Card className="p-4 space-y-3">
              <div>
                <h3 className="text-sm font-semibold">Repository</h3>
                <p className="text-xs text-muted-foreground mt-1">
                  The manual form verifies the repo through the GitHub API, so it takes GitHub repos
                  only. Onboard a GitLab repo with the onboarding agent.
                </p>
              </div>
              <div className="space-y-1">
                <Label htmlFor="onboard-repo">Repo (owner/name)</Label>
                <RepoSelector value={repo} onChange={setRepo} repos={repoOptions} placeholder="owner/name" />
              </div>
            </Card>

            <Card className="p-4 space-y-3">
              <div>
                <h3 className="text-sm font-semibold">App (optional)</h3>
                <p className="text-xs text-muted-foreground mt-1">
                  Declare the app QA should boot. Leave blank to register a config with{" "}
                  <code>qa_required: never</code> — QA skips cleanly until an app is declared.
                </p>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label htmlFor="onboard-app">App name</Label>
                  <Input
                    id="onboard-app"
                    value={app}
                    onChange={(e) => setApp(e.target.value)}
                    placeholder="web"
                    disabled={pending}
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="onboard-boot">Boot command</Label>
                  <Input
                    id="onboard-boot"
                    className="font-mono"
                    value={bootCommand}
                    onChange={(e) => setBootCommand(e.target.value)}
                    placeholder="npm ci && npm run dev"
                    disabled={pending}
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="onboard-frontend-url">Frontend URL</Label>
                  <Input
                    id="onboard-frontend-url"
                    value={frontendUrl}
                    onChange={(e) => setFrontendUrl(e.target.value)}
                    placeholder="http://localhost:3000"
                    disabled={pending}
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="onboard-ready-url">Ready-check URL</Label>
                  <Input
                    id="onboard-ready-url"
                    value={readyCheckUrl}
                    onChange={(e) => setReadyCheckUrl(e.target.value)}
                    placeholder="defaults to the frontend URL"
                    disabled={pending}
                  />
                </div>
              </div>
            </Card>

            <Card className="p-4 space-y-3">
              <div>
                <h3 className="text-sm font-semibold">Sandbox (optional)</h3>
                <p className="text-xs text-muted-foreground mt-1">
                  Seed a sandbox-profile preference for this repo. Leave as default to let triage pick.
                </p>
              </div>
              <div className="space-y-1">
                <Label htmlFor="onboard-profile">Sandbox profile</Label>
                <Select
                  id="onboard-profile"
                  value={sandboxProfile}
                  onChange={(e) => setSandboxProfile(e.target.value)}
                  disabled={pending}
                >
                  <option value="">(default)</option>
                  {profiles.map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.name}
                    </option>
                  ))}
                </Select>
              </div>
            </Card>

            {fieldError && (
              <p className="text-sm text-destructive" data-testid="bootstrap-field-error">
                {fieldError}
              </p>
            )}
            {serverError && (
              <p className="text-sm text-destructive" data-testid="bootstrap-error">
                {serverError}
              </p>
            )}
            {forceDetail && (
              <div
                className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 space-y-2"
                data-testid="bootstrap-force-panel"
              >
                <p className="text-sm text-amber-200">{forceDetail}</p>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    onClick={() => submit(true)}
                    disabled={pending}
                    data-testid="bootstrap-force-overwrite"
                  >
                    Overwrite
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setForceDetail(null)}
                    disabled={pending}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            )}

            <Button type="submit" disabled={pending || !repo.trim()} data-testid="bootstrap-submit">
              {pending ? "Bootstrapping… (contacting GitHub)" : "Bootstrap repo"}
            </Button>
          </form>
        </TabsContent>
      </Tabs>
    </div>
  );
}
