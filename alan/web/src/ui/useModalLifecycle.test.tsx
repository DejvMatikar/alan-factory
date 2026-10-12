import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { useModalLifecycle } from "./useModalLifecycle";

function TestModal({ onClose }: Readonly<{ onClose: () => void }>) {
  const dialogRef = useModalLifecycle();
  return (
    <dialog ref={dialogRef} data-testid="test-modal">
      <button type="button" onClick={onClose} data-testid="close">
        close
      </button>
    </dialog>
  );
}

function LauncherAndModal() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" data-testid="launcher" onClick={() => setOpen(true)}>
        Open
      </button>
      {open && <TestModal onClose={() => setOpen(false)} />}
    </>
  );
}

describe("useModalLifecycle", () => {
  it("calls showModal on mount and close on unmount", () => {
    const showModal = vi.spyOn(HTMLDialogElement.prototype, "showModal");
    const close = vi.spyOn(HTMLDialogElement.prototype, "close");
    const { unmount } = render(<TestModal onClose={() => undefined} />);
    expect(showModal).toHaveBeenCalled();
    unmount();
    expect(close).toHaveBeenCalled();
  });

  it("restores focus to the launching control once the modal unmounts", async () => {
    render(<LauncherAndModal />);
    const launcher = screen.getByTestId("launcher");
    const user = userEvent.setup();
    await user.click(launcher);
    expect(launcher).toHaveFocus();

    await user.click(screen.getByTestId("close"));

    expect(screen.queryByTestId("test-modal")).not.toBeInTheDocument();
    expect(launcher).toHaveFocus();
  });
});
