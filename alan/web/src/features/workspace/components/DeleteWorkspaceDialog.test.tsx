import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "../../../test/server";
import DeleteWorkspaceDialog from "./DeleteWorkspaceDialog";

const MAC_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15";
const LINUX_UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

function renderDialog() {
  const onClose = vi.fn();
  const onDeleted = vi.fn();
  render(<DeleteWorkspaceDialog taskId="t1" onClose={onClose} onDeleted={onDeleted} />);
  return { onClose, onDeleted };
}

function formOf(): HTMLFormElement {
  return screen.getByTestId("delete-dialog").querySelector("form")!;
}

describe("DeleteWorkspaceDialog", () => {
  it("renders inside the shared DialogFrame frame", () => {
    renderDialog();
    expect(screen.getByTestId("delete-dialog").className).toBe(
      "fixed inset-0 m-auto w-[90vw] max-w-md rounded-lg border border-border bg-surface-elevated p-0 text-text-primary shadow-lg backdrop:bg-surface/80",
    );
  });

  it("shows the consequence text naming the workspace folder", () => {
    renderDialog();
    const dialog = screen.getByTestId("delete-dialog");
    expect(dialog).toHaveTextContent("~/.alan/workspaces/t1");
    expect(dialog).toHaveTextContent(/Claude Code sessions/);
    expect(dialog).toHaveTextContent(/are not/);
  });

  it("Escape cancels", () => {
    const { onClose } = renderDialog();
    fireEvent.keyDown(formOf(), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Ctrl+Enter confirms", async () => {
    server.use(
      http.delete("/api/workspaces/t1", () => new HttpResponse(null, { status: 204 })),
    );
    const { onDeleted } = renderDialog();
    fireEvent.keyDown(formOf(), { key: "Enter", ctrlKey: true });
    await waitFor(() => {
      expect(onDeleted).toHaveBeenCalledTimes(1);
    });
  });

  it("neither Escape nor Ctrl+Enter dismiss the dialog while a delete is in progress", async () => {
    let resolveDelete: (() => void) | undefined;
    server.use(
      http.delete(
        "/api/workspaces/t1",
        () =>
          new Promise<Response>((resolve) => {
            resolveDelete = () => resolve(new HttpResponse(null, { status: 204 }));
          }),
      ),
    );
    const { onClose, onDeleted } = renderDialog();
    await userEvent.setup().click(screen.getByRole("button", { name: /^Delete workspace/ }));
    await screen.findByRole("button", { name: "Deleting…" });

    fireEvent.keyDown(formOf(), { key: "Escape" });
    fireEvent.keyDown(formOf(), { key: "Enter", ctrlKey: true });
    expect(onClose).not.toHaveBeenCalled();
    expect(onDeleted).not.toHaveBeenCalled();
    expect(screen.getByTestId("delete-dialog")).toBeInTheDocument();

    resolveDelete?.();
    await waitFor(() => {
      expect(onDeleted).toHaveBeenCalledTimes(1);
    });
  });

  it("shows the Ctrl+Enter hint glyph on an Apple platform, with an accessible name naming the shortcut", () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(MAC_UA);
    renderDialog();
    const button = screen.getByRole("button", { name: /^Delete workspace/ });
    expect(button.textContent).toContain("⌃↵");
    expect(button).toHaveAccessibleName(/shortcut Control Enter/);
  });

  it("shows the Ctrl Enter hint text on a non-Apple platform", () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(LINUX_UA);
    renderDialog();
    const button = screen.getByRole("button", { name: /^Delete workspace/ });
    expect(button.textContent).toContain("Ctrl Enter");
  });

  it("hides the shortcut hint while a delete is in progress", async () => {
    server.use(
      http.delete(
        "/api/workspaces/t1",
        () => new Promise<Response>(() => undefined),
      ),
    );
    renderDialog();
    await userEvent.setup().click(screen.getByRole("button", { name: /^Delete workspace/ }));
    const button = await screen.findByRole("button", { name: "Deleting…" });
    expect(button.textContent).not.toContain("⌃↵");
    expect(button.textContent).not.toContain("Ctrl Enter");
  });

  it("Cancel is ghost and confirm is destroy", () => {
    renderDialog();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveAttribute("data-variant", "ghost");
    expect(screen.getByRole("button", { name: /^Delete workspace/ })).toHaveAttribute(
      "data-variant",
      "destroy",
    );
  });

  it("shows the Deleting… busy label while the request is in flight", async () => {
    server.use(
      http.delete(
        "/api/workspaces/t1",
        () => new Promise<Response>(() => undefined),
      ),
    );
    renderDialog();
    await userEvent.setup().click(screen.getByRole("button", { name: /^Delete workspace/ }));
    expect(await screen.findByRole("button", { name: "Deleting…" })).toBeDisabled();
  });

  it("shows an error banner on failure and clears busy so the buttons re-enable", async () => {
    server.use(
      http.delete("/api/workspaces/t1", () =>
        HttpResponse.json({ detail: "workspace has active jobs" }, { status: 409 }),
      ),
    );
    renderDialog();
    const confirm = screen.getByRole("button", { name: /^Delete workspace/ });
    await userEvent.setup().click(confirm);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("workspace has active jobs");
    expect(confirm).not.toBeDisabled();
  });

  it("calls onDeleted on success", async () => {
    server.use(
      http.delete("/api/workspaces/t1", () => new HttpResponse(null, { status: 204 })),
    );
    const { onDeleted } = renderDialog();
    await userEvent.setup().click(screen.getByRole("button", { name: /^Delete workspace/ }));
    await waitFor(() => {
      expect(onDeleted).toHaveBeenCalledTimes(1);
    });
  });
});
