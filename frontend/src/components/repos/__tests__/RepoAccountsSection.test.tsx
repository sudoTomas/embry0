import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "@/stores/authStore";
import { RepoAccountsSection } from "../RepoAccountsSection";

const mockSet = vi.fn();
let repoCreds: unknown;
let credentialRows: Array<Record<string, unknown>>;
let catalogProviders: { name: string; available?: boolean }[] | undefined;

const forgeRows = () => [
  {
    id: "cred-000000000001",
    label: "alice",
    provider: "github",
    status: "ok",
    masked_preview: "ghp_placeholdersecret",
  },
  { id: "cred-000000000002", label: "gl", provider: "gitlab", status: "ok" },
];

vi.mock("@/hooks/useCredentials", () => ({
  useRepoCredentials: () => ({ data: repoCreds, isLoading: false, isError: false }),
  useSetRepoCredentials: () => ({ mutate: mockSet, isPending: false }),
  useCredentials: () => ({
    data: credentialRows,
    isLoading: false,
  }),
}));

vi.mock("@/hooks/useProvider", () => ({
  useModelsCatalog: () => ({ data: catalogProviders ? { providers: catalogProviders } : undefined }),
}));

function renderSection(repo: string, forge: "github" | "gitlab") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <RepoAccountsSection repo={repo} forge={forge} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const asTier = (tier: string) =>
  useAuthStore.setState({
    status: "authenticated",
    authMode: "session",
    user: { email: "u@example.com", name: "U", tier },
    ssoEnabled: true,
    apiKeyLogin: true,
  });

const GITHUB_PICKER = /^GitHub account embry0 uses for this repository$/;
const NONE_LABEL = "Use the shared account (recommended)";
const ALL_PROVIDERS = ["anthropic", "xai", "openai", "openrouter", "google", "groq", "deepseek", "custom"];

/** The provider rows on screen, by name, in page order. */
function providerRows(): string[] {
  return ALL_PROVIDERS.filter((name) => screen.queryByTestId(`llm-${name}`));
}

/** Every provider this deployment enables; only Anthropic and xAI hold an account. */
function catalogWithTwoAccounts() {
  return ALL_PROVIDERS.map((name) => ({ name, available: name === "anthropic" || name === "xai" }));
}

const llmBinding = {
  credential_id: "cred-000000000009",
  role: "llm" as const,
  pinned: true,
  priority: 0,
};

describe("RepoAccountsSection", () => {
  beforeEach(() => {
    mockSet.mockReset();
    repoCreds = undefined;
    credentialRows = forgeRows();
    catalogProviders = undefined;
    asTier("operator");
  });

  it("shows the effective account with its trace and source", () => {
    repoCreds = {
      repo: "acme/widgets",
      bindings: [],
      effective: {
        forge: {
          id: "cred-000000000001",
          label: "alice",
          provider: "github",
          source: "registry",
          trace: ["repo:acme/widgets -> none", "owner:github:acme -> alice"],
        },
      },
    };
    asTier("operator");
    renderSection("acme/widgets", "github");
    // The select lists the same label, so the card is the effective account.
    const card = screen.getByRole("heading", { name: "Effective forge account" }).parentElement;
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).getByText("alice")).toBeInTheDocument();
    const source = screen.getByText("registry");
    expect(source).toBeInTheDocument();
    expect(source.className).toContain("text-success");
    const trace = screen.getByTestId("repo-accounts-trace");
    expect(trace.tagName).toBe("DETAILS");
    expect(trace).toHaveTextContent("owner:github:acme -> alice");
    expect(screen.queryByText("ghp_placeholdersecret")).not.toBeInTheDocument();
  });

  it("shows the missing-credential message", () => {
    repoCreds = {
      repo: "acme/widgets",
      bindings: [],
      effective: {
        forge: {
          error: "no github credential for 'acme/widgets': bind one in the registry or set GITHUB_TOKEN",
        },
      },
    };
    asTier("operator");
    renderSection("acme/widgets", "github");
    expect(screen.getByTestId("repo-accounts-error")).toHaveTextContent(/bind one in the registry/);
  });

  it("does not crash when the forge credential is missing", () => {
    repoCreds = { repo: "acme/widgets", bindings: [], effective: {} };
    asTier("operator");
    renderSection("acme/widgets", "github");
    expect(screen.getByLabelText(GITHUB_PICKER)).toBeInTheDocument();
  });

  it("marks an env source as coming from the server's settings", () => {
    repoCreds = {
      repo: "acme/widgets",
      bindings: [],
      effective: {
        forge: {
          id: null,
          label: "env-token",
          provider: "github",
          source: "env",
          trace: ["env:GITHUB_TOKEN"],
        },
      },
    };
    asTier("operator");
    renderSection("acme/widgets", "github");
    const badge = screen.getByText("env");
    expect(badge).toHaveAttribute("title", "Comes from the server's settings, not the account list");
    expect(badge.className).toContain("text-warning");
  });

  it("admin binds a credential of the repo's forge only", async () => {
    repoCreds = {
      repo: "acme/widgets",
      bindings: [llmBinding],
      effective: { forge: { error: "x" } },
    };
    asTier("admin");
    const user = userEvent.setup();
    renderSection("acme/widgets", "github");
    const select = screen.getByLabelText(GITHUB_PICKER);
    expect(screen.queryByRole("option", { name: "gl" })).toBeNull();
    expect(within(select).getByRole("option", { name: NONE_LABEL })).toBeInTheDocument();
    await user.selectOptions(select, "cred-000000000001");
    expect(mockSet).toHaveBeenCalledWith(
      expect.objectContaining({
        fullRepo: "acme/widgets",
        bindings: [
          llmBinding,
          { credential_id: "cred-000000000001", role: "forge" },
        ],
      }),
      expect.anything(),
    );
  });

  it("clears the forge row and keeps other roles", async () => {
    repoCreds = {
      repo: "acme/widgets",
      bindings: [llmBinding, { credential_id: "cred-000000000001", role: "forge" }],
      effective: { forge: { error: "x" } },
    };
    asTier("admin");
    const user = userEvent.setup();
    renderSection("acme/widgets", "github");
    await user.selectOptions(screen.getByLabelText(GITHUB_PICKER), "");
    expect(mockSet).toHaveBeenCalledWith(
      expect.objectContaining({
        fullRepo: "acme/widgets",
        bindings: [llmBinding],
      }),
      expect.anything(),
    );
  });

  it("lists gitlab credentials only for a gitlab repo", () => {
    repoCreds = { repo: "acme/widgets", bindings: [], effective: { forge: { error: "x" } } };
    asTier("admin");
    renderSection("acme/widgets", "gitlab");
    expect(screen.getByLabelText("GitLab account embry0 uses for this repository")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "gl" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "alice" })).toBeNull();
  });

  it("operator sees the picker disabled", async () => {
    repoCreds = { repo: "acme/widgets", bindings: [], effective: { forge: { error: "x" } } };
    asTier("operator");
    const user = userEvent.setup();
    renderSection("acme/widgets", "github");
    const select = screen.getByLabelText(GITHUB_PICKER);
    expect(select).toBeDisabled();
    expect(select).toBeInTheDocument();
    await user.hover(select);
    expect(screen.getByText("Only an admin can change accounts.")).toBeInTheDocument();
  });

  it("filters each LLM picker to that provider", () => {
    credentialRows = [
      ...forgeRows(),
      { id: "cred-00000000000a", label: "claude-default", provider: "anthropic", status: "ok" },
      { id: "cred-00000000000b", label: "grok-key", provider: "xai", status: "ok" },
    ];
    repoCreds = {
      repo: "acme/widgets",
      bindings: [],
      effective: {
        forge: { error: "x" },
        llm: {
          anthropic: {
            id: "cred-00000000000a",
            label: "claude-default",
            provider: "anthropic",
            source: "registry",
            trace: ["global -> claude-default"],
          },
        },
      },
    };
    asTier("admin");
    renderSection("acme/widgets", "github");
    expect(screen.getByRole("heading", { name: "LLM accounts" })).toBeInTheDocument();
    const anthropic = screen.getByLabelText(/anthropic account/i);
    expect(within(anthropic).getByRole("option", { name: "claude-default" })).toBeInTheDocument();
    expect(within(anthropic).queryByRole("option", { name: "grok-key" })).toBeNull();
    expect(within(anthropic).queryByRole("option", { name: "alice" })).toBeNull();
    const xai = screen.getByLabelText(/^xai account/i);
    expect(within(xai).getByRole("option", { name: "grok-key" })).toBeInTheDocument();
    expect(within(xai).queryByRole("option", { name: "claude-default" })).toBeNull();
  });

  it("binds an account for every usable provider, counts the unused ones and names the ones not enabled", async () => {
    catalogProviders = [
      { name: "anthropic", available: true },
      { name: "xai", available: false },
      { name: "openai", available: false },
      { name: "custom", available: true },
    ];
    credentialRows = [
      ...forgeRows(),
      { id: "cred-00000000000c", label: "openai-key", provider: "openai", status: "ok" },
      { id: "cred-00000000000d", label: "ollama", provider: "custom", status: "ok" },
    ];
    repoCreds = {
      repo: "acme/widgets",
      bindings: [],
      effective: {
        forge: { error: "x" },
        llm: {
          openai: { id: "cred-00000000000c", label: "openai-key", provider: "openai", source: "registry", trace: ["global -> openai-key"] },
        },
      },
    };
    asTier("admin");
    const user = userEvent.setup();
    renderSection("acme/widgets", "github");
    // OpenAI is not marked available, but it resolves here, so it is never hidden.
    expect(providerRows()).toEqual(["anthropic", "openai", "custom"]);
    const openai = screen.getByLabelText(/^openai account/i);
    expect(within(openai).getByRole("option", { name: "openai-key" })).toBeInTheDocument();
    expect(within(openai).queryByRole("option", { name: "ollama" })).toBeNull();
    expect(within(screen.getByTestId("llm-trace-openai")).getByText("global -> openai-key")).toBeInTheDocument();
    expect(screen.getByTestId("llm-unused")).toHaveTextContent(
      "1 other AI company is switched on here but has no account. This repository does not use it.",
    );
    expect(screen.getByTestId("llm-not-enabled")).toHaveTextContent(
      "Not enabled in this deployment: OpenRouter, Google, Groq, DeepSeek. Name a provider in OPENAI_COMPATIBLE_PROVIDERS to bind its account here.",
    );
    await user.selectOptions(screen.getByLabelText(/^custom account/i), "cred-00000000000d");
    expect(mockSet).toHaveBeenLastCalledWith(
      {
        fullRepo: "acme/widgets",
        bindings: [{ credential_id: "cred-00000000000d", role: "llm", pinned: false }],
      },
      expect.anything(),
    );
  });

  it("shows only the AI companies with an account and counts the rest in one line", () => {
    catalogProviders = catalogWithTwoAccounts();
    repoCreds = { repo: "acme/widgets", bindings: [], effective: { forge: { error: "x" } } };
    asTier("admin");
    renderSection("acme/widgets", "github");
    expect(providerRows()).toEqual(["anthropic", "xai"]);
    const unused = screen.getByTestId("llm-unused");
    expect(unused).toHaveTextContent(
      "6 other AI companies are switched on here but have no account. This repository does not use them.",
    );
    expect(unused.className).toContain("text-muted-foreground");
    expect(screen.queryByText(/No OpenAI account resolved/)).toBeNull();
    expect(screen.queryByTestId("llm-not-enabled")).toBeNull();
  });

  it("never hides a provider this repository has a binding for", () => {
    catalogProviders = catalogWithTwoAccounts();
    credentialRows = [
      ...forgeRows(),
      { id: "cred-00000000000e", label: "groq-key", provider: "groq", status: "disabled" },
    ];
    repoCreds = {
      repo: "acme/widgets",
      bindings: [{ credential_id: "cred-00000000000e", role: "llm", provider: "groq", pinned: true, priority: 0 }],
      effective: { forge: { error: "x" } },
    };
    asTier("admin");
    renderSection("acme/widgets", "github");
    expect(providerRows()).toEqual(["anthropic", "xai", "groq"]);
    expect(screen.getByLabelText(/^groq account/i)).toHaveValue("cred-00000000000e");
    expect(screen.getByTestId("llm-unused")).toHaveTextContent(/^5 other AI companies/);
  });

  it("never hides a provider whose account resolves here, even with an error", () => {
    catalogProviders = catalogWithTwoAccounts();
    repoCreds = {
      repo: "acme/widgets",
      bindings: [],
      effective: { forge: { error: "x" }, llm: { deepseek: { error: "the pinned DeepSeek account is paused" } } },
    };
    asTier("admin");
    renderSection("acme/widgets", "github");
    expect(providerRows()).toEqual(["anthropic", "xai", "deepseek"]);
    expect(within(screen.getByTestId("llm-deepseek")).getByText("the pinned DeepSeek account is paused")).toBeInTheDocument();
  });

  it("puts the code login picker under its effective account, before the AI accounts", () => {
    catalogProviders = catalogWithTwoAccounts();
    repoCreds = { repo: "acme/widgets", bindings: [], effective: { forge: { error: "x" } } };
    renderSection("acme/widgets", "github");
    const effective = screen.getByRole("heading", { name: "Effective forge account" });
    const label = screen.getByText("GitHub account embry0 uses for this repository");
    const llm = screen.getByRole("heading", { name: "LLM accounts" });
    expect(effective.compareDocumentPosition(label) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(label.compareDocumentPosition(llm) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("names the account disclosures 'Why this account?'", () => {
    catalogProviders = catalogWithTwoAccounts();
    repoCreds = {
      repo: "acme/widgets",
      bindings: [],
      effective: {
        forge: { id: "cred-000000000001", label: "alice", provider: "github", source: "registry", trace: [] },
        llm: { anthropic: { id: "cred-00000000000a", label: "claude-default", provider: "anthropic", source: "registry", trace: [] } },
      },
    };
    renderSection("acme/widgets", "github");
    expect(within(screen.getByTestId("repo-accounts-trace")).getByText("Why this account?")).toBeInTheDocument();
    expect(within(screen.getByTestId("llm-trace-anthropic")).getByText("Why this account?")).toBeInTheDocument();
    expect(screen.queryByText("Trace")).toBeNull();
  });

  it("shows Anthropic and xAI only, and claims nothing, before the catalog answers", () => {
    repoCreds = { repo: "acme/widgets", bindings: [], effective: { forge: { error: "x" } } };
    renderSection("acme/widgets", "github");
    expect(screen.getByTestId("llm-anthropic")).toBeInTheDocument();
    expect(screen.getByTestId("llm-xai")).toBeInTheDocument();
    expect(screen.queryByTestId("llm-openai")).toBeNull();
    expect(screen.queryByTestId("llm-not-enabled")).toBeNull();
    expect(screen.queryByTestId("llm-unused")).toBeNull();
  });

  it("pins the selected LLM account for this repo", async () => {
    credentialRows = [
      ...forgeRows(),
      { id: "cred-00000000000a", label: "claude-default", provider: "anthropic", status: "ok" },
    ];
    repoCreds = {
      repo: "acme/widgets",
      bindings: [{ credential_id: "cred-000000000001", role: "forge", pinned: false, priority: 0 }],
      effective: { forge: { error: "x" } },
    };
    asTier("admin");
    const user = userEvent.setup();
    renderSection("acme/widgets", "github");
    await user.selectOptions(screen.getByLabelText(/anthropic account/i), "cred-00000000000a");
    const pin = within(screen.getByTestId("llm-anthropic")).getByRole("checkbox", {
      name: "If this account is paused, stop work on this repository instead of using the shared account",
    });
    await user.click(pin);
    expect(mockSet).toHaveBeenLastCalledWith(
      expect.objectContaining({
        fullRepo: "acme/widgets",
        bindings: expect.arrayContaining([
          expect.objectContaining({ credential_id: "cred-000000000001", role: "forge" }),
          expect.objectContaining({
            credential_id: "cred-00000000000a",
            role: "llm",
            pinned: true,
          }),
        ]),
      }),
      expect.anything(),
    );
  });

  it("enables the picker for an API key session", () => {
    repoCreds = { repo: "acme/widgets", bindings: [], effective: { forge: { error: "x" } } };
    useAuthStore.setState({
      status: "authenticated",
      authMode: "key",
      user: null,
      ssoEnabled: false,
      apiKeyLogin: true,
    });
    renderSection("acme/widgets", "github");
    expect(screen.getByLabelText(GITHUB_PICKER)).toBeEnabled();
  });
});
