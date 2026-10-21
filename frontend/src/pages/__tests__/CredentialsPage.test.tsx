// frontend/src/pages/__tests__/CredentialsPage.test.tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { useAuthStore } from "@/stores/authStore";

const mockCreate = vi.fn(); const mockImport = vi.fn(); const mockVerify = vi.fn();
const creds = [{ id: "cred-000000000001", kind: "forge_token", provider: "github", host: "github.com", label: "alice",
  git_author_name: null, git_author_email: null, masked_preview: "ghp_••••••••mnop", verified_login: null, status: "ok",
  status_reason: null, quarantined_until: null, binding_count: 0, created_at: null, updated_at: null, rotated_at: null, last_used_at: null }];
vi.mock("@/hooks/useCredentials", () => ({
  useCredentials: () => ({ data: creds, isLoading: false, isError: false }),
  useCreateCredential: () => ({ mutateAsync: mockCreate, isPending: false }),
  useUpdateCredential: () => ({ mutate: vi.fn(), isPending: false }),
  useRotateCredential: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useVerifyCredential: () => ({ mutate: mockVerify, isPending: false }),
  useDeleteCredential: () => ({ mutate: vi.fn(), isPending: false }),
  useImportEnvCredentials: () => ({ mutateAsync: mockImport, isPending: false }),
  useScopedBindings: () => ({ data: [], isLoading: false }),
  useSetScopedBindings: () => ({ mutate: vi.fn(), isPending: false }),
}));
let catalogProviders: { name: string }[] | undefined;
vi.mock("@/hooks/useProvider", () => ({
  useModelsCatalog: () => ({ data: catalogProviders ? { providers: catalogProviders } : undefined }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
import { CredentialsPage } from "../CredentialsPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><CredentialsPage /></MemoryRouter></QueryClientProvider>);
}
const asTier = (tier: string) => useAuthStore.setState({ status: "authenticated", authMode: "session", user: { email: "u@example.com", name: "U", tier }, ssoEnabled: true, apiKeyLogin: true });

describe("CredentialsPage", () => {
  beforeEach(() => { mockCreate.mockReset(); mockImport.mockReset(); catalogProviders = undefined; });

  it("renders read-only for operator tier", () => {
    asTier("operator"); renderPage();
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add credential/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /import from environment/i })).toBeDisabled();
    expect(screen.getByTestId("credential-rotate")).toBeDisabled();
  });

  it("admin can open the create modal and submit", async () => {
    asTier("admin"); mockCreate.mockResolvedValue(creds[0]); const user = userEvent.setup(); renderPage();
    await user.click(screen.getByRole("button", { name: /add credential/i }));
    await user.type(screen.getByLabelText(/label/i), "bob");
    await user.type(screen.getByLabelText(/^secret/i), "ghp_abcdefghijklmnop");
    await user.click(screen.getByRole("button", { name: /^create/i }));
    expect(mockCreate).toHaveBeenCalledWith(expect.objectContaining({ label: "bob", secret: "ghp_abcdefghijklmnop" }));
  });

  it("offers the LLM providers this deployment's catalog enables", async () => {
    asTier("admin"); mockCreate.mockResolvedValue(creds[0]); const user = userEvent.setup();
    catalogProviders = [{ name: "anthropic" }, { name: "xai" }, { name: "groq" }];
    renderPage();
    await user.click(screen.getByRole("button", { name: /add credential/i }));
    await user.selectOptions(screen.getByLabelText(/^kind/i), "llm_api_key");
    expect(screen.getByRole("option", { name: "Groq" })).toBeEnabled();
    expect(screen.getByRole("option", { name: "OpenAI (not enabled)" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText(/^provider/i), "groq");
    await user.type(screen.getByLabelText(/label/i), "groq-key");
    await user.type(screen.getByLabelText(/^secret/i), "gsk-placeholder");
    await user.click(screen.getByRole("button", { name: /^create/i }));
    expect(mockCreate).toHaveBeenCalledWith({ kind: "llm_api_key", provider: "groq", label: "groq-key", secret: "gsk-placeholder" });
  });

  it("secret field is cleared when the modal closes", async () => {
    asTier("admin"); const user = userEvent.setup(); renderPage();
    await user.click(screen.getByRole("button", { name: /add credential/i }));
    await user.type(screen.getByLabelText(/^secret/i), "ghp_abcdefghijklmnop");
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: /add credential/i }));
    expect(screen.getByLabelText(/^secret/i)).toHaveValue("");
  });

  it("import shows the report", async () => {
    asTier("admin"); mockImport.mockResolvedValue({ created: ["github/acme"], bound: ["owner:github:acme:forge -> github/acme"], skipped: [] });
    const user = userEvent.setup(); renderPage();
    await user.click(screen.getByRole("button", { name: /import from environment/i }));
    expect(await screen.findByText("github/acme")).toBeInTheDocument();
    expect(mockImport).toHaveBeenCalledWith(false);
  });
});
