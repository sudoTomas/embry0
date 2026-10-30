/**
 * #541: the live screenshot and the evidence links must fetch bytes through
 * the orchestrator passthrough and hand the browser a blob: URL. The
 * `/jobs/{id}/artifacts/...` routes answer with a 302 to the compose-internal
 * storage endpoint, which no browser can reach.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

const mockGet = vi.fn();
vi.mock("@/api/client", () => ({
  api: { get: (...a: unknown[]) => mockGet(...a) },
}));

import { QAScreenshotPanel } from "../QAScreenshotPanel";
import { QAAcceptanceResults } from "../QAAcceptanceResults";

const REDIRECT_ROUTE = /\/artifacts\//;

let blobCounter = 0;
const createObjectURL = vi.fn(() => `blob:http://localhost/frame-${++blobCounter}`);
const revokeObjectURL = vi.fn();

beforeEach(() => {
  mockGet.mockReset();
  createObjectURL.mockClear();
  revokeObjectURL.mockClear();
  blobCounter = 0;
  Object.assign(URL, { createObjectURL, revokeObjectURL });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("QAScreenshotPanel (#541)", () => {
  it("renders the latest screenshot from a blob URL fetched through the passthrough", async () => {
    mockGet.mockResolvedValue({ data: new Blob(["png"], { type: "image/png" }) });
    render(<QAScreenshotPanel jobId="job-1" jobIsLive={false} />);

    const img = await screen.findByAltText("Latest QA screenshot");
    const src = img.getAttribute("src") ?? "";
    expect(src, `img src must be a blob: URL, not the redirect route; got ${src}`).toMatch(/^blob:/);
    expect(src).not.toMatch(REDIRECT_ROUTE);
    expect(mockGet).toHaveBeenCalledWith("/jobs/job-1/qa/screenshots/latest", { responseType: "blob" });
  });

  it("polls while live and revokes the previous frame", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockGet.mockResolvedValue({ data: new Blob(["png"], { type: "image/png" }) });
    const { unmount } = render(<QAScreenshotPanel jobId="job-1" jobIsLive={true} />);

    await waitFor(() => expect(screen.getByAltText("Latest QA screenshot")).toHaveAttribute("src", "blob:http://localhost/frame-1"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    await waitFor(() => expect(screen.getByAltText("Latest QA screenshot")).toHaveAttribute("src", "blob:http://localhost/frame-2"));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:http://localhost/frame-1");

    unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:http://localhost/frame-2");
  });

  it("shows the empty state on 404", async () => {
    mockGet.mockRejectedValue(Object.assign(new Error("nf"), { isAxiosError: true, response: { status: 404 } }));
    render(<QAScreenshotPanel jobId="job-1" jobIsLive={false} />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(screen.getByText("No screenshot yet")).toBeInTheDocument();
    expect(screen.queryByAltText("Latest QA screenshot")).toBeNull();
  });
});

describe("QAAcceptanceResults evidence (#541)", () => {
  const RESULTS = [
    {
      criterion: "login works",
      status: "passed" as const,
      evidence: ["screenshots/exploratory/1-login.png"],
      console_errors: [],
      network_failures: [],
      log_excerpts: [],
    },
  ];

  it("never links the browser to the redirect route", () => {
    render(<QAAcceptanceResults jobId="job-1" attemptN={2} results={RESULTS} />);
    for (const a of document.querySelectorAll("a")) {
      const href = a.getAttribute("href") ?? "";
      expect(href, `evidence anchor points at the storage redirect: ${href}`).not.toMatch(REDIRECT_ROUTE);
    }
    expect(screen.getByRole("button", { name: "1-login.png" })).toBeInTheDocument();
  });

  it("fetches the evidence of its own attempt, opens it as a blob URL and revokes it a minute later", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockGet.mockResolvedValue({ data: new Blob(["png"], { type: "image/png" }) });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.href).toMatch(/^blob:/);
    });
    render(<QAAcceptanceResults jobId="job-1" attemptN={2} results={RESULTS} />);

    fireEvent.click(screen.getByRole("button", { name: "1-login.png" }));

    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    expect(mockGet).toHaveBeenCalledWith("/jobs/job-1/qa/attempts/2/artifacts/screenshots/exploratory/1-login.png", {
      responseType: "blob",
    });
    click.mockRestore();

    // The new tab keeps the URL a minute, then the page frees the bytes.
    expect(revokeObjectURL).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:http://localhost/frame-1");
  });
});
