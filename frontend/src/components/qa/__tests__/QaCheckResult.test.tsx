// The free check of a test setup, read by someone who is not technical:
// what would stop every test run, what is worth knowing, and what the setup
// does, each in a plain sentence.
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { QaCheckResult as CheckResult } from "@/api/qaConfig";
import { QaCheckResult } from "../QaCheckResult";

type App = NonNullable<CheckResult["summary"]>["apps"][number];

function app(overrides: Partial<App> = {}): App {
  return {
    name: "web",
    target: "managed",
    start: "npm ci && npm run dev",
    address: "http://localhost:5173",
    address_is_local: true,
    criteria: ["home page loads", "sign-in form shows", "no console errors"],
    guardrails: 0,
    machine: "qa-external",
    machine_has_browser: true,
    ...overrides,
  };
}

function result(overrides: Partial<CheckResult> = {}): CheckResult {
  return {
    ok: true,
    problems: [],
    warnings: [],
    summary: { when: "auto", when_plain: "When a change touches the app", apps: [app()] },
    ...overrides,
  };
}

describe("QaCheckResult", () => {
  it("says what a local managed app does in one sentence, and when it is tested", () => {
    render(<QaCheckResult result={result()} />);
    const summary = screen.getByTestId("qa-check-summary");
    expect(
      within(summary).getByText(
        "web: starts with npm ci && npm run dev, tested at http://localhost:5173 on embry0's test computer, 3 checks.",
      ),
    ).toBeInTheDocument();
    expect(within(summary).getByText("When: When a change touches the app")).toBeInTheDocument();
  });

  it("does not claim the test computer for an address outside it", () => {
    render(
      <QaCheckResult
        result={result({
          summary: {
            when: "always",
            when_plain: "On every change",
            apps: [app({ address: "https://www.example.com", address_is_local: false, criteria: ["home page loads"] })],
          },
        })}
      />,
    );
    expect(
      screen.getByText("web: starts with npm ci && npm run dev, tested at https://www.example.com, 1 check."),
    ).toBeInTheDocument();
  });

  it("leaves out the start for an app embry0 does not start", () => {
    render(
      <QaCheckResult
        result={result({
          summary: {
            when: "auto",
            when_plain: "When a change touches the app",
            apps: [
              app({
                name: "shop",
                target: "deployed",
                start: null,
                address: "https://shop.example.com",
                address_is_local: false,
                criteria: ["home page loads", "cart opens"],
              }),
            ],
          },
        })}
      />,
    );
    expect(screen.getByText("shop: tested at https://shop.example.com, 2 checks.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/null/);
  });

  it("says all is good when the setup is fine and there is nothing to warn about", () => {
    render(<QaCheckResult result={result()} />);
    expect(
      screen.getByText("All good: every app has a work machine with a browser."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("qa-check-problems")).not.toBeInTheDocument();
    expect(screen.queryByTestId("qa-check-warnings")).not.toBeInTheDocument();
  });

  it("lists each problem with its fix in the destructive tone, and never says all good", () => {
    render(
      <QaCheckResult
        result={result({
          ok: false,
          problems: [
            {
              where: "app web",
              plain: "The work machine 'slim' has no browser, so every test run would stop.",
              fix: "Use 'qa-external'.",
            },
            { where: "", plain: "The text could not be read near line 4.", fix: "" },
          ],
        })}
      />,
    );
    const problems = screen.getByTestId("qa-check-problems");
    expect(problems.className).toContain("text-destructive");
    const items = within(problems).getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual([
      "The work machine 'slim' has no browser, so every test run would stop. Use 'qa-external'.",
      "The text could not be read near line 4.",
    ]);
    expect(screen.queryByText(/All good/)).not.toBeInTheDocument();
  });

  it("lists warnings in the warning tone, and does not say all good", () => {
    render(
      <QaCheckResult
        result={result({
          warnings: [
            {
              code: "live_address",
              plain:
                "web is tested at https://www.example.com, which is outside embry0's test computer. Tests there click through a real site.",
              confirm: true,
            },
            {
              code: "testing_off",
              plain: "Testing stays off: embry0 opens pull requests without testing the app.",
              confirm: false,
            },
          ],
        })}
      />,
    );
    const warnings = screen.getByTestId("qa-check-warnings");
    expect(warnings.className).toContain("text-warning");
    expect(within(warnings).getAllByRole("listitem")).toHaveLength(2);
    expect(warnings).toHaveTextContent("Tests there click through a real site.");
    expect(screen.queryByText(/All good/)).not.toBeInTheDocument();
  });

  it("shows no summary when the text could not be read", () => {
    render(
      <QaCheckResult
        result={result({
          ok: false,
          problems: [{ where: "", plain: "Line 3 starts with a tab. Indent with spaces.", fix: "" }],
          summary: null,
        })}
      />,
    );
    expect(screen.queryByTestId("qa-check-summary")).not.toBeInTheDocument();
    expect(screen.getByText("Line 3 starts with a tab. Indent with spaces.")).toBeInTheDocument();
  });
});
