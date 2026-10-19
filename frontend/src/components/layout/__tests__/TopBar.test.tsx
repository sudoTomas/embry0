import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { TopBar } from "../TopBar";
import { useAuthStore } from "@/stores/authStore";

function renderBar() {
  return render(
    <MemoryRouter>
      <TopBar />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useAuthStore.setState({ authMode: null, user: null });
});

describe("TopBar — the family header", () => {
  it("carries the lockup and the tagline, and no breadcrumb", () => {
    renderBar();
    expect(screen.getByRole("img", { name: "embry0" })).toBeInTheDocument();
    expect(screen.getByText("Software that is alive")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
  });

  it("shows the signed-in identity and a sign-out control for an SSO session", () => {
    useAuthStore.setState({
      authMode: "session",
      user: { email: "operator@example.com", name: "Operator", tier: "operator" },
    });
    renderBar();
    expect(screen.getByText("operator@example.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("says the session runs on an API key when there is no SSO identity", () => {
    useAuthStore.setState({ authMode: "key", user: null });
    renderBar();
    expect(screen.getByText("API key")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
  });
});
