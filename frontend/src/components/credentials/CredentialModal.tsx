import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { X } from "lucide-react";
import { pollXaiDeviceLogin, startXaiDeviceLogin } from "@/api/credentials";
import { detailMessages } from "@/api/errorDetail";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Tooltip } from "@/components/ui/Tooltip";
import { createFocusTrap } from "@/lib/focus-trap";
import type { Credential, CredentialCreate, CredentialProvider } from "@/lib/types/credentials";

/** Same vocabulary as embry0/api/schemas/credentials.py `_LABEL` (1–64). */
const LABEL_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

const KNOWN_FIELDS = new Set([
  "provider",
  "host",
  "label",
  "secret",
  "git_author_name",
  "git_author_email",
]);

type FieldName = "provider" | "host" | "label" | "secret" | "git_author_name" | "git_author_email";
type FieldErrors = Partial<Record<FieldName, string>>;

export type CredentialModalMode = { mode: "create" } | { mode: "rotate"; credential: Credential };

type AccountKind = "forge_token" | "claude_oauth" | "claude_api_key" | "grok_api_key";

interface DeviceLoginView {
  loginId: string;
  userCode: string;
  verificationUri: string;
  interval: number;
}

export interface CredentialModalProps {
  modeSpec: CredentialModalMode;
  onSubmit(payload: CredentialCreate | { secret: string }): Promise<void>;
  onClose(): void;
  /** The device-login poll creates the row. The page refetches when it finishes. */
  onDeviceLoginDone?(): void;
  gitlabHost: string | null;
}

/**
 * Blank is valid (the field is optional). Otherwise the same rule as
 * `validate_git_author_email` in embry0/api/schemas/repo_preferences.py:
 * an '@' and no whitespace. RepoPreferencesSection does not check this
 * on the client; the server does, and this form matches that check.
 */
function gitAuthorEmailError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (!trimmed.includes("@") || /\s/.test(trimmed)) {
    return "Git author email must contain '@' and no whitespace.";
  }
  return null;
}

function labelError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return "Label is required.";
  if (trimmed.length > 64 || !LABEL_RE.test(trimmed)) {
    return "Label must be 1-64 characters: a letter or digit, then letters, digits, or . _ -.";
  }
  return null;
}

/**
 * 422 detail arrays map onto fields by `loc[-1]`. Anything else (including
 * a string detail) is one form-level line. `input` is never read, so a
 * rejected secret is not copied into the message.
 */
function parseSubmitError(error: unknown): { fields: FieldErrors; form: string | null } {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } }).response;
  const detail = response?.data?.detail;

  if (response?.status === 422 && Array.isArray(detail)) {
    const fields: FieldErrors = {};
    const extra: string[] = [];
    for (const item of detail) {
      if (!item || typeof item !== "object") {
        extra.push("Invalid value");
        continue;
      }
      const rec = item as { loc?: unknown; msg?: unknown };
      const loc = Array.isArray(rec.loc) ? rec.loc : [];
      const name = loc.length > 0 ? String(loc[loc.length - 1]) : "";
      const msg = typeof rec.msg === "string" && rec.msg ? rec.msg : "Invalid value";
      if (name && KNOWN_FIELDS.has(name)) {
        const key = name as FieldName;
        fields[key] = fields[key] ? `${fields[key]} ${msg}` : msg;
      } else {
        extra.push(name ? `${name}: ${msg}` : msg);
      }
    }
    return { fields, form: extra.length > 0 ? extra.join(" ") : null };
  }

  if (typeof detail === "string" && detail.trim()) {
    return { fields: {}, form: detail };
  }
  // A non-422 array is one line. Read `msg` only — never `input`.
  if (Array.isArray(detail)) {
    return { fields: {}, form: detailMessages(detail).join(" ") || "Request failed" };
  }
  return { fields: {}, form: "Request failed" };
}

function FieldAlert({ id, children }: { id: string; children: string }) {
  return (
    <p id={id} role="alert" className="mt-1 text-sm text-destructive">
      {children}
    </p>
  );
}

export function CredentialModal({
  modeSpec,
  onSubmit,
  onClose,
  onDeviceLoginDone,
  gitlabHost,
}: CredentialModalProps) {
  const isRotate = modeSpec.mode === "rotate";
  const containerRef = useRef<HTMLDivElement>(null);
  const aliveRef = useRef(true);
  const pendingRef = useRef(false);
  const onDeviceLoginDoneRef = useRef(onDeviceLoginDone);
  onDeviceLoginDoneRef.current = onDeviceLoginDone;

  const [accountKind, setAccountKind] = useState<AccountKind>("forge_token");
  const [provider, setProvider] = useState<Extract<CredentialProvider, "github" | "gitlab">>("github");
  const [deviceLogin, setDeviceLogin] = useState<DeviceLoginView | null>(null);
  const [deviceStatus, setDeviceStatus] = useState<"pending" | "done" | "expired" | "error" | null>(null);
  const [label, setLabel] = useState("");
  const [gitAuthorName, setGitAuthorName] = useState("");
  const [gitAuthorEmail, setGitAuthorEmail] = useState("");
  // Write-only. Local state only — never a ref, cache, or browser store — and
  // cleared on close, on submit success, and on submit failure.
  const [secret, setSecret] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const requestClose = useCallback(() => {
    setSecret("");
    onClose();
  }, [onClose]);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") requestClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [requestClose]);

  useEffect(() => {
    if (!deviceLogin || deviceStatus !== "pending") return;
    let stopped = false;
    const timer = window.setInterval(() => {
      void pollXaiDeviceLogin(deviceLogin.loginId)
        .then((result) => {
          if (stopped || result.status === "pending") return;
          setDeviceStatus(result.status);
          if (result.status === "done") onDeviceLoginDoneRef.current?.();
        })
        .catch((error: unknown) => {
          if (stopped) return;
          setDeviceStatus("error");
          const parsed = parseSubmitError(error);
          setFormError(parsed.form ?? "Request failed");
        });
    }, deviceLogin.interval * 1000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [deviceLogin, deviceStatus]);

  useEffect(() => {
    if (!containerRef.current) return;
    document.body.style.overflow = "hidden";
    const cleanup = createFocusTrap(containerRef.current);
    return () => {
      cleanup();
      document.body.style.overflow = "";
    };
  }, []);

  function clearField(name: FieldName) {
    setFieldErrors((curr) => {
      if (!curr[name]) return curr;
      const next = { ...curr };
      delete next[name];
      return next;
    });
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (pendingRef.current) return;

    const trimmedSecret = secret.trim();
    const errors: FieldErrors = {};
    if (!trimmedSecret) errors.secret = "Secret is required.";

    if (!isRotate) {
      const labelProblem = labelError(label);
      if (labelProblem) errors.label = labelProblem;
      if (accountKind === "forge_token") {
        const emailProblem = gitAuthorEmailError(gitAuthorEmail);
        if (emailProblem) errors.git_author_email = emailProblem;
        if (provider === "gitlab" && gitlabHost === null) {
          errors.provider = "GitLab needs a configured host.";
        }
      }
    }

    if (Object.keys(errors).length > 0) {
      if (!trimmedSecret) setSecret("");
      setFieldErrors(errors);
      setFormError(null);
      return;
    }

    const payload: CredentialCreate | { secret: string } = isRotate
      ? { secret: trimmedSecret }
      : createPayload(trimmedSecret);

    setFieldErrors({});
    setFormError(null);
    setSecret("");
    pendingRef.current = true;
    setPending(true);
    try {
      await onSubmit(payload);
    } catch (error) {
      if (!aliveRef.current) return;
      const parsed = parseSubmitError(error);
      setFieldErrors(parsed.fields);
      setFormError(parsed.form ?? (Object.keys(parsed.fields).length === 0 ? "Request failed" : null));
    } finally {
      pendingRef.current = false;
      if (aliveRef.current) {
        setSecret("");
        setPending(false);
      }
    }
  }

  async function handleDeviceLogin() {
    if (pendingRef.current) return;
    const labelProblem = labelError(label);
    if (labelProblem) {
      setFieldErrors({ label: labelProblem });
      setFormError(null);
      return;
    }
    pendingRef.current = true;
    setPending(true);
    setFormError(null);
    try {
      const started = await startXaiDeviceLogin(label.trim());
      if (!aliveRef.current) return;
      const seconds = Number.isFinite(started.interval) && started.interval > 0 ? started.interval : 5;
      setDeviceStatus("pending");
      setDeviceLogin({
        loginId: started.login_id,
        userCode: started.user_code,
        verificationUri: started.verification_uri_complete,
        interval: seconds,
      });
    } catch (error) {
      if (!aliveRef.current) return;
      const parsed = parseSubmitError(error);
      setFormError(parsed.form ?? "Request failed");
    } finally {
      pendingRef.current = false;
      if (aliveRef.current) setPending(false);
    }
  }

  function createPayload(trimmedSecret: string): CredentialCreate {
    const trimmedLabel = label.trim();
    if (accountKind === "claude_oauth") {
      return { kind: "llm_oauth", provider: "anthropic", label: trimmedLabel, secret: trimmedSecret };
    }
    if (accountKind === "claude_api_key") {
      return { kind: "llm_api_key", provider: "anthropic", label: trimmedLabel, secret: trimmedSecret };
    }
    if (accountKind === "grok_api_key") {
      return { kind: "llm_api_key", provider: "xai", label: trimmedLabel, secret: trimmedSecret };
    }
    const name = gitAuthorName.trim();
    const email = gitAuthorEmail.trim();
    const body: CredentialCreate = {
      kind: "forge_token",
      provider,
      host: provider === "github" ? "github.com" : (gitlabHost ?? ""),
      label: trimmedLabel,
      secret: trimmedSecret,
    };
    if (name) body.git_author_name = name;
    if (email) body.git_author_email = email;
    return body;
  }

  const host = provider === "github" ? "github.com" : (gitlabHost ?? "");
  const title = isRotate ? "Rotate credential" : "Create credential";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) requestClose();
      }}
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="relative w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-xl"
      >
        <button
          type="button"
          onClick={requestClose}
          aria-label="Close"
          className="absolute top-4 right-4 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
        >
          <X className="h-4 w-4" />
        </button>

        <h2 className="text-section font-semibold mb-5">{title}</h2>

        <form onSubmit={handleSubmit} noValidate autoComplete="off" className="space-y-4">
          {!isRotate && (
            <>
              <div>
                <Label htmlFor="credential-kind">Kind</Label>
                <Select
                  id="credential-kind"
                  className="mt-1"
                  value={accountKind}
                  onChange={(e) => {
                    const value = e.target.value;
                    if (value === "claude_oauth" || value === "claude_api_key" || value === "grok_api_key") {
                      setAccountKind(value);
                    } else {
                      setAccountKind("forge_token");
                    }
                  }}
                >
                  <option value="forge_token">Forge token</option>
                  <option value="claude_oauth">Claude OAuth token</option>
                  <option value="claude_api_key">Claude API key</option>
                  <option value="grok_api_key">Grok API key</option>
                </Select>
              </div>

              {accountKind === "forge_token" && (
              <>
              <div>
                <Label htmlFor="credential-provider">Provider</Label>
                <Select
                  id="credential-provider"
                  className="mt-1"
                  value={provider}
                  aria-describedby={gitlabHost === null ? "credential-provider-hint" : undefined}
                  onChange={(e) => {
                    setProvider(e.target.value === "gitlab" ? "gitlab" : "github");
                    clearField("provider");
                  }}
                >
                  <option value="github">GitHub</option>
                  <option value="gitlab" disabled={gitlabHost === null}>
                    {gitlabHost === null ? "GitLab (no host configured)" : "GitLab"}
                  </option>
                </Select>
                {gitlabHost === null && (
                  <p id="credential-provider-hint" className="mt-1 text-xs text-muted-foreground">
                    GitLab is disabled until a host is configured.
                  </p>
                )}
                {fieldErrors.provider && (
                  <FieldAlert id="credential-provider-error">{fieldErrors.provider}</FieldAlert>
                )}
              </div>

              <div>
                <Label htmlFor="credential-host">Host</Label>
                <Input
                  id="credential-host"
                  value={host}
                  readOnly
                  className="mt-1 font-mono"
                  aria-invalid={fieldErrors.host ? true : undefined}
                  aria-describedby={fieldErrors.host ? "credential-host-error" : undefined}
                />
                {fieldErrors.host && <FieldAlert id="credential-host-error">{fieldErrors.host}</FieldAlert>}
              </div>
              </>
              )}
            </>
          )}

          <div>
            <Label htmlFor="credential-label">Label</Label>
            <Input
              id="credential-label"
              value={isRotate ? modeSpec.credential.label : label}
              onChange={
                isRotate
                  ? undefined
                  : (e) => {
                      setLabel(e.target.value);
                      clearField("label");
                    }
              }
              readOnly={isRotate}
              className="mt-1 font-mono"
              autoComplete="off"
              aria-invalid={fieldErrors.label ? true : undefined}
              aria-describedby={fieldErrors.label ? "credential-label-error" : undefined}
            />
            {fieldErrors.label && <FieldAlert id="credential-label-error">{fieldErrors.label}</FieldAlert>}
          </div>

          {!isRotate && (
            <div className="space-y-2">
              <Button type="button" variant="outline" onClick={() => void handleDeviceLogin()} disabled={pending}>
                Sign in with xAI
              </Button>
              {deviceLogin && (
                <div className="space-y-1 text-sm">
                  <p className="font-mono">{deviceLogin.userCode}</p>
                  <Tooltip content="opens x.ai in a new tab">
                    <a
                      href={deviceLogin.verificationUri}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline"
                    >
                      {deviceLogin.verificationUri}
                    </a>
                  </Tooltip>
                  {deviceStatus === "expired" && <p role="alert">Device login expired.</p>}
                </div>
              )}
            </div>
          )}

          <div>
            <Label htmlFor="credential-secret">{isRotate ? "New secret" : "Secret"}</Label>
            <Input
              id="credential-secret"
              type="password"
              autoComplete="off"
              value={secret}
              disabled={pending}
              onChange={(e) => {
                setSecret(e.target.value);
                clearField("secret");
              }}
              className="mt-1 font-mono"
              aria-invalid={fieldErrors.secret ? true : undefined}
              aria-describedby={fieldErrors.secret ? "credential-secret-error" : undefined}
            />
            {fieldErrors.secret && <FieldAlert id="credential-secret-error">{fieldErrors.secret}</FieldAlert>}
          </div>

          {!isRotate && accountKind === "forge_token" && (
            <>
              <div>
                <Label htmlFor="credential-git-author-name">Git author name</Label>
                <Input
                  id="credential-git-author-name"
                  value={gitAuthorName}
                  onChange={(e) => {
                    setGitAuthorName(e.target.value);
                    clearField("git_author_name");
                  }}
                  className="mt-1"
                  autoComplete="off"
                  aria-invalid={fieldErrors.git_author_name ? true : undefined}
                  aria-describedby={fieldErrors.git_author_name ? "credential-git-author-name-error" : undefined}
                />
                <p className="mt-1 text-xs text-muted-foreground">Optional.</p>
                {fieldErrors.git_author_name && (
                  <FieldAlert id="credential-git-author-name-error">{fieldErrors.git_author_name}</FieldAlert>
                )}
              </div>

              <div>
                <Label htmlFor="credential-git-author-email">Git author email</Label>
                <Input
                  id="credential-git-author-email"
                  value={gitAuthorEmail}
                  onChange={(e) => {
                    setGitAuthorEmail(e.target.value);
                    clearField("git_author_email");
                  }}
                  className="mt-1"
                  autoComplete="off"
                  aria-invalid={fieldErrors.git_author_email ? true : undefined}
                  aria-describedby={fieldErrors.git_author_email ? "credential-git-author-email-error" : undefined}
                />
                <p className="mt-1 text-xs text-muted-foreground">Optional. Must contain @ and no whitespace.</p>
                {fieldErrors.git_author_email && (
                  <FieldAlert id="credential-git-author-email-error">{fieldErrors.git_author_email}</FieldAlert>
                )}
              </div>
            </>
          )}

          {formError && <p role="alert" className="text-sm text-destructive">{formError}</p>}

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" onClick={requestClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {isRotate ? "Rotate" : "Create"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
