import { useState, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Edge, Node } from "@xyflow/react";
import ts from "typescript";
import { createFocusTrap } from "../focus-trap";
import { EnvVarModal } from "@/components/environments/EnvVarModal";
import { CredentialModal } from "@/components/credentials/CredentialModal";
import { BindingsDrawer } from "@/components/credentials/BindingsDrawer";
import { CreateIssueDialog } from "@/components/issues/CreateIssueDialog";
import { SetupAgentDialog } from "@/components/repos/SetupAgentDialog";
import { AgentDetailPopup } from "@/components/pipeline-editor/AgentDetailPopup";
import { EdgeDetailPopup } from "@/components/pipeline-editor/EdgeDetailPopup";
import type { Credential } from "@/lib/types/credentials";
import type { EnvVar } from "@/lib/types/environment";

vi.mock("@/api/credentials", () => ({ startXaiDeviceLogin: vi.fn(), pollXaiDeviceLogin: vi.fn() }));
vi.mock("@/hooks/useCredentials", () => ({
  useCredentials: () => ({ data: [] }),
  useScopedBindings: () => ({ data: [], isError: false, isFetching: false }),
  useSetScopedBindings: () => ({ mutate: vi.fn(), error: null, isPending: false }),
}));
vi.mock("@/hooks/useIssues", () => ({ useCreateIssue: () => ({ mutate: vi.fn(), isPending: false }) }));
vi.mock("@/hooks/useRepos", () => ({ useRegisteredRepos: () => ({ data: [], isLoading: false, isError: false }) }));
vi.mock("@/hooks/useGitHub", () => ({ useGitHubRepos: () => ({ data: { repos: [] }, isLoading: false, isError: false }) }));
vi.mock("@/hooks/useSetupProposal", () => ({
  useSetupCost: () => ({ data: undefined, isPending: true, isError: false, refetch: vi.fn() }),
  useStartSetup: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [] }) }));
vi.mock("@/hooks/useSkills", () => ({ useSkills: () => ({ data: [], isLoading: false }) }));

const SOURCES = import.meta.glob(
  ["/src/**/*.tsx", "!/src/**/__tests__/**", "!/src/**/*.test.tsx"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

/** The names of every JSX attribute written in a source file. */
function jsxAttributes(fileName: string, source: string): string[] {
  const file = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isJsxAttribute(node)) out.push(node.name.getText(file));
    ts.forEachChild(node, visit);
  };
  visit(file);
  return out;
}

const variable: EnvVar = {
  key: "API_URL",
  value: "https://example.invalid",
  var_type: "config",
  description: "",
  required: false,
  scope: "app",
};

const credential: Credential = {
  id: "cred-000000000001",
  kind: "forge_token",
  provider: "github",
  host: "github.com",
  label: "alice",
  git_author_name: null,
  git_author_email: null,
  masked_preview: "ghp_••••••••mnop",
  verified_login: null,
  status: "ok",
  status_reason: null,
  quarantined_until: null,
  binding_count: 0,
  created_at: null,
  updated_at: null,
  rotated_at: null,
  last_used_at: null,
};

const node = { id: "n1", position: { x: 0, y: 0 }, data: { agentType: "coder" } } as Node;
const flowEdge = { id: "e1", source: "triage", target: "developer", data: { edgeType: "flow" } } as Edge;
const feedbackEdge = { id: "e2", source: "reviewer", target: "developer", data: { edgeType: "feedback" } } as Edge;

/** Every dialog that traps focus, and the ways a person closes it. The
 * screenshot lightbox is pinned in its own tests. */
const DIALOGS: { name: string; closes: string[]; dialog: (close: () => void) => ReactNode }[] = [
  {
    name: "add variable",
    closes: ["Escape", "Cancel"],
    dialog: (close) => <EnvVarModal variable={null} onSave={vi.fn()} onClose={close} />,
  },
  {
    name: "edit variable",
    closes: ["Escape", "Cancel"],
    dialog: (close) => <EnvVarModal variable={variable} onSave={vi.fn()} onClose={close} />,
  },
  {
    name: "credential",
    closes: ["Escape", "Cancel"],
    dialog: (close) => <CredentialModal modeSpec={{ mode: "create" }} onSubmit={vi.fn()} onClose={close} gitlabHost={null} />,
  },
  {
    name: "bindings",
    closes: ["Escape", "Close"],
    dialog: (close) => <BindingsDrawer credential={credential} isAdmin onClose={close} />,
  },
  {
    name: "create issue",
    closes: ["Escape", "Cancel"],
    dialog: (close) => <CreateIssueDialog onClose={close} />,
  },
  {
    name: "setup agent",
    closes: ["Escape", "Cancel"],
    dialog: (close) => <SetupAgentDialog repo="acme/widgets" onClose={close} />,
  },
  {
    name: "pipeline agent",
    closes: ["Escape", "Close"],
    dialog: (close) => <AgentDetailPopup node={node} onUpdate={vi.fn()} onClose={close} />,
  },
  {
    // Its footer button and its corner X are both named Close.
    name: "pipeline flow edge",
    closes: ["Escape"],
    dialog: (close) => <EdgeDetailPopup edge={flowEdge} onUpdate={vi.fn()} onClose={close} />,
  },
  {
    name: "pipeline feedback edge",
    closes: ["Escape", "Cancel"],
    dialog: (close) => <EdgeDetailPopup edge={feedbackEdge} onUpdate={vi.fn()} onClose={close} />,
  },
];

function Page({ dialog }: { dialog: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <main>
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      {open && dialog(() => setOpen(false))}
    </main>
  );
}

/**
 * Open the dialog from its button and wait for focus to move into it. Safari
 * does not focus a button it clicks or taps; with `safari`, the button's
 * mousedown is prevented, so user-event leaves its focus where it was too.
 */
async function open(user: ReturnType<typeof userEvent.setup>, { safari = false } = {}) {
  const opener = screen.getByRole("button", { name: "Open" });
  const focused = vi.fn();
  opener.addEventListener("focus", focused);
  if (safari) opener.addEventListener("mousedown", (e) => e.preventDefault());
  await user.click(opener);
  const dialog = screen.getByRole("dialog");
  await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));
  if (safari) expect(focused).not.toHaveBeenCalled();
  return opener;
}

describe("createFocusTrap", () => {
  it("moves focus to the element marked data-autofocus, then back to the opener", async () => {
    const opener = document.body.appendChild(document.createElement("button"));
    const container = document.body.appendChild(document.createElement("div"));
    container.innerHTML = `<button>Close</button><input data-autofocus /><button>Save</button>`;
    opener.focus();

    const cleanup = createFocusTrap(container);
    await waitFor(() => expect(document.activeElement).toBe(container.querySelector("input")));
    container.remove();
    cleanup();

    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  // Safari does not focus a button it clicks or taps, so the trap saved
  // <body> as the opener and on close sent focus nowhere.
  it("returns focus to the control the pointer pressed when the press left focus on <body>", async () => {
    const opener = document.body.appendChild(document.createElement("button"));
    opener.innerHTML = `<svg><path d="M0 0" /></svg>`;
    const container = document.body.appendChild(document.createElement("div"));
    container.innerHTML = `<button>Close</button>`;
    fireEvent.pointerDown(opener.querySelector("path")!);
    expect(document.activeElement).toBe(document.body);

    const cleanup = createFocusTrap(container);
    await waitFor(() => expect(document.activeElement).toBe(container.querySelector("button")));
    container.remove();
    cleanup();

    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it("forgets a pressed control once the pointer presses something that takes no focus", async () => {
    const pressed = document.body.appendChild(document.createElement("button"));
    const blank = document.body.appendChild(document.createElement("div"));
    const container = document.body.appendChild(document.createElement("div"));
    container.innerHTML = `<button>Close</button>`;
    fireEvent.pointerDown(pressed);
    fireEvent.pointerDown(blank);

    const cleanup = createFocusTrap(container);
    await waitFor(() => expect(document.activeElement).toBe(container.querySelector("button")));
    container.remove();
    cleanup();

    expect(document.activeElement).not.toBe(pressed);
    pressed.remove();
    blank.remove();
  });

  // React focuses an autoFocus field while it commits the dialog, before the
  // effect that creates the trap, so the trap saved that field as the opener
  // and on close sent focus to an element no longer in the page: <body>.
  it("is never paired with autoFocus in the same component", () => {
    const offenders = Object.entries(SOURCES)
      .filter(([file, source]) => source.includes("createFocusTrap") && jsxAttributes(file, source).includes("autoFocus"))
      .map(([file]) => file);
    expect(offenders, "mark the first field with data-autofocus, not autoFocus").toEqual([]);
  });
});

describe.each(DIALOGS)("the $name dialog", ({ closes, dialog }) => {
  it.each(closes)("returns focus to the button that opened it when %s closes it", async (close) => {
    const user = userEvent.setup();
    render(<Page dialog={dialog} />);
    const opener = await open(user);
    if (close === "Escape") await user.keyboard("{Escape}");
    else await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: close }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it.each(closes)("returns focus to that button in Safari, which a click does not focus, when %s closes it", async (close) => {
    const user = userEvent.setup();
    render(<Page dialog={dialog} />);
    const opener = await open(user, { safari: true });
    if (close === "Escape") await user.keyboard("{Escape}");
    else await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: close }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});

describe("the variable dialog", () => {
  it("opens on the Key field when adding and on the Value field when editing", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<Page dialog={DIALOGS[0].dialog} />);
    await open(user);
    expect(document.activeElement).toBe(screen.getByLabelText("Key"));
    unmount();

    render(<Page dialog={DIALOGS[1].dialog} />);
    await open(user);
    expect(document.activeElement).toBe(screen.getByLabelText("Value"));
  });
});
