// frontend/src/components/credentials/__tests__/CredentialModal.test.tsx
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CredentialModal } from "../CredentialModal";

const { startXaiDeviceLogin, pollXaiDeviceLogin } = vi.hoisted(() => ({
  startXaiDeviceLogin: vi.fn(),
  pollXaiDeviceLogin: vi.fn(),
}));

vi.mock("@/api/credentials", () => ({
  startXaiDeviceLogin,
  pollXaiDeviceLogin,
}));

describe("CredentialModal", () => {
  it("submits a create payload with a trimmed secret", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined); const user = userEvent.setup();
    render(<CredentialModal modeSpec={{ mode: "create" }} onSubmit={onSubmit} onClose={vi.fn()} gitlabHost="gitlab.example.com" />);
    await user.type(screen.getByLabelText(/label/i), "alice");
    await user.type(screen.getByLabelText(/^secret/i), "  ghp_abcdefghijklmnop  ");
    await user.click(screen.getByRole("button", { name: /create/i }));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ kind: "forge_token", provider: "github", label: "alice", secret: "ghp_abcdefghijklmnop" }));
  });

  it("blocks a blank secret with an inline error, no submit", async () => {
    const onSubmit = vi.fn(); const user = userEvent.setup();
    render(<CredentialModal modeSpec={{ mode: "create" }} onSubmit={onSubmit} onClose={vi.fn()} gitlabHost={null} />);
    await user.type(screen.getByLabelText(/label/i), "alice");
    await user.click(screen.getByRole("button", { name: /create/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/secret/i);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("clears the secret field after a failed submit", async () => {
    const onSubmit = vi.fn().mockRejectedValue({ response: { status: 409, data: { detail: "a github credential labelled 'alice' already exists" } } });
    const user = userEvent.setup();
    render(<CredentialModal modeSpec={{ mode: "create" }} onSubmit={onSubmit} onClose={vi.fn()} gitlabHost={null} />);
    await user.type(screen.getByLabelText(/label/i), "alice");
    await user.type(screen.getByLabelText(/^secret/i), "ghp_abcdefghijklmnop");
    await user.click(screen.getByRole("button", { name: /create/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists/);
    expect(screen.getByLabelText(/^secret/i)).toHaveValue("");
  });

  it("gitlab option is disabled without a configured host", () => {
    render(<CredentialModal modeSpec={{ mode: "create" }} onSubmit={vi.fn()} onClose={vi.fn()} gitlabHost={null} />);
    expect(screen.getByRole("option", { name: /gitlab/i })).toBeDisabled();
  });

  it("submits an Anthropic API key as an anthropic llm_api_key", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<CredentialModal modeSpec={{ mode: "create" }} onSubmit={onSubmit} onClose={vi.fn()} gitlabHost={null} />);
    await user.selectOptions(screen.getByLabelText(/^kind/i), "llm_api_key");
    expect(screen.getByLabelText(/^provider/i)).toHaveValue("anthropic");
    await user.type(screen.getByLabelText(/label/i), "claude-default");
    await user.type(screen.getByLabelText(/^secret/i), "sk-ant-placeholder");
    await user.click(screen.getByRole("button", { name: /^create$/i }));
    expect(onSubmit).toHaveBeenCalledWith({
      kind: "llm_api_key",
      provider: "anthropic",
      label: "claude-default",
      secret: "sk-ant-placeholder",
    });
  });

  it("offers every LLM provider by its maker's name and says which are not enabled", async () => {
    const user = userEvent.setup();
    render(
      <CredentialModal
        modeSpec={{ mode: "create" }}
        onSubmit={vi.fn()}
        onClose={vi.fn()}
        gitlabHost={null}
        catalogProviders={[{ name: "anthropic" }, { name: "xai" }, { name: "openai" }, { name: "custom" }]}
      />,
    );
    await user.selectOptions(screen.getByLabelText(/^kind/i), "llm_api_key");
    const select = screen.getByLabelText(/^provider/i);
    const options = within(select).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([
      "Anthropic",
      "xAI",
      "OpenAI",
      "OpenRouter (not enabled)",
      "Google (not enabled)",
      "Groq (not enabled)",
      "DeepSeek (not enabled)",
      "Custom",
    ]);
    expect(options.filter((o) => (o as HTMLOptionElement).disabled).map((o) => (o as HTMLOptionElement).value)).toEqual([
      "openrouter",
      "google",
      "groq",
      "deepseek",
    ]);
    expect(screen.getByText(/not enabled in this deployment: openrouter, google, groq, deepseek/i)).toHaveTextContent(
      "OPENAI_COMPATIBLE_PROVIDERS",
    );
  });

  it("submits an enabled OpenAI-compatible key with no host and no metadata", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(
      <CredentialModal
        modeSpec={{ mode: "create" }}
        onSubmit={onSubmit}
        onClose={vi.fn()}
        gitlabHost={null}
        catalogProviders={[{ name: "anthropic" }, { name: "xai" }, { name: "deepseek" }]}
      />,
    );
    await user.selectOptions(screen.getByLabelText(/^kind/i), "llm_api_key");
    await user.selectOptions(screen.getByLabelText(/^provider/i), "deepseek");
    await user.type(screen.getByLabelText(/label/i), "api-key");
    await user.type(screen.getByLabelText(/^secret/i), "sk-placeholder");
    await user.click(screen.getByRole("button", { name: /^create$/i }));
    expect(onSubmit).toHaveBeenCalledWith({
      kind: "llm_api_key",
      provider: "deepseek",
      label: "api-key",
      secret: "sk-placeholder",
    });
  });

  it("submits a custom key with the API root it needs", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(
      <CredentialModal
        modeSpec={{ mode: "create" }}
        onSubmit={onSubmit}
        onClose={vi.fn()}
        gitlabHost={null}
        catalogProviders={[{ name: "anthropic" }, { name: "xai" }, { name: "custom" }]}
      />,
    );
    await user.selectOptions(screen.getByLabelText(/^kind/i), "llm_api_key");
    expect(screen.queryByLabelText(/^base url/i)).toBeNull();
    await user.selectOptions(screen.getByLabelText(/^provider/i), "custom");
    await user.type(screen.getByLabelText(/^base url/i), " http://10.0.0.5:11434/v1 ");
    await user.type(screen.getByLabelText(/label/i), "ollama");
    await user.type(screen.getByLabelText(/^secret/i), "placeholder");
    await user.click(screen.getByRole("button", { name: /^create$/i }));
    expect(onSubmit).toHaveBeenCalledWith({
      kind: "llm_api_key",
      provider: "custom",
      label: "ollama",
      secret: "placeholder",
      metadata: { base_url: "http://10.0.0.5:11434/v1" },
    });
  });

  it("refuses a custom key whose API root the backend would refuse, no submit", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(
      <CredentialModal
        modeSpec={{ mode: "create" }}
        onSubmit={onSubmit}
        onClose={vi.fn()}
        gitlabHost={null}
        catalogProviders={[{ name: "anthropic" }, { name: "xai" }, { name: "custom" }]}
      />,
    );
    await user.selectOptions(screen.getByLabelText(/^kind/i), "llm_api_key");
    await user.selectOptions(screen.getByLabelText(/^provider/i), "custom");
    await user.type(screen.getByLabelText(/label/i), "ollama");
    await user.type(screen.getByLabelText(/^secret/i), "placeholder");
    await user.click(screen.getByRole("button", { name: /^create$/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/base url is required/i);
    await user.type(screen.getByLabelText(/^base url/i), "http://user:pw@10.0.0.5:11434/v1");
    await user.click(screen.getByRole("button", { name: /^create$/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/credentials, a query or a fragment/i);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("says a provider is not enabled instead of sending it", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    const props = { modeSpec: { mode: "create" } as const, onSubmit, onClose: vi.fn(), gitlabHost: null };
    const { rerender } = render(<CredentialModal {...props} />);
    await user.selectOptions(screen.getByLabelText(/^kind/i), "llm_api_key");
    // Before the catalog answers the family is offered and the backend decides.
    expect(screen.getByRole("option", { name: "OpenAI" })).toBeEnabled();
    expect(screen.queryByText(/not enabled in this deployment/i)).toBeNull();
    await user.selectOptions(screen.getByLabelText(/^provider/i), "openai");
    rerender(<CredentialModal {...props} catalogProviders={[{ name: "anthropic" }, { name: "xai" }]} />);
    await user.type(screen.getByLabelText(/label/i), "api-key");
    await user.type(screen.getByLabelText(/^secret/i), "sk-placeholder");
    await user.click(screen.getByRole("button", { name: /^create$/i }));
    expect(screen.getByRole("alert")).toHaveTextContent("OpenAI is not enabled in this deployment.");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("offers xAI sign-in only for an xAI account, the one kind it creates", async () => {
    const user = userEvent.setup();
    render(<CredentialModal modeSpec={{ mode: "create" }} onSubmit={vi.fn()} onClose={vi.fn()} gitlabHost={null} />);
    const signIn = () => screen.queryByRole("button", { name: /sign in with xai/i });
    expect(signIn(), "forge token").toBeNull();
    await user.selectOptions(screen.getByLabelText(/^kind/i), "claude_oauth");
    expect(signIn(), "Claude OAuth token").toBeNull();
    await user.selectOptions(screen.getByLabelText(/^kind/i), "llm_api_key");
    expect(screen.getByLabelText(/^provider/i)).toHaveValue("anthropic");
    expect(signIn(), "Anthropic API key").toBeNull();
    await user.selectOptions(screen.getByLabelText(/^provider/i), "xai");
    expect(signIn()).toBeInTheDocument();
    expect(screen.getByText(/stores an xAI OAuth account/i)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/^kind/i), "forge_token");
    expect(signIn(), "back to forge token").toBeNull();
  });

  it("keeps a started xAI login on screen when the kind changes", async () => {
    startXaiDeviceLogin.mockResolvedValue({
      login_id: "login-2",
      user_code: "WXYZ-1234",
      verification_uri_complete: "https://x.ai/device?user_code=WXYZ-1234",
      expires_at: "2026-10-01T00:05:00Z",
      interval: 5,
    });
    const user = userEvent.setup();
    render(<CredentialModal modeSpec={{ mode: "create" }} onSubmit={vi.fn()} onClose={vi.fn()} gitlabHost={null} />);
    await user.selectOptions(screen.getByLabelText(/^kind/i), "llm_api_key");
    await user.selectOptions(screen.getByLabelText(/^provider/i), "xai");
    await user.type(screen.getByLabelText(/label/i), "grok-default");
    await user.click(screen.getByRole("button", { name: /sign in with xai/i }));
    expect(await screen.findByText("WXYZ-1234")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/^kind/i), "forge_token");
    expect(screen.getByText("WXYZ-1234")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /sign in with xai/i })).toBeNull();
  });

  it("polls xAI device login on the interval from the start response", async () => {
    vi.useFakeTimers();
    startXaiDeviceLogin.mockResolvedValue({
      login_id: "login-1",
      user_code: "ABCD-EFGH",
      verification_uri_complete: "https://x.ai/device?user_code=ABCD-EFGH",
      expires_at: "2026-10-01T00:05:00Z",
      interval: 5,
    });
    pollXaiDeviceLogin
      .mockResolvedValueOnce({ status: "pending" })
      .mockResolvedValueOnce({ status: "done", credential: { id: "cred-00000000000b" } });
    const onSubmit = vi.fn();
    render(<CredentialModal modeSpec={{ mode: "create" }} onSubmit={onSubmit} onClose={vi.fn()} gitlabHost={null} />);
    fireEvent.change(screen.getByLabelText(/^kind/i), { target: { value: "llm_api_key" } });
    fireEvent.change(screen.getByLabelText(/^provider/i), { target: { value: "xai" } });
    fireEvent.change(screen.getByLabelText(/label/i), { target: { value: "grok-default" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in with xai/i }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(startXaiDeviceLogin).toHaveBeenCalledWith("grok-default");
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText("ABCD-EFGH")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /x\.ai/i });
    expect(link).toHaveAttribute("href", "https://x.ai/device?user_code=ABCD-EFGH");
    expect(link).toHaveAttribute("target", "_blank");
    fireEvent.mouseOver(link);
    expect(screen.getByText("opens x.ai in a new tab")).toBeInTheDocument();
    expect(pollXaiDeviceLogin).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4999);
    });
    expect(pollXaiDeviceLogin).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(pollXaiDeviceLogin).toHaveBeenCalledTimes(1);
    expect(pollXaiDeviceLogin).toHaveBeenCalledWith("login-1");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(pollXaiDeviceLogin).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(pollXaiDeviceLogin).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });

  it("keeps the dialog inside a short screen and scrolls it inside itself", () => {
    render(<CredentialModal modeSpec={{ mode: "create" }} onSubmit={vi.fn()} onClose={vi.fn()} gitlabHost={null} />);
    const dialog = screen.getByRole("dialog", { name: "Create credential" });
    // jsdom has no layout, so this pins the classes: the backdrop keeps a 16px
    // gutter, and the dialog is no taller than the space inside it and scrolls
    // its own content without handing the scroll to the page.
    expect(dialog.parentElement?.className).toContain("p-4");
    expect(dialog.className).toContain("max-h-full");
    expect(dialog.className).toContain("overflow-y-auto");
    expect(dialog.className).toContain("overscroll-contain");
  });

  it("rotate mode shows the label read-only and submits only the secret", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined); const user = userEvent.setup();
    const credential = { id: "cred-000000000001", label: "alice", provider: "github", host: "github.com" } as never;
    render(<CredentialModal modeSpec={{ mode: "rotate", credential }} onSubmit={onSubmit} onClose={vi.fn()} gitlabHost={null} />);
    await user.type(screen.getByLabelText(/new secret/i), "ghp_zzzzzzzzzzzzzzzz");
    await user.click(screen.getByRole("button", { name: /rotate/i }));
    expect(onSubmit).toHaveBeenCalledWith({ secret: "ghp_zzzzzzzzzzzzzzzz" });
  });
});

afterEach(() => {
  vi.useRealTimers();
  startXaiDeviceLogin.mockReset();
  pollXaiDeviceLogin.mockReset();
});
