import { describe, it, expect, vi, beforeEach } from "vitest";
import { useAuthStore } from "@/stores/authStore";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetch = vi.fn();
const mockUpdate = vi.fn();

vi.mock("@/api/integrations", () => ({
  fetchIntegrationConfig: () => mockFetch(),
  updateIntegrationConfig: (cfg: unknown) => mockUpdate(cfg),
}));

import IntegrationsTab from "../IntegrationsTab";

const CONFIG = {
  trigger_labels: ["embry0"],
  webhook_secret_set: false,
  slack_webhook_url_set: false,
  slack_webhook_url_masked: "",
  telegram_bot_token_set: false,
  telegram_bot_token_masked: "",
  telegram_chat_id: "",
  webhook_notify_url: "",
  webhook_notify_secret_set: false,
  webhook_notify_secret_masked: "",
  linear_api_key_set: true,
  linear_api_key_masked: "...3f9a",
  linear_webhook_secret_set: false,
  linear_webhook_secret_masked: "",
  linear_repo_map: '{"Proj": "owner/repo"}',
};

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <IntegrationsTab />
    </QueryClientProvider>,
  );
}

describe("IntegrationsTab — Linear (#157)", () => {
  beforeEach(() => {
    // Saving is admin work (require_admin); these tests drive it as the API key.
    useAuthStore.setState({ authMode: "key" });
    vi.clearAllMocks();
    mockFetch.mockResolvedValue(CONFIG);
    mockUpdate.mockResolvedValue(CONFIG);
  });

  it("renders a Linear section", async () => {
    renderTab();
    expect(await screen.findByText("Linear")).toBeInTheDocument();
  });

  it("shows the stored repo map and never the raw api key", async () => {
    renderTab();
    const repoMap = await screen.findByLabelText(/Project \/ Team/i);
    expect(repoMap).toHaveValue('{"Proj": "owner/repo"}');
    // Only the masked form is ever available to the client.
    expect(screen.queryByDisplayValue(/lin_api/)).toBeNull();
    expect(screen.getByText("...3f9a")).toBeInTheDocument();
  });

  it("sends only the edited field on save", async () => {
    const user = userEvent.setup();
    renderTab();

    const repoMap = await screen.findByLabelText(/Project \/ Team/i);
    await user.clear(repoMap);
    await user.type(repoMap, '{{"A": "o/a"}');

    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    const payload = mockUpdate.mock.calls[0][0];
    expect(payload).toEqual({ linear_repo_map: '{"A": "o/a"}' });
    // An untouched secret must not be transmitted as an empty string, which
    // the repository would otherwise treat as a deliberate write.
    expect(payload).not.toHaveProperty("linear_api_key");
  });
});
