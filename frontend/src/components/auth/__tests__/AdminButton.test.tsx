import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AdminButton } from "../AdminButton";
import { useAuthStore } from "@/stores/authStore";

beforeEach(() => {
  useAuthStore.setState({
    status: "authenticated",
    sessionExpired: false,
    authMode: "key",
    user: null,
    keyTier: undefined,
    ssoEnabled: false,
    apiKeyLogin: true,
  });
});

describe("AdminButton", () => {
  it("is disabled with the scope hint for a key session of tier operator (#101)", async () => {
    useAuthStore.setState({ authMode: "key", keyTier: "operator" });
    render(<AdminButton>Delete host</AdminButton>);
    const button = screen.getByRole("button", { name: "Delete host" });
    expect(button, "an operate-scope key cannot use require_admin routes").toBeDisabled();
    await userEvent.setup().hover(button);
    expect(screen.getByText("Admin scope required")).toBeInTheDocument();
  });

  it("is enabled for a key session of tier admin", () => {
    useAuthStore.setState({ authMode: "key", keyTier: "admin" });
    render(<AdminButton>Delete host</AdminButton>);
    expect(screen.getByRole("button", { name: "Delete host" })).toBeEnabled();
  });

  it("is enabled for a key session on a backend that does not report the tier", () => {
    useAuthStore.setState({ authMode: "key", keyTier: undefined });
    render(<AdminButton>Delete host</AdminButton>);
    expect(screen.getByRole("button", { name: "Delete host" })).toBeEnabled();
  });
});
