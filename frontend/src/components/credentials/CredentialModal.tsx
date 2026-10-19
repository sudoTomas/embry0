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
import { ENABLE_SETTING, isLlmProvider, llmProviderStates, type LlmProviderName } from "@/lib/llmProviders";
import { providerLabel } from "@/lib/status";
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

type FieldName = "provider" | "host" | "label" | "secret" | "git_author_name" | "git_author_email" | "base_url";
type FieldErrors = Partial<Record<FieldName, string>>;

export type CredentialModalMode = { mode: "create" } | { mode: "rotate"; credential: Credential };

type AccountKind = "forge_token" | "claude_oauth" | "llm_api_key";

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
  /** GET /config/models providers: the LLM providers this deployment enables.
   * Undefined until it answers, when the backend decides on save. */
  catalogProviders?: readonly { name: string }[];
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

function parseUrl(value: string): URL | null {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

/**
 * A custom provider's API root, by the backend's rule (`_custom_base_url` in
 * embry0/api/schemas/credentials.py): an http(s) URL with a host, and no
 * credentials, query or fragment. The server checks it again.
 */
function baseUrlError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return "Base URL is required for a custom provider.";
  const url = parseUrl(trimmed);
  if (!url || (url.protocol !== "http:" && url.protocol !== "https:") || !url.hostname) {
    return "Base URL must be an http(s) URL with a host, e.g. http://10.0.0.5:11434/v1.";
  }
  if (url.username || url.password || url.search || url.hash) {
    return "Base URL must not carry credentials, a query or a fragment.";
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
  catalogProviders,
}: CredentialModalProps) {
  const isRotate = modeSpec.mode === "rotate";
  const containerRef = useRef<HTMLDivElement>(null);
  const aliveRef = useRef(true);
  const pendingRef = useRef(false);
  const onDeviceLoginDoneRef = useRef(onDeviceLoginDone);
  onDeviceLoginDoneRef.current = onDeviceLoginDone;

  const [accountKind, setAccountKind] = useState<AccountKind>("forge_token");
  const [provider, setProvider] = useState<Extract<CredentialProvider, "github" | "gitlab">>("github");
  const [llmProvider, setLlmProvider] = useState<LlmProviderName>("anthropic");
  const [baseUrl, setBaseUrl] = useState("");
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

  const llmStates = llmProviderStates(catalogProviders);
  const notEnabled = llmStates.filter((entry) => entry.state === "not_enabled").map((entry) => entry.name);
  // The device login creates an xAI llm_oauth account (POST /credentials/xai/device-login),
  // so it is offered only while the form is making an xAI account.
  const offersXaiSignIn = !isRotate && accountKind === "llm_api_key" && llmProvider === "xai";

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
      if (accountKind === "llm_api_key") {
        if (notEnabled.includes(llmProvider)) {
          errors.provider = `${providerLabel(llmProvider)} is not enabled in this deployment.`;
        }
        if (llmProvider === "custom") {
          const urlProblem = baseUrlError(baseUrl);
          if (urlProblem) errors.base_url = urlProblem;
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
    if (accountKind === "llm_api_key") {
      const body: CredentialCreate = {
        kind: "llm_api_key",
        provider: llmProvider,
        label: trimmedLabel,
        secret: trimmedSecret,
      };
      if (llmProvider === "custom") body.metadata = { base_url: baseUrl.trim() };
      return body;
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
                    if (value === "claude_oauth" || value === "llm_api_key") {
                      setAccountKind(value);
                    } else {
                      setAccountKind("forge_token");
                    }
                    clearField("provider");
                  }}
                >
                  <option value="forge_token">Forge token</option>
                  <option value="llm_api_key">LLM API key</option>
                  <option value="claude_oauth">Claude OAuth token</option>
                </Select>
              </div>

              {accountKind === "llm_api_key" && (
              <>
              <div>
                <Label htmlFor="credential-llm-provider">Provider</Label>
                <Select
                  id="credential-llm-provider"
                  className="mt-1"
                  value={llmProvider}
                  aria-invalid={fieldErrors.provider ? true : undefined}
                  aria-describedby={
                    [
                      notEnabled.length > 0 ? "credential-llm-provider-hint" : "",
                      fieldErrors.provider ? "credential-llm-provider-error" : "",
                    ]
                      .filter(Boolean)
                      .join(" ") || undefined
                  }
                  onChange={(e) => {
                    const value = e.target.value;
                    if (isLlmProvider(value)) setLlmProvider(value);
                    clearField("provider");
                    clearField("base_url");
                  }}
                >
                  {llmStates.map(({ name, state }) => (
                    <option key={name} value={name} disabled={state === "not_enabled"}>
                      {state === "not_enabled" ? `${providerLabel(name)} (not enabled)` : providerLabel(name)}
                    </option>
                  ))}
                </Select>
                {notEnabled.length > 0 && (
                  <p id="credential-llm-provider-hint" className="mt-1 text-xs text-muted-foreground">
                    Not enabled in this deployment: {notEnabled.map(providerLabel).join(", ")}. Name a provider in{" "}
                    <span className="font-mono">{ENABLE_SETTING}</span> and restart the orchestrator to add its key.
                  </p>
                )}
                {fieldErrors.provider && (
                  <FieldAlert id="credential-llm-provider-error">{fieldErrors.provider}</FieldAlert>
                )}
              </div>

              {llmProvider === "custom" && (
                <div>
                  <Label htmlFor="credential-base-url">Base URL</Label>
                  <Input
                    id="credential-base-url"
                    value={baseUrl}
                    onChange={(e) => {
                      setBaseUrl(e.target.value);
                      clearField("base_url");
                    }}
                    placeholder="http://10.0.0.5:11434/v1"
                    className="mt-1 font-mono"
                    autoComplete="off"
                    aria-invalid={fieldErrors.base_url ? true : undefined}
                    aria-describedby={
                      fieldErrors.base_url ? "credential-base-url-hint credential-base-url-error" : "credential-base-url-hint"
                    }
                  />
                  <p id="credential-base-url-hint" className="mt-1 text-xs text-muted-foreground">
                    The root of the server&apos;s OpenAI-compatible API. A server that checks no key still needs a
                    placeholder secret.
                  </p>
                  {fieldErrors.base_url && (
                    <FieldAlert id="credential-base-url-error">{fieldErrors.base_url}</FieldAlert>
                  )}
                </div>
              )}
              </>
              )}

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

          {(offersXaiSignIn || deviceLogin) && (
            <div className="space-y-2">
              {offersXaiSignIn && (
                <>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void handleDeviceLogin()}
                    disabled={pending}
                    aria-describedby="credential-xai-sign-in-hint"
                  >
                    Sign in with xAI
                  </Button>
                  <p id="credential-xai-sign-in-hint" className="text-xs text-muted-foreground">
                    Or sign in instead of pasting a key: that stores an xAI OAuth account under this label.
                  </p>
                </>
              )}
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
