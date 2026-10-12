import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ConfirmDestructiveDialog from "./ConfirmDestructiveDialog";

function renderDialog() {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <ConfirmDestructiveDialog
      title="Delete this chat?"
      body="This can't be undone."
      confirmLabel="Delete chat"
      onConfirm={onConfirm}
      onCancel={onCancel}
      testId="confirm-destructive-dialog"
    />,
  );
  return { onConfirm, onCancel };
}

describe("ConfirmDestructiveDialog", () => {
  it("renders the title, body, and confirm label", () => {
    renderDialog();
    expect(screen.getByText("Delete this chat?")).toBeInTheDocument();
    expect(screen.getByText("This can't be undone.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete chat" })).toBeInTheDocument();
  });

  it("has an accessible name matching the title", () => {
    renderDialog();
    expect(screen.getByRole("dialog", { name: "Delete this chat?" })).toBeInTheDocument();
  });

  it("calls onConfirm when the confirm button is clicked", async () => {
    const user = userEvent.setup();
    const { onConfirm, onCancel } = renderDialog();
    await user.click(screen.getByRole("button", { name: "Delete chat" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("calls onCancel when Cancel is clicked", async () => {
    const user = userEvent.setup();
    const { onCancel, onConfirm } = renderDialog();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("calls onCancel on Escape", () => {
    const { onCancel } = renderDialog();
    const form = screen.getByTestId("confirm-destructive-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel on the dialog's native cancel event", () => {
    const { onCancel } = renderDialog();
    const dialog = screen.getByTestId("confirm-destructive-dialog");
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("renders the confirm button with the destroy variant by default", () => {
    renderDialog();
    expect(screen.getByRole("button", { name: "Delete chat" }).className).toContain(
      "bg-btn-destroy",
    );
  });

  it("renders the confirm button in the reset (amber) variant when confirmVariant='reset'", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConfirmDestructiveDialog
        title="Replace the control token?"
        body="This issues a new value and signs out every other browser."
        confirmLabel="Replace token"
        confirmVariant="reset"
        onConfirm={onConfirm}
        onCancel={onCancel}
        testId="confirm-destructive-dialog"
      />,
    );
    const confirmBtn = screen.getByRole("button", { name: "Replace token" });
    expect(confirmBtn.className).toContain("bg-btn-reset");
    expect(confirmBtn.className).not.toContain("bg-btn-destroy");
  });

  it("gives both buttons the minimum 44px tap target", () => {
    renderDialog();
    for (const btn of [
      screen.getByRole("button", { name: "Cancel" }),
      screen.getByRole("button", { name: "Delete chat" }),
    ]) {
      expect(btn.className).toContain("min-h-11");
      expect(btn.className).toContain("min-w-11");
    }
  });

  it("ignores a plain form submit event", () => {
    const { onConfirm, onCancel } = renderDialog();
    const form = screen.getByTestId("confirm-destructive-dialog").querySelector("form")!;
    fireEvent.submit(form);
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onCancel).not.toHaveBeenCalled();
  });
});
