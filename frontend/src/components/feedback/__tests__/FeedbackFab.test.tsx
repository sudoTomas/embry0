import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Phase 5: global bug-report / feature-request FAB. Mounted once at AppLayout,
// fixed to the bottom-right of the viewport, opens a modal with
// category/severity/urgency + title/body + optional screenshot capture, and
// posts via the Companion agent's `/feedback` intake.

const mockSubmit = vi.fn();
const mockCapture = vi.fn();

vi.mock("@/api/agent", () => ({
  submitFeedback: (...args: unknown[]) => mockSubmit(...args),
}));

vi.mock("@/lib/capture-screen", () => ({
  captureScreen: (...args: unknown[]) => mockCapture(...args),
}));

import { FeedbackFab } from "../FeedbackFab";

beforeEach(() => {
  mockSubmit.mockReset();
  mockCapture.mockReset();
});

describe("FeedbackFab", () => {
  it("renders the floating action button with an accessible label", () => {
    render(<FeedbackFab />);
    const btn = screen.getByTestId("feedback-fab");
    expect(btn).toBeInTheDocument();
    expect(btn).toHaveAccessibleName(/feedback/i);
  });

  it("modal is hidden until the FAB is clicked", () => {
    render(<FeedbackFab />);
    expect(screen.queryByTestId("feedback-modal")).toBeNull();
    fireEvent.click(screen.getByTestId("feedback-fab"));
    const modal = screen.getByTestId("feedback-modal");
    expect(modal).toBeInTheDocument();
    expect(modal).toHaveAttribute("role", "dialog");
    expect(modal).toHaveAttribute("aria-modal", "true");
  });

  it("modal exposes category / severity / urgency / title / body inputs", () => {
    render(<FeedbackFab />);
    fireEvent.click(screen.getByTestId("feedback-fab"));
    expect(screen.getByLabelText(/category/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/severity/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/urgency/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/title/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/details/i)).toBeInTheDocument();
  });

  it("close button dismisses the modal", () => {
    render(<FeedbackFab />);
    fireEvent.click(screen.getByTestId("feedback-fab"));
    fireEvent.click(screen.getByTestId("feedback-modal-close"));
    expect(screen.queryByTestId("feedback-modal")).toBeNull();
  });

  it("Escape dismisses the modal", () => {
    render(<FeedbackFab />);
    fireEvent.click(screen.getByTestId("feedback-fab"));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("feedback-modal")).toBeNull();
  });

  it("submit POSTs the form values via submitFeedback and closes the modal", async () => {
    mockSubmit.mockResolvedValue(undefined);
    render(<FeedbackFab />);
    fireEvent.click(screen.getByTestId("feedback-fab"));

    fireEvent.change(screen.getByLabelText(/category/i), { target: { value: "bug" } });
    fireEvent.change(screen.getByLabelText(/severity/i), { target: { value: "high" } });
    fireEvent.change(screen.getByLabelText(/urgency/i), { target: { value: "medium" } });
    fireEvent.change(screen.getByLabelText(/title/i), { target: { value: "modal hangs" } });
    fireEvent.change(screen.getByLabelText(/details/i), { target: { value: "after submit" } });

    fireEvent.click(screen.getByTestId("feedback-submit"));

    await waitFor(() => expect(mockSubmit).toHaveBeenCalledTimes(1));
    expect(mockSubmit).toHaveBeenCalledWith({
      category: "bug",
      severity: "high",
      urgency: "medium",
      title: "modal hangs",
      body: "after submit",
    });
    await waitFor(() => expect(screen.queryByTestId("feedback-modal")).toBeNull());
  });

  it("blocks submit when title is empty (validation guard)", () => {
    render(<FeedbackFab />);
    fireEvent.click(screen.getByTestId("feedback-fab"));
    fireEvent.click(screen.getByTestId("feedback-submit"));
    expect(mockSubmit).not.toHaveBeenCalled();
  });

  it("capture button calls captureScreen and shows an attached indicator", async () => {
    const shot = new Blob([new Uint8Array([1])], { type: "image/png" });
    mockCapture.mockResolvedValue(shot);
    render(<FeedbackFab />);
    fireEvent.click(screen.getByTestId("feedback-fab"));
    fireEvent.click(screen.getByTestId("feedback-capture"));
    await waitFor(() =>
      expect(screen.getByTestId("feedback-screenshot-attached")).toBeInTheDocument(),
    );
  });

  it("submit forwards an attached screenshot blob to submitFeedback", async () => {
    const shot = new Blob([new Uint8Array([1])], { type: "image/png" });
    mockCapture.mockResolvedValue(shot);
    mockSubmit.mockResolvedValue(undefined);
    render(<FeedbackFab />);
    fireEvent.click(screen.getByTestId("feedback-fab"));
    fireEvent.click(screen.getByTestId("feedback-capture"));
    await waitFor(() =>
      expect(screen.getByTestId("feedback-screenshot-attached")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText(/title/i), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("feedback-submit"));
    await waitFor(() => expect(mockSubmit).toHaveBeenCalledTimes(1));
    expect(mockSubmit.mock.calls[0][0]).toMatchObject({
      title: "x",
      screenshot: shot,
    });
  });

  it("surfaces an error message when submitFeedback rejects", async () => {
    mockSubmit.mockRejectedValue(new Error("nope"));
    render(<FeedbackFab />);
    fireEvent.click(screen.getByTestId("feedback-fab"));
    fireEvent.change(screen.getByLabelText(/title/i), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("feedback-submit"));
    expect(await screen.findByTestId("feedback-error")).toBeInTheDocument();
    // Modal stays open so the user can retry.
    expect(screen.getByTestId("feedback-modal")).toBeInTheDocument();
  });
});
