import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "../test/server";
import NewProjectModal from "./NewProjectModal";

function renderModal(onCreated = vi.fn(), onClose = vi.fn()) {
  return { onCreated, onClose, ...render(<NewProjectModal onClose={onClose} onCreated={onCreated} />) };
}

describe("NewProjectModal", () => {
  it("disables Create project until a title is entered", async () => {
    renderModal();
    const user = userEvent.setup();
    expect(screen.getByTestId("new-project-submit")).toBeDisabled();
    await user.type(screen.getByTestId("new-project-title"), "New Thing");
    expect(screen.getByTestId("new-project-submit")).not.toBeDisabled();
  });

  it("submits the title and a trimmed doc_ref, calling onCreated with the created project", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/projects", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(
          {
            id: "new-thing",
            title: "New Thing",
            status: "active",
            doc_ref: "docs/x.md",
            created_at: "2026-06-01T00:00:00Z",
            updated_at: "2026-06-01T00:00:00Z",
          },
          { status: 201 },
        );
      }),
    );
    const onCreated = vi.fn();
    renderModal(onCreated);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("new-project-title"), "New Thing");
    await user.type(screen.getByTestId("new-project-doc-ref"), "  docs/x.md  ");
    await user.click(screen.getByTestId("new-project-submit"));
    await vi.waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    expect(captured).toEqual({ title: "New Thing", doc_ref: "docs/x.md" });
  });

  it("submits a blank doc_ref as null", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/projects", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(
          {
            id: "no-doc",
            title: "No Doc",
            status: "active",
            doc_ref: null,
            created_at: "2026-06-01T00:00:00Z",
            updated_at: "2026-06-01T00:00:00Z",
          },
          { status: 201 },
        );
      }),
    );
    renderModal();
    const user = userEvent.setup();
    await user.type(screen.getByTestId("new-project-title"), "No Doc");
    await user.click(screen.getByTestId("new-project-submit"));
    await vi.waitFor(() => expect(captured).not.toBeNull());
    expect((captured as { doc_ref: string | null }).doc_ref).toBeNull();
  });

  it("reports non-Error rejections via String(err)", async () => {
    const apiModule = await import("../api");
    const original = apiModule.api.createProject;
    // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
    apiModule.api.createProject = () => Promise.reject("string-create-error");
    try {
      renderModal();
      const user = userEvent.setup();
      await user.type(screen.getByTestId("new-project-title"), "Doomed");
      await user.click(screen.getByTestId("new-project-submit"));
      const alert = await screen.findByTestId("new-project-error");
      expect(alert).toHaveTextContent("string-create-error");
    } finally {
      apiModule.api.createProject = original;
    }
  });

  it("shows an inline destructive-coloured error and keeps the modal open on failure", async () => {
    server.use(
      http.post("/api/projects", () => HttpResponse.json({ detail: "create-boom" }, { status: 500 })),
    );
    renderModal();
    const user = userEvent.setup();
    await user.type(screen.getByTestId("new-project-title"), "Doomed");
    await user.click(screen.getByTestId("new-project-submit"));
    const alert = await screen.findByTestId("new-project-error");
    expect(alert).toHaveTextContent("create-boom");
    expect(alert.className).toContain("text-btn-destroy");
    expect(screen.getByTestId("new-project-dialog")).toBeInTheDocument();
  });

  it("Cancel calls onClose", async () => {
    const onClose = vi.fn();
    renderModal(vi.fn(), onClose);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Escape calls onClose", () => {
    const onClose = vi.fn();
    renderModal(vi.fn(), onClose);
    const form = screen.getByTestId("new-project-dialog").querySelector("form");
    if (form === null) throw new Error("form not found");
    fireEvent.keyDown(form, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Ctrl+Enter submits the form", async () => {
    server.use(
      http.post("/api/projects", () =>
        HttpResponse.json(
          {
            id: "ctrl-enter",
            title: "Ctrl Enter",
            status: "active",
            doc_ref: null,
            created_at: "2026-06-01T00:00:00Z",
            updated_at: "2026-06-01T00:00:00Z",
          },
          { status: 201 },
        ),
      ),
    );
    const onCreated = vi.fn();
    renderModal(onCreated);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("new-project-title"), "Ctrl Enter");
    const form = screen.getByTestId("new-project-dialog").querySelector("form");
    if (form === null) throw new Error("form not found");
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    await vi.waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
  });

  it("blocks Escape while a create request is in flight", async () => {
    const gate: { resolve: (() => void) | null } = { resolve: null };
    server.use(
      http.post(
        "/api/projects",
        () =>
          new Promise((resolve) => {
            gate.resolve = () =>
              resolve(
                HttpResponse.json(
                  {
                    id: "slow",
                    title: "Slow",
                    status: "active",
                    doc_ref: null,
                    created_at: "2026-06-01T00:00:00Z",
                    updated_at: "2026-06-01T00:00:00Z",
                  },
                  { status: 201 },
                ),
              );
          }),
      ),
    );
    const onClose = vi.fn();
    const onCreated = vi.fn();
    renderModal(onCreated, onClose);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("new-project-title"), "Slow");
    await user.click(screen.getByTestId("new-project-submit"));
    expect(screen.getByTestId("new-project-submit")).toHaveTextContent("Creating…");

    const form = screen.getByTestId("new-project-dialog").querySelector("form");
    if (form === null) throw new Error("form not found");
    fireEvent.keyDown(form, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();

    gate.resolve?.();
    await vi.waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
  });
});
