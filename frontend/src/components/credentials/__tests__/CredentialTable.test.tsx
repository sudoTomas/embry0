import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CredentialTable } from "../CredentialTable";
import type { Credential } from "@/lib/types/credentials";

const cred = (over: Partial<Credential> = {}): Credential => ({
  id: "cred-000000000001", kind: "forge_token", provider: "github", host: "github.com", label: "alice",
  git_author_name: "Alice", git_author_email: "alice@example.com", masked_preview: "ghp_••••••••mnop", verified_login: "alice-gh",
  status: "ok", status_reason: null, quarantined_until: null, binding_count: 2, created_at: null, updated_at: null,
  rotated_at: null, last_used_at: null, ...over,
});

const handlers = () => ({ onRotate: vi.fn(), onVerify: vi.fn(), onToggleDisabled: vi.fn(), onDelete: vi.fn(), onBindings: vi.fn() });

describe("CredentialTable", () => {
  it("renders label, masked preview and status, never a secret-looking value", () => {
    render(<CredentialTable credentials={[cred()]} isAdmin {...handlers()} />);
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("ghp_••••••••mnop")).toBeInTheDocument();
    expect(screen.getByText("OK")).toBeInTheDocument();
    expect(screen.getByText("alice-gh")).toBeInTheDocument();
  });

  it("disables every mutating action for non-admins", () => {
    render(<CredentialTable credentials={[cred()]} isAdmin={false} {...handlers()} />);
    for (const id of ["credential-rotate", "credential-verify", "credential-toggle", "credential-delete"]) {
      expect(screen.getByTestId(id)).toBeDisabled();
    }
    expect(screen.getByTestId("credential-bindings")).toBeEnabled(); // read-only drawer
  });

  it("calls the handlers for admins", async () => {
    const h = handlers(); const user = userEvent.setup();
    render(<CredentialTable credentials={[cred()]} isAdmin {...h} />);
    await user.click(screen.getByTestId("credential-rotate"));
    expect(h.onRotate).toHaveBeenCalledWith(expect.objectContaining({ id: "cred-000000000001" }));
  });

  it("shows quarantine reason and decryption failure tones", () => {
    render(<CredentialTable credentials={[
      cred({ id: "cred-000000000002", label: "q", status: "quarantined", status_reason: "rate_limited", quarantined_until: "2026-09-30T10:00:00Z" }),
      cred({ id: "cred-000000000003", label: "d", status: "disabled", status_reason: "decryption_failed" }),
    ]} isAdmin {...handlers()} />);
    expect(screen.getByText("Quarantined")).toHaveAttribute("title", expect.stringContaining("rate_limited"));
    expect(screen.getByText("Disabled")).toHaveAttribute("title", expect.stringContaining("decryption_failed"));
  });

  it("shows an LLM provider together with its kind", () => {
    render(<CredentialTable credentials={[cred({
      id: "cred-00000000000b", provider: "xai", kind: "llm_api_key", host: "", label: "grok-key",
    })]} isAdmin {...handlers()} />);
    const row = screen.getByTestId("credential-row");
    expect(row).toHaveTextContent("xAI");
    // The kind reads in words, never as the stored identifier.
    expect(row).toHaveTextContent("API key");
    expect(row).not.toHaveTextContent("llm_api_key");
  });

  it("shows the kind of an OpenAI-compatible key, not its API host", () => {
    render(<CredentialTable credentials={[
      cred({ id: "cred-00000000000c", provider: "openai", kind: "llm_api_key", host: "api.openai.com", label: "openai-key" }),
      cred({ id: "cred-00000000000d", provider: "custom", kind: "llm_api_key", host: "10.0.0.5", label: "ollama" }),
    ]} isAdmin {...handlers()} />);
    const [openai, custom] = screen.getAllByTestId("credential-row");
    expect(openai).toHaveTextContent("OpenAI");
    expect(openai).toHaveTextContent("API key");
    expect(openai).not.toHaveTextContent("api.openai.com");
    expect(custom).toHaveTextContent("Custom");
    expect(custom).toHaveTextContent("API key");
  });

  it("names the default commit author in words, never the internal fallback", () => {
    render(<CredentialTable credentials={[cred({ git_author_name: null, git_author_email: null })]} isAdmin {...handlers()} />);
    const cell = screen.getByText("embry0 default");
    expect(cell).toHaveAttribute("title", expect.stringContaining("EMBRY0_GIT_AUTHOR_NAME"));
    expect(screen.getByTestId("credential-row")).not.toHaveTextContent("branding default");
  });

  it("renders the empty state", () => {
    render(<CredentialTable credentials={[]} isAdmin {...handlers()} />);
    expect(screen.getByText("No credentials yet")).toBeInTheDocument();
  });
});
