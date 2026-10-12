import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "../../../test/server";
import ImportRoadmapModal from "./ImportRoadmapModal";

const PREVIEW_URL = "/api/projects/alpha/roadmap/preview";
const IMPORT_URL = "/api/projects/alpha/roadmap/import";

describe("ImportRoadmapModal", () => {
  it("shows the dialog on mount with the markdown editor and disables Preview until non-blank", () => {
    render(<ImportRoadmapModal projectId="alpha" onClose={vi.fn()} onImported={vi.fn()} />);
    expect(screen.getByTestId("import-roadmap-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("import-roadmap-preview-button")).toBeDisabled();
  });

  it("previews parsed items and enables Import once items are found", async () => {
    server.use(
      http.post(PREVIEW_URL, async ({ request }) => {
        const body = (await request.json()) as { markdown: string };
        expect(body.markdown).toBe("## Ship it");
        return HttpResponse.json({
          items: [{ title: "Ship it", text: "Do the thing." }],
        });
      }),
    );
    const user = userEvent.setup();
    render(<ImportRoadmapModal projectId="alpha" onClose={vi.fn()} onImported={vi.fn()} />);
    await user.type(screen.getByTestId("import-roadmap-markdown"), "## Ship it");
    await user.click(screen.getByTestId("import-roadmap-preview-button"));

    const list = await screen.findByTestId("import-roadmap-preview-list");
    expect(list).toHaveTextContent("Ship it");
    expect(list).toHaveTextContent("Do the thing.");
    expect(screen.getByTestId("import-roadmap-submit")).not.toBeDisabled();
  });

  it("shows an empty-preview hint and keeps Import disabled when no ## headings are found", async () => {
    server.use(http.post(PREVIEW_URL, () => HttpResponse.json({ items: [] })));
    const user = userEvent.setup();
    render(<ImportRoadmapModal projectId="alpha" onClose={vi.fn()} onImported={vi.fn()} />);
    await user.type(screen.getByTestId("import-roadmap-markdown"), "no headings here");
    await user.click(screen.getByTestId("import-roadmap-preview-button"));

    expect(await screen.findByTestId("import-roadmap-preview-empty")).toBeInTheDocument();
    expect(screen.getByTestId("import-roadmap-submit")).toBeDisabled();
  });

  it("shows an inline error and stays on the edit step when preview fails", async () => {
    server.use(
      http.post(PREVIEW_URL, () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    const user = userEvent.setup();
    render(<ImportRoadmapModal projectId="alpha" onClose={vi.fn()} onImported={vi.fn()} />);
    await user.type(screen.getByTestId("import-roadmap-markdown"), "## Ship it");
    await user.click(screen.getByTestId("import-roadmap-preview-button"));

    const alert = await screen.findByTestId("import-roadmap-error");
    expect(alert).toHaveTextContent("500");
    expect(screen.getByTestId("import-roadmap-markdown")).toBeInTheDocument();
  });

  it("Back returns to the edit step from preview, keeping the typed markdown", async () => {
    server.use(
      http.post(PREVIEW_URL, () =>
        HttpResponse.json({ items: [{ title: "Ship it", text: "" }] }),
      ),
    );
    const user = userEvent.setup();
    render(<ImportRoadmapModal projectId="alpha" onClose={vi.fn()} onImported={vi.fn()} />);
    await user.type(screen.getByTestId("import-roadmap-markdown"), "## Ship it");
    await user.click(screen.getByTestId("import-roadmap-preview-button"));
    await screen.findByTestId("import-roadmap-preview-list");

    await user.click(screen.getByTestId("import-roadmap-back"));
    expect(screen.getByTestId("import-roadmap-markdown")).toHaveValue("## Ship it");
  });

  it("imports and calls onImported on success", async () => {
    let importedBody: unknown = null;
    server.use(
      http.post(PREVIEW_URL, () =>
        HttpResponse.json({ items: [{ title: "Ship it", text: "" }] }),
      ),
      http.post(IMPORT_URL, async ({ request }) => {
        importedBody = await request.json();
        return HttpResponse.json(
          { items: [{ id: "itm_1", title: "Ship it", text: "" }] },
          { status: 201 },
        );
      }),
    );
    const onImported = vi.fn();
    const user = userEvent.setup();
    render(<ImportRoadmapModal projectId="alpha" onClose={vi.fn()} onImported={onImported} />);
    await user.type(screen.getByTestId("import-roadmap-markdown"), "## Ship it");
    await user.click(screen.getByTestId("import-roadmap-preview-button"));
    await screen.findByTestId("import-roadmap-preview-list");
    await user.click(screen.getByTestId("import-roadmap-submit"));

    await waitFor(() => expect(onImported).toHaveBeenCalledOnce());
    expect(importedBody).toEqual({ markdown: "## Ship it" });
  });

  it("shows an inline error and stays open when import fails (e.g. already imported)", async () => {
    server.use(
      http.post(PREVIEW_URL, () =>
        HttpResponse.json({ items: [{ title: "Ship it", text: "" }] }),
      ),
      http.post(IMPORT_URL, () =>
        HttpResponse.json({ detail: "roadmap already imported" }, { status: 409 }),
      ),
    );
    const onImported = vi.fn();
    const user = userEvent.setup();
    render(<ImportRoadmapModal projectId="alpha" onClose={vi.fn()} onImported={onImported} />);
    await user.type(screen.getByTestId("import-roadmap-markdown"), "## Ship it");
    await user.click(screen.getByTestId("import-roadmap-preview-button"));
    await screen.findByTestId("import-roadmap-preview-list");
    await user.click(screen.getByTestId("import-roadmap-submit"));

    const alert = await screen.findByTestId("import-roadmap-error");
    expect(alert).toHaveTextContent("409");
    expect(screen.getByTestId("import-roadmap-dialog")).toBeInTheDocument();
    expect(onImported).not.toHaveBeenCalled();
  });

  it("Cancel button closes the modal", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<ImportRoadmapModal projectId="alpha" onClose={onClose} onImported={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Escape closes the modal", () => {
    const onClose = vi.fn();
    render(<ImportRoadmapModal projectId="alpha" onClose={onClose} onImported={vi.fn()} />);
    const form = screen.getByTestId("import-roadmap-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Ctrl+Enter on the edit step runs Preview", async () => {
    server.use(
      http.post(PREVIEW_URL, () =>
        HttpResponse.json({ items: [{ title: "Ship it", text: "" }] }),
      ),
    );
    const user = userEvent.setup();
    render(<ImportRoadmapModal projectId="alpha" onClose={vi.fn()} onImported={vi.fn()} />);
    await user.type(screen.getByTestId("import-roadmap-markdown"), "## Ship it");
    const form = screen.getByTestId("import-roadmap-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    expect(await screen.findByTestId("import-roadmap-preview-list")).toBeInTheDocument();
  });

  it("ignores Ctrl+Enter on the edit step while the markdown is blank", () => {
    render(<ImportRoadmapModal projectId="alpha" onClose={vi.fn()} onImported={vi.fn()} />);
    const form = screen.getByTestId("import-roadmap-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    expect(screen.getByTestId("import-roadmap-markdown")).toBeInTheDocument();
  });

  it("ignores Ctrl+Enter on the preview step when no items were found", async () => {
    server.use(http.post(PREVIEW_URL, () => HttpResponse.json({ items: [] })));
    const user = userEvent.setup();
    render(<ImportRoadmapModal projectId="alpha" onClose={vi.fn()} onImported={vi.fn()} />);
    await user.type(screen.getByTestId("import-roadmap-markdown"), "no headings here");
    await user.click(screen.getByTestId("import-roadmap-preview-button"));
    await screen.findByTestId("import-roadmap-preview-empty");

    const form = screen.getByTestId("import-roadmap-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    expect(screen.getByTestId("import-roadmap-preview-empty")).toBeInTheDocument();
  });

  it("ignores a plain form submit event", async () => {
    const user = userEvent.setup();
    render(<ImportRoadmapModal projectId="alpha" onClose={vi.fn()} onImported={vi.fn()} />);
    await user.type(screen.getByTestId("import-roadmap-markdown"), "## Ship it");
    const form = screen.getByTestId("import-roadmap-dialog").querySelector("form")!;
    fireEvent.submit(form);
    expect(screen.getByTestId("import-roadmap-dialog")).toBeInTheDocument();
  });

  it("blocks Escape and Ctrl+Enter while a preview request is in flight", async () => {
    server.use(
      http.post(PREVIEW_URL, async () => {
        await new Promise(() => undefined);
        return HttpResponse.json({ items: [] });
      }),
    );
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<ImportRoadmapModal projectId="alpha" onClose={onClose} onImported={vi.fn()} />);
    await user.type(screen.getByTestId("import-roadmap-markdown"), "## Ship it");
    await user.click(screen.getByTestId("import-roadmap-preview-button"));
    expect(screen.getByTestId("import-roadmap-preview-button")).toHaveTextContent(
      "Loading preview…",
    );

    const form = screen.getByTestId("import-roadmap-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Escape" });
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("shows Importing… and blocks dismissal while an import request is in flight", async () => {
    server.use(
      http.post(PREVIEW_URL, () =>
        HttpResponse.json({ items: [{ title: "Ship it", text: "" }] }),
      ),
      http.post(IMPORT_URL, async () => {
        await new Promise(() => undefined);
        return HttpResponse.json({ items: [] });
      }),
    );
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<ImportRoadmapModal projectId="alpha" onClose={onClose} onImported={vi.fn()} />);
    await user.type(screen.getByTestId("import-roadmap-markdown"), "## Ship it");
    await user.click(screen.getByTestId("import-roadmap-preview-button"));
    await screen.findByTestId("import-roadmap-preview-list");
    await user.click(screen.getByTestId("import-roadmap-submit"));

    expect(screen.getByTestId("import-roadmap-submit")).toHaveTextContent("Importing…");
    const form = screen.getByTestId("import-roadmap-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("blocks the dialog's native cancel event while a preview request is in flight", async () => {
    server.use(
      http.post(PREVIEW_URL, async () => {
        await new Promise(() => undefined);
        return HttpResponse.json({ items: [] });
      }),
    );
    const user = userEvent.setup();
    render(<ImportRoadmapModal projectId="alpha" onClose={vi.fn()} onImported={vi.fn()} />);
    await user.type(screen.getByTestId("import-roadmap-markdown"), "## Ship it");
    await user.click(screen.getByTestId("import-roadmap-preview-button"));
    const dialog = screen.getByTestId("import-roadmap-dialog");
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(screen.getByTestId("import-roadmap-dialog")).toBeInTheDocument();
  });

  it("shows the dialog on mount and closes it on unmount", () => {
    const showModal = vi.spyOn(HTMLDialogElement.prototype, "showModal");
    const close = vi.spyOn(HTMLDialogElement.prototype, "close");
    const { unmount } = render(
      <ImportRoadmapModal projectId="alpha" onClose={vi.fn()} onImported={vi.fn()} />,
    );
    expect(showModal).toHaveBeenCalled();
    unmount();
    expect(close).toHaveBeenCalled();
  });
});
