import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { ArrowLeft, ArrowRight, Radar, RefreshCw, Search } from "lucide-react";
import {
  isMissingEndpoint,
  probeOrganization,
  registerOrganization,
  type DiscoveryRun,
  type OrgAccess,
  type Organization,
} from "@/api/orgs";
import type { Forge } from "@/api/repos";
import { apiErrorMessage } from "@/api/errorDetail";
import { AccessChips } from "@/components/orgs/AccessChips";
import { DiscoveryStatus } from "@/components/orgs/DiscoveryStatus";
import { OrgActPanel } from "@/components/orgs/OrgActPanel";
import { OrgNeedsList } from "@/components/orgs/OrgNeedsList";
import { OrgReposTable } from "@/components/orgs/OrgReposTable";
import { Button, buttonVariants } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { Select } from "@/components/ui/Select";
import { useCredentials } from "@/hooks/useCredentials";
import {
  useDiscoverOrganization,
  useOrganizationNeeds,
  useOrganizationRepos,
  useOrganizationRuns,
} from "@/hooks/useOrgs";
import { useAuthStore } from "@/stores/authStore";
import { cn } from "@/lib/utils";

const STEPS = ["Connect", "Discover", "Report", "Act"] as const;
type Step = 1 | 2 | 3 | 4;

// A forge owner is one path segment: the server refuses a slash.
const OWNER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/;

type ProbeState =
  | { kind: "idle" }
  | { kind: "ok"; access: OrgAccess }
  | { kind: "missing" }
  | { kind: "error"; message: string };

function errorText(err: unknown): string {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } }).response;
  if (response?.status === 403) return "This needs admin scope.";
  return apiErrorMessage(err);
}

function httpStatus(err: unknown): number | undefined {
  return (err as { response?: { status?: number } }).response?.status;
}

function Stepper({ step }: { step: Step }) {
  return (
    <ol className="flex flex-wrap gap-2" aria-label="Wizard steps">
      {STEPS.map((label, index) => {
        const n = index + 1;
        const state = n < step ? "done" : n === step ? "current" : "todo";
        return (
          <li
            key={label}
            data-testid="wizard-step"
            data-state={state}
            aria-current={state === "current" ? "step" : undefined}
            className={cn(
              "flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs font-medium",
              state === "current" && "border-border bg-primary/10 text-primary",
              state === "done" && "border-success/25 text-success",
              state === "todo" && "border-white/[0.06] text-white/40",
            )}
          >
            <span className="font-mono">{n}</span>
            {label}
          </li>
        );
      })}
    </ol>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3 rounded-lg border border-white/[0.06] bg-card p-4">
      <h2 className="text-section font-semibold">{title}</h2>
      {children}
    </section>
  );
}

/**
 * Add an organization in four steps: connect (owner, forge, credential and
 * a probe), discover (register, then walk every repository), report (what
 * discovery found and what embry0 still needs) and act on those needs.
 */
export function OrgWizardPage() {
  const authMode = useAuthStore((s) => s.authMode);
  const userTier = useAuthStore((s) => s.user?.tier);
  const isAdmin = authMode === "key" || userTier === "admin";

  const [step, setStep] = useState<Step>(1);
  const [ownerInput, setOwnerInput] = useState("");
  const [forge, setForge] = useState<Forge>("github");
  const [credentialId, setCredentialId] = useState("");
  const [probe, setProbe] = useState<ProbeState>({ kind: "idle" });
  const [probing, setProbing] = useState(false);
  const [registered, setRegistered] = useState<Organization | null>(null);
  const [startedRun, setStartedRun] = useState<DiscoveryRun | null>(null);
  const [discoveryStarted, setDiscoveryStarted] = useState(false);
  const [working, setWorking] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);

  const owner = ownerInput.trim().toLowerCase();
  const ownerValid = OWNER_PATTERN.test(owner);

  const credentials = useCredentials();
  const forgeTokens = (credentials.data ?? []).filter(
    (c) => c.kind === "forge_token" && c.provider === forge && c.status === "ok",
  );

  // Starting a run through the hook reads the runs again, so polling picks
  // up the new run on a retry too, not only on the first start.
  const discover = useDiscoverOrganization(owner);
  const runs = useOrganizationRuns(owner, discoveryStarted);
  const repos = useOrganizationRepos(owner, step >= 3);
  const needs = useOrganizationNeeds(owner, step >= 3);

  const run = startedRun ? (runs.data?.find((r) => r.id === startedRun.id) ?? startedRun) : (runs.data?.[0] ?? null);

  const resetConnection = () => {
    setProbe({ kind: "idle" });
    setRegistered(null);
    setStartedRun(null);
    setDiscoveryStarted(false);
    setRegisterError(null);
  };

  const doProbe = async () => {
    setProbing(true);
    try {
      const access = await probeOrganization({ owner, forge, credential_id: credentialId || undefined });
      setProbe({ kind: "ok", access });
    } catch (err) {
      setProbe(isMissingEndpoint(err) ? { kind: "missing" } : { kind: "error", message: errorText(err) });
    } finally {
      setProbing(false);
    }
  };

  const startDiscovery = async () => {
    try {
      const started = await discover.mutateAsync();
      setStartedRun(started);
    } catch (err) {
      // 409: a run is already going for this owner; follow it instead.
      if (httpStatus(err) !== 409) {
        setRegisterError(`Discovery did not start: ${errorText(err)}`);
        return;
      }
      setStartedRun(null);
    }
    setDiscoveryStarted(true);
  };

  const registerAndDiscover = async () => {
    setWorking(true);
    setRegisterError(null);
    try {
      const org = await registerOrganization({ owner, forge, credential_id: credentialId || undefined });
      setRegistered(org);
      await startDiscovery();
    } catch (err) {
      setRegisterError(errorText(err));
    } finally {
      setWorking(false);
    }
  };

  const canLeaveConnect =
    ownerValid && ((probe.kind === "ok" && probe.access.read) || probe.kind === "missing");

  return (
    <div className="min-w-0 max-w-5xl space-y-4">
      <div className="space-y-2">
        <Link to="/orgs" className="text-sm text-white/50 hover:underline">
          ← Organizations
        </Link>
        <h1 className="title-pixel text-xl font-bold">Add organization</h1>
        <Stepper step={step} />
        {!isAdmin && (
          <p className="text-sm text-warning" data-testid="wizard-admin-note">
            Registering, discovering and acting on an organization require admin scope.
          </p>
        )}
      </div>

      {step === 1 && (
        <Section title="Connect">
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="org-owner">Owner</Label>
              <Input
                id="org-owner"
                value={ownerInput}
                onChange={(e) => {
                  setOwnerInput(e.target.value);
                  resetConnection();
                }}
                placeholder="acme"
                autoComplete="off"
                data-testid="wizard-owner"
              />
              {ownerInput.trim() !== "" && !ownerValid && (
                <p className="text-xs text-destructive">One name, no slash: a GitHub organization or user, or a GitLab group.</p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="org-forge">Forge</Label>
              <Select
                id="org-forge"
                value={forge}
                onChange={(e) => {
                  setForge(e.target.value as Forge);
                  setCredentialId("");
                  resetConnection();
                }}
                data-testid="wizard-forge"
              >
                <option value="github">GitHub</option>
                <option value="gitlab">GitLab</option>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="org-credential">Credential</Label>
              <Select
                id="org-credential"
                value={credentialId}
                onChange={(e) => {
                  setCredentialId(e.target.value);
                  resetConnection();
                }}
                data-testid="wizard-credential"
              >
                <option value="">Environment token for this owner</option>
                {forgeTokens.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.label}
                    {c.verified_login ? ` (${c.verified_login})` : ""}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void doProbe()}
              disabled={!ownerValid || probing}
              data-testid="wizard-probe"
            >
              <Search className="h-4 w-4" /> {probing ? "Probing" : "Probe"}
            </Button>
            <span className="text-xs text-muted-foreground">
              Asks the forge what this credential can do at the owner. Nothing is stored.
            </span>
          </div>

          {probe.kind === "ok" && (
            <div className="space-y-2 rounded-md border border-white/[0.06] p-3" data-testid="wizard-probe-result">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <AccessChips access={probe.access} />
                {probe.access.repo_count !== null && (
                  <span data-testid="wizard-probe-count">{probe.access.repo_count} repositories</span>
                )}
                {probe.access.owner_kind && <span className="text-muted-foreground">{probe.access.owner_kind}</span>}
              </div>
              <p className="text-sm text-white/70">{probe.access.detail}</p>
              {!probe.access.read && (
                <p className="text-sm text-destructive">
                  This credential cannot list the owner's repositories, so embry0 cannot maintain them with it.
                </p>
              )}
            </div>
          )}
          {probe.kind === "missing" && (
            <p className="text-sm text-muted-foreground" data-testid="wizard-probe-missing">
              This server has no probe preview yet. The next step probes the owner as it registers it, and refuses one
              the credential cannot list.
            </p>
          )}
          {probe.kind === "error" && (
            <p className="text-sm text-destructive" role="alert" data-testid="wizard-probe-error">
              {probe.message}
            </p>
          )}

          <div className="flex justify-end">
            <Button type="button" size="sm" onClick={() => setStep(2)} disabled={!canLeaveConnect} data-testid="wizard-next-1">
              Next <ArrowRight className="h-4 w-4" />
            </Button>
          </div>
        </Section>
      )}

      {step === 2 && (
        <Section title="Discover">
          <p className="text-sm text-white/70">
            Registers <span className="font-mono">{owner}</span> on{" "}
            {forge === "gitlab" ? "GitLab" : "GitHub"} with{" "}
            {credentialId ? (forgeTokens.find((c) => c.id === credentialId)?.label ?? credentialId) : "the environment token"},
            then reads every repository under it. Registering changes nothing for any repository.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              onClick={() => void registerAndDiscover()}
              disabled={!isAdmin || working || (discoveryStarted && run?.status === "running")}
              data-testid="wizard-register"
            >
              <Radar className="h-4 w-4" />
              {registered ? "Register and discover again" : "Register and discover"}
            </Button>
            {registered && (
              <Link to={`/orgs/${registered.owner}`} className="text-sm text-white/60 hover:underline">
                Organization page
              </Link>
            )}
          </div>
          {registerError && (
            <p className="text-sm text-destructive" role="alert" data-testid="wizard-register-error">
              {registerError}
            </p>
          )}
          {registered && (
            <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="wizard-registered">
              <span>Registered</span>
              <AccessChips access={registered.access} />
            </div>
          )}
          {discoveryStarted &&
            (run ? <DiscoveryStatus owner={owner} run={run} /> : <Skeleton className="h-12 w-full" />)}
          {run?.status === "error" && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void startDiscovery()}
              disabled={!isAdmin || working || discover.isPending}
              data-testid="wizard-retry-discovery"
            >
              <RefreshCw className="h-4 w-4" /> Retry discovery
            </Button>
          )}
          <div className="flex justify-between">
            <Button type="button" variant="ghost" size="sm" onClick={() => setStep(1)} data-testid="wizard-back-2">
              <ArrowLeft className="h-4 w-4" /> Back
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => setStep(3)}
              disabled={!(discoveryStarted && run?.status === "done")}
              data-testid="wizard-next-2"
            >
              Next <ArrowRight className="h-4 w-4" />
            </Button>
          </div>
        </Section>
      )}

      {step === 3 && (
        <Section title="Report">
          <h3 className="text-sm font-semibold">What embry0 needs</h3>
          <OrgNeedsList
            needs={needs.data}
            isLoading={needs.isLoading}
            isError={needs.isError}
            onRetry={() => void needs.refetch()}
          />
          <h3 className="pt-2 text-sm font-semibold">Repositories</h3>
          {repos.isError ? (
            <p className="text-sm text-destructive">Failed to load the repositories.</p>
          ) : repos.data ? (
            <OrgReposTable repos={repos.data} />
          ) : (
            <Skeleton className="h-32 w-full" />
          )}
          <div className="flex justify-between">
            <Button type="button" variant="ghost" size="sm" onClick={() => setStep(2)}>
              <ArrowLeft className="h-4 w-4" /> Back
            </Button>
            <Button type="button" size="sm" onClick={() => setStep(4)} data-testid="wizard-next-3">
              Next <ArrowRight className="h-4 w-4" />
            </Button>
          </div>
        </Section>
      )}

      {step === 4 && (
        <Section title="Act">
          <OrgActPanel owner={owner} needs={needs.data} isAdmin={isAdmin} />
          <div className="flex justify-between">
            <Button type="button" variant="ghost" size="sm" onClick={() => setStep(3)}>
              <ArrowLeft className="h-4 w-4" /> Back
            </Button>
            <Link to={`/orgs/${owner}`} className={cn(buttonVariants({ size: "sm" }))} data-testid="wizard-finish">
              Done
            </Link>
          </div>
        </Section>
      )}
    </div>
  );
}
