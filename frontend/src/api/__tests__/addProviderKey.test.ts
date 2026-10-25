import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();
const put = vi.fn();
vi.mock("../client", () => ({ api: { get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a), put: (...a: unknown[]) => put(...a) } }));

import { addProviderKey } from "../credentials";

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  put.mockReset();
});

describe("addProviderKey", () => {
  it("stores the key and binds it globally in place of the provider's old global key, keeping every other binding", async () => {
    post.mockResolvedValue({ data: { id: "cred-new" } });
    get.mockResolvedValue({
      data: {
        bindings: [
          { role: "forge", provider: "github", credential_id: "cred-gh", pinned: false, priority: 0 },
          { role: "llm", provider: "openrouter", credential_id: "cred-old", pinned: false, priority: 0 },
          { role: "llm", provider: "anthropic", credential_id: "cred-an", pinned: true, priority: 2 },
        ],
      },
    });
    put.mockResolvedValue({ data: { bindings: [] } });
    await addProviderKey("openrouter", { secret: "sk-or", label: "api-key" });
    expect(post.mock.calls[0][1]).toEqual({ kind: "llm_api_key", provider: "openrouter", label: "api-key", secret: "sk-or" });
    expect(put.mock.calls[0][1]).toEqual({
      scope_kind: "global",
      scope_value: "",
      bindings: [
        { credential_id: "cred-gh", role: "forge", pinned: false, priority: 0 },
        { credential_id: "cred-an", role: "llm", pinned: true, priority: 2 },
        { credential_id: "cred-new", role: "llm" },
      ],
    });
  });

  it("sends a custom provider's API root with its key", async () => {
    post.mockResolvedValue({ data: { id: "cred-c" } });
    get.mockResolvedValue({ data: { bindings: [] } });
    put.mockResolvedValue({ data: { bindings: [] } });
    await addProviderKey("custom", { secret: "x", label: "api-key", baseUrl: "http://10.0.0.5:11434/v1" });
    expect(post.mock.calls[0][1].metadata).toEqual({ base_url: "http://10.0.0.5:11434/v1" });
  });
});
