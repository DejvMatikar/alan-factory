import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { describe, expect, it, vi } from "vitest";

import SendOrStopGroup from "./SendOrStopGroup";

interface HarnessProps {
  thinking: boolean;
  stopping?: boolean;
  disabled?: boolean;
  draft?: string;
  onStop: () => void;
  stopTestId?: string;
}

function Harness({
  thinking,
  stopping = false,
  disabled = false,
  draft = "hi",
  onStop,
  stopTestId,
}: Readonly<HarnessProps>) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  return (
    <>
      <button type="button" data-testid="other-control">
        other
      </button>
      <textarea ref={textareaRef} aria-label="draft" />
      <SendOrStopGroup
        thinking={thinking}
        stopping={stopping}
        disabled={disabled}
        draft={draft}
        onStop={onStop}
        textareaRef={textareaRef}
        {...(stopTestId !== undefined ? { stopTestId } : {})}
      />
    </>
  );
}

describe("SendOrStopGroup", () => {
  it("renders Send (disabled while draft empty) when not thinking", () => {
    render(<Harness thinking={false} draft="" onStop={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Send/ })).toBeDisabled();
  });

  it("enables Send when draft is non-empty and not disabled", () => {
    render(<Harness thinking={false} draft="hi" onStop={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Send/ })).not.toBeDisabled();
  });

  it("disables Send when disabled prop is set", () => {
    render(<Harness thinking={false} draft="hi" disabled onStop={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Send/ })).toBeDisabled();
  });

  it("renders Queue and Stop when thinking", () => {
    render(<Harness thinking draft="x" onStop={vi.fn()} />);
    expect(screen.getByTestId("queue-button")).toBeInTheDocument();
    expect(screen.getByTestId("stop-button")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Send/ })).toBeNull();
  });

  it("carries the send shortcut for both modifier keys in the button's accessible name, with no visible help prose", () => {
    render(<Harness thinking={false} draft="hi" onStop={vi.fn()} />);
    expect(
      screen.getByRole("button", { name: /Send.*shortcut Command Enter or Control Enter/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Press ⌘/)).not.toBeInTheDocument();
  });

  it("carries the queue and stop shortcuts in their buttons' accessible names", () => {
    render(<Harness thinking draft="x" onStop={vi.fn()} />);
    expect(screen.getByTestId("queue-button")).toHaveAccessibleName(
      /shortcut Command Enter or Control Enter/,
    );
    expect(screen.getByTestId("stop-button")).toHaveAccessibleName(/shortcut Escape/);
  });

  it("disables Queue when draft is empty", () => {
    render(<Harness thinking draft="" onStop={vi.fn()} />);
    expect(screen.getByTestId("queue-button")).toBeDisabled();
  });

  it("calls onStop when Stop is clicked", async () => {
    const user = userEvent.setup();
    const onStop = vi.fn();
    render(<Harness thinking onStop={onStop} />);
    await user.click(screen.getByTestId("stop-button"));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("calls onStop when Escape is pressed while thinking", () => {
    const onStop = vi.fn();
    render(<Harness thinking onStop={onStop} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("Escape is a no-op when not thinking", () => {
    const onStop = vi.fn();
    render(<Harness thinking={false} onStop={onStop} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onStop).not.toHaveBeenCalled();
  });

  it("Escape is a no-op while already stopping, avoiding a duplicate interrupt", () => {
    const onStop = vi.fn();
    render(<Harness thinking stopping onStop={onStop} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onStop).not.toHaveBeenCalled();
  });

  it("shows the Stopping… label, aria-busy, and disables Stop while stopping", () => {
    render(<Harness thinking stopping onStop={vi.fn()} />);
    const stopButton = screen.getByTestId("stop-button");
    expect(stopButton).toHaveTextContent("Stopping…");
    expect(stopButton).toBeDisabled();
    expect(stopButton).toHaveAttribute("aria-busy", "true");
  });

  it("announces the stopping state via the live region", () => {
    render(<Harness thinking stopping onStop={vi.fn()} />);
    expect(screen.getByTestId("stop-status")).toHaveTextContent(
      "Stopping the current turn…",
    );
  });

  it("live region is empty when not stopping", () => {
    render(<Harness thinking stopping={false} onStop={vi.fn()} />);
    expect(screen.getByTestId("stop-status")).toHaveTextContent("");
  });

  it("honors a custom stopTestId", () => {
    render(<Harness thinking onStop={vi.fn()} stopTestId="ws-stop-button" />);
    expect(screen.getByTestId("ws-stop-button")).toBeInTheDocument();
    expect(screen.queryByTestId("stop-button")).toBeNull();
  });

  it("moves focus to the textarea after a stop completes, when the stop button had focus", () => {
    const onStop = vi.fn();
    const { rerender } = render(<Harness thinking onStop={onStop} />);
    screen.getByTestId("stop-button").focus();
    fireEvent.click(screen.getByTestId("stop-button"));
    rerender(<Harness thinking={false} onStop={onStop} />);
    expect(screen.getByLabelText("draft")).toHaveFocus();
  });

  it("does not move focus to the textarea when the stop control did not have focus", () => {
    const onStop = vi.fn();
    const { rerender } = render(<Harness thinking onStop={onStop} />);
    screen.getByTestId("other-control").focus();
    fireEvent.keyDown(document, { key: "Escape" });
    rerender(<Harness thinking={false} onStop={onStop} />);
    expect(screen.getByLabelText("draft")).not.toHaveFocus();
    expect(screen.getByTestId("other-control")).toHaveFocus();
  });
});
