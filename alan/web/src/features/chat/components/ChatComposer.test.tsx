import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { FormEvent } from "react";
import { describe, expect, it, vi } from "vitest";

import { AUTO_DISPATCH_IDLE } from "../hooks/autoDispatch";
import ChatComposer, { type ChatComposerProps } from "./ChatComposer";

function composerProps(overrides: Partial<ChatComposerProps> = {}): ChatComposerProps {
  return {
    draft: "",
    setDraft: vi.fn(),
    onSubmit: vi.fn((e: FormEvent) => e.preventDefault()),
    disabled: false,
    onClear: vi.fn(),
    onSummarize: vi.fn(),
    onStop: vi.fn(),
    summarizing: false,
    sessionReady: true,
    thinking: false,
    stopping: false,
    onAutoDispatch: vi.fn(),
    autoDispatch: AUTO_DISPATCH_IDLE,
    ...overrides,
  };
}

function renderComposer(overrides: Partial<ChatComposerProps> = {}) {
  const props = composerProps(overrides);
  render(<ChatComposer {...props} />);
  return props;
}

describe("ChatComposer", () => {
  it("renders Send (disabled while draft empty) when not thinking", () => {
    renderComposer({ draft: "" });
    expect(screen.getByRole("button", { name: /Send/ })).toBeDisabled();
  });

  it("enables Send when draft is non-empty", () => {
    renderComposer({ draft: "hello" });
    expect(screen.getByRole("button", { name: /Send/ })).not.toBeDisabled();
  });

  it("submits the form when Send is clicked", async () => {
    const user = userEvent.setup();
    const props = renderComposer({ draft: "hi" });
    await user.click(screen.getByRole("button", { name: /Send/ }));
    expect(props.onSubmit).toHaveBeenCalled();
  });

  it("disables Send when disabled prop is set", () => {
    renderComposer({ draft: "hi", disabled: true });
    expect(screen.getByRole("button", { name: /Send/ })).toBeDisabled();
  });

  it("shows Queue and Stop when thinking", () => {
    renderComposer({ thinking: true, draft: "x" });
    expect(screen.getByTestId("queue-button")).toBeInTheDocument();
    expect(screen.getByTestId("stop-button")).toBeInTheDocument();
  });

  it("disables Queue when draft empty", () => {
    renderComposer({ thinking: true, draft: "" });
    expect(screen.getByTestId("queue-button")).toBeDisabled();
  });

  it("calls onStop when Stop clicked", async () => {
    const user = userEvent.setup();
    const props = renderComposer({ thinking: true, draft: "x" });
    await user.click(screen.getByTestId("stop-button"));
    expect(props.onStop).toHaveBeenCalled();
  });

  it("calls onStop on Escape while thinking", () => {
    const props = renderComposer({ thinking: true, draft: "x" });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(props.onStop).toHaveBeenCalled();
  });

  it("shows Stopping… and disables Stop while stopping", () => {
    renderComposer({ thinking: true, draft: "x", stopping: true });
    const stopButton = screen.getByTestId("stop-button");
    expect(stopButton).toHaveTextContent("Stopping…");
    expect(stopButton).toBeDisabled();
  });

  it("defaults stopping to false when omitted", () => {
    render(
      <ChatComposer
        draft="x"
        setDraft={vi.fn()}
        onSubmit={vi.fn((e: FormEvent) => e.preventDefault())}
        disabled={false}
        onClear={vi.fn()}
        onSummarize={vi.fn()}
        onStop={vi.fn()}
        summarizing={false}
        sessionReady
        thinking
        onAutoDispatch={vi.fn()}
        autoDispatch={AUTO_DISPATCH_IDLE}
      />,
    );
    expect(screen.getByTestId("stop-button")).toHaveTextContent("Stop");
  });

  it("calls setDraft on input", async () => {
    const user = userEvent.setup();
    const props = renderComposer();
    await user.type(screen.getByLabelText("Message"), "a");
    expect(props.setDraft).toHaveBeenCalled();
  });

  it("submits on ⌘+Enter", async () => {
    const user = userEvent.setup();
    const props = renderComposer({ draft: "hi" });
    await user.type(screen.getByLabelText("Message"), "{Meta>}{Enter}{/Meta}");
    expect(props.onSubmit).toHaveBeenCalled();
  });

  it("submits on Ctrl+Enter", async () => {
    const user = userEvent.setup();
    const props = renderComposer({ draft: "hi" });
    await user.type(screen.getByLabelText("Message"), "{Control>}{Enter}{/Control}");
    expect(props.onSubmit).toHaveBeenCalled();
  });

  it("does not submit on plain Enter", async () => {
    const user = userEvent.setup();
    const props = renderComposer({ draft: "hi" });
    screen.getByLabelText("Message").focus();
    await user.keyboard("{Enter}");
    expect(props.onSubmit).not.toHaveBeenCalled();
  });

  it("removes the help prose sentence and carries the shortcuts in accessible names instead", () => {
    renderComposer();
    expect(screen.queryByText(/Press ⌘\/Ctrl\+Enter/)).not.toBeInTheDocument();
    expect(screen.getByTestId("clear-button")).toHaveAccessibleName(/shortcut Control Shift K/);
    expect(screen.getByTestId("summarize-button")).toHaveAccessibleName(/shortcut Control Shift S/);
  });

  it("calls onClear and disables Clear when no session", async () => {
    const user = userEvent.setup();
    const props = renderComposer();
    await user.click(screen.getByTestId("clear-button"));
    expect(props.onClear).toHaveBeenCalled();
    renderComposer({ sessionReady: false });
    expect(screen.getAllByTestId("clear-button")[1]).toBeDisabled();
  });

  it("calls onSummarize and shows the resting label", async () => {
    const user = userEvent.setup();
    const props = renderComposer();
    const btn = screen.getByTestId("summarize-button");
    expect(btn).toHaveTextContent("Summarize");
    await user.click(btn);
    expect(props.onSummarize).toHaveBeenCalled();
  });

  it("shows Summarizing… and disables while summarizing", () => {
    renderComposer({ summarizing: true });
    const btn = screen.getByTestId("summarize-button");
    expect(btn).toHaveTextContent("Summarizing…");
    expect(btn).toBeDisabled();
  });

  it("focuses the textarea when enabled", () => {
    renderComposer({ disabled: false });
    expect(screen.getByLabelText("Message")).toHaveFocus();
  });

  it("does not focus the textarea when disabled", () => {
    renderComposer({ disabled: true });
    expect(screen.getByLabelText("Message")).not.toHaveFocus();
  });

  it("keeps focus on another element when the session becomes ready while it holds focus", () => {
    function Wrapper({ disabled }: Readonly<{ disabled: boolean }>) {
      return (
        <div>
          <input data-testid="sibling" />
          <ChatComposer
            draft=""
            setDraft={vi.fn()}
            onSubmit={vi.fn((e: FormEvent) => e.preventDefault())}
            disabled={disabled}
            onClear={vi.fn()}
            onSummarize={vi.fn()}
            onStop={vi.fn()}
            summarizing={false}
            sessionReady={false}
            thinking={false}
            onAutoDispatch={vi.fn()}
            autoDispatch={AUTO_DISPATCH_IDLE}
              />
        </div>
      );
    }
    const { rerender } = render(<Wrapper disabled={true} />);
    const sibling = screen.getByTestId("sibling");
    sibling.focus();
    expect(sibling).toHaveFocus();
    rerender(<Wrapper disabled={false} />);
    expect(sibling).toHaveFocus();
    expect(screen.getByLabelText("Message")).not.toHaveFocus();
  });

  it("re-takes focus on every fresh mount while the body is active (covers ChatPanel's conditional mount)", () => {
    const { unmount } = render(<ChatComposer {...composerProps({ disabled: false })} />);
    expect(screen.getByLabelText("Message")).toHaveFocus();
    unmount();
    render(<ChatComposer {...composerProps({ disabled: false })} />);
    expect(screen.getByLabelText("Message")).toHaveFocus();
  });

  it("shows a keyboard-focus ring matching the shared button focus ring", () => {
    renderComposer();
    const textarea = screen.getByLabelText("Message");
    expect(textarea.className).toContain("focus:ring-2");
    expect(textarea.className).toContain("focus:ring-text-primary/60");
  });

  it("applies an explicit rows value", () => {
    renderComposer({ rows: 12 });
    expect(screen.getByLabelText("Message")).toHaveAttribute("rows", "12");
  });

  it("defaults rows to 6", () => {
    renderComposer();
    expect(screen.getByLabelText("Message")).toHaveAttribute("rows", "6");
  });

  it("renders no delete button when deleteAction is omitted", () => {
    renderComposer();
    expect(screen.queryByTestId("delete-chat-button")).not.toBeInTheDocument();
  });

  it("renders a destroy-variant delete button when deleteAction is supplied", () => {
    renderComposer({ deleteAction: { onRequestDelete: vi.fn(), deleting: false } });
    const btn = screen.getByTestId("delete-chat-button");
    expect(btn).toHaveTextContent("Delete chat");
    expect(btn.className).toContain("bg-btn-destroy");
    expect(btn.className).toContain("min-h-11");
    expect(btn.className).toContain("min-w-11");
  });

  it("disables the delete button when the session is not ready", () => {
    renderComposer({
      sessionReady: false,
      deleteAction: { onRequestDelete: vi.fn(), deleting: false },
    });
    expect(screen.getByTestId("delete-chat-button")).toBeDisabled();
  });

  it("disables and relabels the delete button while deleting", () => {
    renderComposer({ deleteAction: { onRequestDelete: vi.fn(), deleting: true } });
    const btn = screen.getByTestId("delete-chat-button");
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent("Deleting…");
  });

  it("calls onRequestDelete on click", async () => {
    const user = userEvent.setup();
    const onRequestDelete = vi.fn();
    renderComposer({ deleteAction: { onRequestDelete, deleting: false } });
    await user.click(screen.getByTestId("delete-chat-button"));
    expect(onRequestDelete).toHaveBeenCalledTimes(1);
  });

  it("calls onRequestDelete on keyboard activation", async () => {
    const user = userEvent.setup();
    const onRequestDelete = vi.fn();
    renderComposer({ deleteAction: { onRequestDelete, deleting: false } });
    screen.getByTestId("delete-chat-button").focus();
    await user.keyboard("{Enter}");
    expect(onRequestDelete).toHaveBeenCalledTimes(1);
  });

  it("calls onAutoDispatch when the auto-dispatch button is clicked", async () => {
    const user = userEvent.setup();
    const props = renderComposer();
    await user.click(screen.getByTestId("auto-dispatch-button"));
    expect(props.onAutoDispatch).toHaveBeenCalledTimes(1);
  });

  it("disables the auto-dispatch button while summarizing", () => {
    renderComposer({ summarizing: true });
    expect(screen.getByTestId("auto-dispatch-button")).toBeDisabled();
  });

  it("disables the auto-dispatch button when the session is not ready", () => {
    renderComposer({ sessionReady: false });
    expect(screen.getByTestId("auto-dispatch-button")).toBeDisabled();
  });

  it("disables the auto-dispatch button while thinking", () => {
    renderComposer({ thinking: true, draft: "x" });
    expect(screen.getByTestId("auto-dispatch-button")).toBeDisabled();
  });

  it("separates the composer from the transcript above it by more than it separates its own field from its own buttons", () => {
    renderComposer();
    const form = screen.getByLabelText("Message").closest("form");
    expect(form).not.toBeNull();
    expect(form?.className).toContain("mt-4");
    expect(form?.className).toContain("gap-2");
  });

  it("shows the running label and disables the button while auto dispatch is running", () => {
    renderComposer({ autoDispatch: { running: true, stage: "summarizing", stop: null } });
    const btn = screen.getByTestId("auto-dispatch-button");
    expect(btn).toHaveTextContent("Summarizing & dispatching…");
    expect(btn).toBeDisabled();
  });
});
