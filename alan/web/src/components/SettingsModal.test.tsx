import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { readControlToken, writeControlToken } from "../controlToken";
import { server } from "../test/server";
import SettingsModal from "./SettingsModal";

function LauncherAndModal() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" data-testid="launcher" onClick={() => setOpen(true)}>
        Open settings
      </button>
      {open && <SettingsModal onClose={() => setOpen(false)} />}
    </>
  );
}

function renderModal(onClose = vi.fn()) {
  return render(<SettingsModal onClose={onClose} />);
}

function containerOf(dialog: HTMLElement): HTMLElement {
  const container = dialog.querySelector("div");
  if (container === null) throw new Error("modal container div not found");
  return container;
}

describe("SettingsModal", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders a dialog with the visible label and a masked input by default", () => {
    renderModal();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText("Control token")).toHaveAttribute("type", "password");
  });

  it("seeds the input and indicator from a stored token", () => {
    writeControlToken("existing-token");
    renderModal();
    expect(screen.getByTestId("control-token-input")).toHaveValue("existing-token");
    expect(screen.getByTestId("control-token-status")).toHaveTextContent("Token set.");
  });

  it("seeds an empty input and 'No token set.' when nothing is stored", () => {
    renderModal();
    expect(screen.getByTestId("control-token-input")).toHaveValue("");
    expect(screen.getByTestId("control-token-status")).toHaveTextContent("No token set.");
  });

  it("is masked by default on every open, even with a stored token", () => {
    writeControlToken("existing-token");
    renderModal();
    expect(screen.getByTestId("control-token-input")).toHaveAttribute("type", "password");
  });

  it("toggle show/hide flips the input type and the accessible name", async () => {
    renderModal();
    const user = userEvent.setup();
    const toggle = screen.getByTestId("toggle-token-visibility");
    expect(toggle).toHaveAccessibleName("Show token");
    await user.click(toggle);
    expect(screen.getByTestId("control-token-input")).toHaveAttribute("type", "text");
    expect(toggle).toHaveAccessibleName("Hide token");
    await user.click(toggle);
    expect(screen.getByTestId("control-token-input")).toHaveAttribute("type", "password");
    expect(toggle).toHaveAccessibleName("Show token");
  });

  it("typing a draft does not change the persisted indicator", async () => {
    renderModal();
    const user = userEvent.setup();
    await user.type(screen.getByTestId("control-token-input"), "not-saved-yet");
    expect(screen.getByTestId("control-token-status")).toHaveTextContent("No token set.");
  });

  it("Save persists the draft and updates the indicator to 'Token set.'", async () => {
    renderModal();
    const user = userEvent.setup();
    await user.type(screen.getByTestId("control-token-input"), "new-token");
    await user.click(screen.getByTestId("save-token"));
    expect(readControlToken()).toBe("new-token");
    expect(screen.getByTestId("control-token-status")).toHaveTextContent("Token set.");
  });

  it("Clear removes the stored token, empties the input, and updates the indicator", async () => {
    writeControlToken("existing-token");
    renderModal();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("clear-token"));
    expect(readControlToken()).toBe("");
    expect(screen.getByTestId("control-token-input")).toHaveValue("");
    expect(screen.getByTestId("control-token-status")).toHaveTextContent("No token set.");
  });

  it("Escape closes the modal", () => {
    const onClose = vi.fn();
    renderModal(onClose);
    const container = containerOf(screen.getByTestId("settings-modal"));
    fireEvent.keyDown(container, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Ctrl+Enter saves the draft without closing the modal", async () => {
    const onClose = vi.fn();
    renderModal(onClose);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("control-token-input"), "ctrl-enter-token");
    const container = containerOf(screen.getByTestId("settings-modal"));
    fireEvent.keyDown(container, { key: "Enter", ctrlKey: true });
    expect(readControlToken()).toBe("ctrl-enter-token");
    expect(screen.getByTestId("control-token-status")).toHaveTextContent("Token set.");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("close button calls onClose", async () => {
    const onClose = vi.fn();
    renderModal(onClose);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("settings-modal-close"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking the dialog backdrop (outside the content) calls onClose", async () => {
    const onClose = vi.fn();
    renderModal(onClose);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("settings-modal"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking inside the modal content does not call onClose", async () => {
    const onClose = vi.fn();
    renderModal(onClose);
    const user = userEvent.setup();
    await user.click(screen.getByText("Settings"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("the native dialog cancel event closes the modal", () => {
    const onClose = vi.fn();
    renderModal(onClose);
    fireEvent(screen.getByTestId("settings-modal"), new Event("cancel"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("renders the control-token file path as plain text, not a link", () => {
    renderModal();
    const hint = screen.getByText(/control_token/);
    expect(hint.tagName).not.toBe("A");
  });

  it("shows the dialog on mount and closes it on unmount", () => {
    const showModal = vi.spyOn(HTMLDialogElement.prototype, "showModal");
    const close = vi.spyOn(HTMLDialogElement.prototype, "close");
    const { unmount } = renderModal();
    expect(showModal).toHaveBeenCalled();
    unmount();
    expect(close).toHaveBeenCalled();
  });

  it("renders the Credentials section below the existing control-token block", async () => {
    renderModal();
    expect(await screen.findByTestId("credentials-section")).toBeInTheDocument();
  });

  it("keeps the control-token status pill's class list byte-identical to HEAD", () => {
    writeControlToken("existing-token");
    renderModal();
    expect(screen.getByTestId("control-token-status")).toHaveClass(
      "inline-block", "rounded", "px-2", "py-0.5", "text-xs", "bg-green-500/15", "text-green-300",
    );
  });

  it("keeps the unset control-token status pill's class list byte-identical to HEAD", () => {
    renderModal();
    expect(screen.getByTestId("control-token-status")).toHaveClass(
      "inline-block", "rounded", "px-2", "py-0.5", "text-xs", "bg-zinc-500/15", "text-zinc-300",
    );
  });

  it("the settings dialog no longer contains the project-features switch", () => {
    renderModal();
    expect(screen.queryByTestId("project-features-toggle")).not.toBeInTheDocument();
    expect(screen.queryByText(/Show project features/)).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("the settings dialog renders a single divider between the control-token and credentials sections", async () => {
    renderModal();
    const credentials = await screen.findByTestId("credentials-section");
    const dividers = screen.getByTestId("settings-modal").querySelectorAll("hr");
    expect(dividers).toHaveLength(1);
    const tokenSection = screen.getByTestId("control-token-input");
    const divider = dividers[0]!;
    expect(tokenSection.compareDocumentPosition(divider) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(divider.compareDocumentPosition(credentials) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders the server lifecycle line, separate from the local paste indicator, and the three lifecycle actions", async () => {
    server.use(
      http.get("/api/control-token/status", () =>
        HttpResponse.json({ state: "active", deadline: "2026-09-14T00:00:00+00:00" }),
      ),
    );
    renderModal();
    const serverLine = screen.getByTestId("control-token-server-status");
    await waitFor(() => {
      expect(serverLine).toHaveTextContent(/^Server token: active, expires/);
    });
    expect(serverLine).not.toHaveTextContent("2026-09-14T00:00:00+00:00");
    expect(screen.getByTestId("control-token-status")).toHaveTextContent("No token set.");
    expect(screen.getByTestId("control-token-extend")).toBeInTheDocument();
    expect(screen.getByTestId("control-token-replace")).toBeInTheDocument();
    expect(screen.getByTestId("control-token-revoke")).toBeInTheDocument();
  });

  it("focus returns to the launching control once the modal closes", async () => {
    render(<LauncherAndModal />);
    const launcher = screen.getByTestId("launcher");
    const user = userEvent.setup();
    await user.click(launcher);
    expect(launcher).toHaveFocus();

    const container = containerOf(screen.getByTestId("settings-modal"));
    fireEvent.keyDown(container, { key: "Escape" });

    expect(screen.queryByTestId("settings-modal")).not.toBeInTheDocument();
    expect(launcher).toHaveFocus();
  });
});
