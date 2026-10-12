import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "../test/server";
import PostureConfirmModal from "./PostureConfirmModal";

const ENGINE_KEY = "acme/widget";

function renderModal(onClose = vi.fn()) {
  const view = render(
    <PostureConfirmModal engineKey={ENGINE_KEY} currentPosture="propose" onClose={onClose} />,
  );
  return { ...view, onClose };
}

describe("PostureConfirmModal", () => {
  it("defaults the select to the current posture with Confirm disabled", () => {
    renderModal();
    expect(screen.getByTestId("posture-confirm-select")).toHaveValue("propose");
    expect(screen.getByTestId("posture-confirm-submit")).toBeDisabled();
  });

  it("enables Confirm once a different posture is selected", async () => {
    const user = userEvent.setup();
    renderModal();
    await user.selectOptions(screen.getByTestId("posture-confirm-select"), "autonomous");
    expect(screen.getByTestId("posture-confirm-submit")).not.toBeDisabled();
  });

  it("posts the selected posture with confirm=true and closes on success (no optimistic update)", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/engines/acme/widget/posture", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "autonomous" });
      }),
    );
    const user = userEvent.setup();
    const { onClose } = renderModal();
    await user.selectOptions(screen.getByTestId("posture-confirm-select"), "autonomous");
    await user.click(screen.getByTestId("posture-confirm-submit"));
    await vi.waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1);
    });
    expect(captured).toEqual({ posture: "autonomous", confirm: true });
  });

  it("shows an inline error and keeps the dialog open when the request fails", async () => {
    server.use(
      http.post("/api/engines/acme/widget/posture", () =>
        HttpResponse.json({ detail: "control token mismatch" }, { status: 403 }),
      ),
    );
    const user = userEvent.setup();
    const { onClose } = renderModal();
    await user.selectOptions(screen.getByTestId("posture-confirm-select"), "autonomous");
    await user.click(screen.getByTestId("posture-confirm-submit"));
    expect(await screen.findByTestId("posture-confirm-error")).toHaveTextContent(
      "control token mismatch",
    );
    expect(screen.getByTestId("posture-confirm-dialog")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByTestId("posture-confirm-submit")).not.toBeDisabled();
  });

  it("Escape closes the dialog when idle", () => {
    const { onClose } = renderModal();
    const form = screen.getByTestId("posture-confirm-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Ctrl+Enter confirms once a different posture is selected", async () => {
    server.use(
      http.post("/api/engines/acme/widget/posture", () =>
        HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "autonomous" }),
      ),
    );
    const user = userEvent.setup();
    const { onClose } = renderModal();
    await user.selectOptions(screen.getByTestId("posture-confirm-select"), "autonomous");
    const form = screen.getByTestId("posture-confirm-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    await vi.waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });

  it("Ctrl+Enter does nothing while the selection is unchanged", () => {
    renderModal();
    const form = screen.getByTestId("posture-confirm-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    expect(screen.getByTestId("posture-confirm-dialog")).toBeInTheDocument();
  });

  it("blocks Escape and Ctrl+Enter while a submit is in flight", async () => {
    server.use(
      http.post("/api/engines/acme/widget/posture", async () => {
        await new Promise(() => undefined);
        return HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "autonomous" });
      }),
    );
    const user = userEvent.setup();
    const { onClose } = renderModal();
    await user.selectOptions(screen.getByTestId("posture-confirm-select"), "autonomous");
    await user.click(screen.getByTestId("posture-confirm-submit"));
    expect(screen.getByTestId("posture-confirm-submit")).toHaveTextContent("Changing…");

    const form = screen.getByTestId("posture-confirm-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Escape" });
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    expect(screen.getByTestId("posture-confirm-dialog")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("targetPosture supplied: the free <select> is absent entirely and the locked target text renders", () => {
    render(
      <PostureConfirmModal
        engineKey={ENGINE_KEY}
        currentPosture="observe"
        targetPosture="propose"
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("posture-confirm-select")).not.toBeInTheDocument();
    expect(screen.getByTestId("posture-confirm-target")).toHaveTextContent("Propose");
    expect(screen.getByTestId("posture-confirm-submit")).not.toBeDisabled();
  });

  it("targetPosture omitted: the existing <select> with all four options renders unchanged (EngineOpsPanel regression guard)", () => {
    renderModal();
    const options = screen
      .getByTestId("posture-confirm-select")
      .querySelectorAll("option");
    expect(options).toHaveLength(4);
  });

  it("posts {posture: targetPosture, confirm: true} and fires onConfirmed before onClose on success", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/engines/acme/widget/posture", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "propose" });
      }),
    );
    const calls: string[] = [];
    const onConfirmed = vi.fn(() => calls.push("confirmed"));
    const onClose = vi.fn(() => calls.push("closed"));
    const user = userEvent.setup();
    render(
      <PostureConfirmModal
        engineKey={ENGINE_KEY}
        currentPosture="observe"
        targetPosture="propose"
        onClose={onClose}
        onConfirmed={onConfirmed}
      />,
    );
    await user.click(screen.getByTestId("posture-confirm-submit"));
    await vi.waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1);
    });
    expect(captured).toEqual({ posture: "propose", confirm: true });
    expect(onConfirmed).toHaveBeenCalledTimes(1);
    expect(calls).toEqual(["confirmed", "closed"]);
  });

  it("does not fire onConfirmed when the request is rejected", async () => {
    server.use(
      http.post("/api/engines/acme/widget/posture", () =>
        HttpResponse.json({ detail: "control token mismatch" }, { status: 403 }),
      ),
    );
    const onConfirmed = vi.fn();
    const user = userEvent.setup();
    render(
      <PostureConfirmModal
        engineKey={ENGINE_KEY}
        currentPosture="observe"
        targetPosture="propose"
        onClose={vi.fn()}
        onConfirmed={onConfirmed}
      />,
    );
    await user.click(screen.getByTestId("posture-confirm-submit"));
    await screen.findByTestId("posture-confirm-error");
    expect(onConfirmed).not.toHaveBeenCalled();
  });

  it("Cancel button closes without calling the server", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const user = userEvent.setup();
    const { onClose } = renderModal();
    const callsBefore = fetchSpy.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls.length).toBe(callsBefore);
    fetchSpy.mockRestore();
  });

  it("the dialog's native cancel event closes it when idle", () => {
    const { onClose } = renderModal();
    const dialog = screen.getByTestId("posture-confirm-dialog");
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("blocks the dialog's native cancel event while a submit is in flight", async () => {
    server.use(
      http.post("/api/engines/acme/widget/posture", async () => {
        await new Promise(() => undefined);
        return HttpResponse.json({ repo: { owner: "acme", repo: "widget" }, posture: "autonomous" });
      }),
    );
    const user = userEvent.setup();
    const { onClose } = renderModal();
    await user.selectOptions(screen.getByTestId("posture-confirm-select"), "autonomous");
    await user.click(screen.getByTestId("posture-confirm-submit"));

    const dialog = screen.getByTestId("posture-confirm-dialog");
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("ignores a plain form submit event", () => {
    const { onClose } = renderModal();
    const form = screen.getByTestId("posture-confirm-dialog").querySelector("form")!;
    fireEvent.submit(form);
    expect(screen.getByTestId("posture-confirm-dialog")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
