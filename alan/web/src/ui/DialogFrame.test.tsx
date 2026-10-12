import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import DialogFrame from "./DialogFrame";

const MD_CLASS =
  "fixed inset-0 m-auto w-[90vw] max-w-md rounded-lg border border-border bg-surface-elevated p-0 text-text-primary shadow-lg backdrop:bg-surface/80";
const LG_CLASS =
  "fixed inset-0 m-auto w-[90vw] max-w-lg rounded-lg border border-border bg-surface-elevated p-0 text-text-primary shadow-lg backdrop:bg-surface/80";

function renderFrame(props: Partial<ComponentProps<typeof DialogFrame>> = {}) {
  const onClose = props.onClose ?? vi.fn();
  const onSubmit = props.onSubmit ?? vi.fn();
  const testId = "testId" in props ? props.testId : "test-dialog";
  render(
    <DialogFrame
      size={props.size ?? "md"}
      ariaLabel={props.ariaLabel ?? "Test dialog"}
      testId={testId}
      busy={props.busy}
      onClose={onClose}
      onSubmit={onSubmit}
    >
      {props.children ?? <p>body</p>}
    </DialogFrame>,
  );
  return { onClose, onSubmit };
}

describe("DialogFrame", () => {
  it("emits the byte-identical md frame class", () => {
    renderFrame({ size: "md" });
    expect(screen.getByTestId("test-dialog").className).toBe(MD_CLASS);
  });

  it("emits the byte-identical lg frame class", () => {
    renderFrame({ size: "lg" });
    expect(screen.getByTestId("test-dialog").className).toBe(LG_CLASS);
  });

  it("sets the dialog's accessible name from ariaLabel", () => {
    renderFrame({ ariaLabel: "Custom label" });
    expect(screen.getByRole("dialog", { name: "Custom label" })).toBeInTheDocument();
  });

  it("sets data-testid when supplied", () => {
    renderFrame({ testId: "my-dialog" });
    expect(screen.getByTestId("my-dialog")).toBeInTheDocument();
  });

  it("omits data-testid when not supplied", () => {
    renderFrame({ testId: undefined });
    expect(screen.getByRole("dialog")).not.toHaveAttribute("data-testid");
  });

  it("renders children inside the form", () => {
    renderFrame({ children: <p data-testid="frame-child">hello</p> });
    const form = screen.getByRole("dialog").querySelector("form")!;
    expect(form.querySelector('[data-testid="frame-child"]')).toHaveTextContent("hello");
  });

  it("idle: Escape calls onClose", () => {
    const { onClose } = renderFrame();
    const form = screen.getByTestId("test-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("idle: Ctrl+Enter calls onSubmit", () => {
    const { onSubmit } = renderFrame();
    const form = screen.getByTestId("test-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("busy: Escape is a no-op", () => {
    const { onClose } = renderFrame({ busy: true });
    const form = screen.getByTestId("test-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("busy: Ctrl+Enter does not re-submit", () => {
    const { onSubmit } = renderFrame({ busy: true });
    const form = screen.getByTestId("test-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("idle: native cancel calls onClose exactly once", () => {
    const { onClose } = renderFrame();
    const dialog = screen.getByTestId("test-dialog");
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("idle: native cancel is prevented so the browser does not also close the dialog", () => {
    renderFrame();
    const dialog = screen.getByTestId("test-dialog");
    const cancelEvent = new Event("cancel", { cancelable: true });
    fireEvent(dialog, cancelEvent);
    expect(cancelEvent.defaultPrevented).toBe(true);
  });

  it("busy: native cancel does not call onClose", () => {
    const { onClose } = renderFrame({ busy: true });
    const dialog = screen.getByTestId("test-dialog");
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("ignores a plain form submit event", () => {
    const { onClose, onSubmit } = renderFrame();
    const form = screen.getByTestId("test-dialog").querySelector("form")!;
    fireEvent.submit(form);
    expect(onClose).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
