import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import WorkspaceNoteEditor from "./WorkspaceNoteEditor";

function renderEditor(taskId = "ws-1", initialNote: string | null | undefined = null) {
  return render(<WorkspaceNoteEditor taskId={taskId} initialNote={initialNote} />);
}

describe("WorkspaceNoteEditor", () => {
  it("renders the section with heading", () => {
    renderEditor();
    expect(screen.getByTestId("note-editor")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 3, name: "Personal note" }),
    ).toBeInTheDocument();
  });

  it("pre-fills textarea with initialNote", () => {
    renderEditor("ws-1", "existing memo");
    expect(screen.getByRole("textbox", { name: "Personal note" })).toHaveValue("existing memo");
  });

  it("pre-fills with empty string when initialNote is null", () => {
    renderEditor("ws-1", null);
    expect(screen.getByRole("textbox", { name: "Personal note" })).toHaveValue("");
  });

  it("shows Saved confirmation after successful save", async () => {
    const user = userEvent.setup();
    server.use(
      http.put("/api/workspaces/ws-1/note", () =>
        HttpResponse.json({ note: "hello" }),
      ),
    );
    renderEditor("ws-1", null);
    await user.type(screen.getByRole("textbox", { name: "Personal note" }), "hello");
    await user.click(screen.getByTestId("note-save-btn"));
    expect(await screen.findByTestId("note-saved")).toBeInTheDocument();
    expect(screen.queryByTestId("note-error")).not.toBeInTheDocument();
  });

  it("updates textarea value to server response after save", async () => {
    const user = userEvent.setup();
    server.use(
      http.put("/api/workspaces/ws-1/note", () =>
        HttpResponse.json({ note: "trimmed" }),
      ),
    );
    renderEditor("ws-1", null);
    await user.type(screen.getByRole("textbox", { name: "Personal note" }), "  trimmed  ");
    await user.click(screen.getByTestId("note-save-btn"));
    await screen.findByTestId("note-saved");
    expect(screen.getByRole("textbox", { name: "Personal note" })).toHaveValue("trimmed");
  });

  it("clears to empty and shows Saved when note is null response", async () => {
    const user = userEvent.setup();
    server.use(
      http.put("/api/workspaces/ws-1/note", () =>
        HttpResponse.json({ note: null }),
      ),
    );
    renderEditor("ws-1", "old");
    const textarea = screen.getByRole("textbox", { name: "Personal note" });
    await user.clear(textarea);
    await user.click(screen.getByTestId("note-save-btn"));
    await screen.findByTestId("note-saved");
    expect(textarea).toHaveValue("");
  });

  it("shows error alert on save failure", async () => {
    const user = userEvent.setup();
    server.use(
      http.put("/api/workspaces/ws-1/note", () =>
        HttpResponse.json({ detail: "server down" }, { status: 500 }),
      ),
    );
    renderEditor("ws-1", null);
    await user.click(screen.getByTestId("note-save-btn"));
    const alert = await screen.findByTestId("note-error");
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert).toHaveTextContent("500");
    expect(screen.queryByTestId("note-saved")).not.toBeInTheDocument();
  });

  it("shows Saving… while request is in-flight and re-enables after", async () => {
    const user = userEvent.setup();
    let resolve!: () => void;
    server.use(
      http.put("/api/workspaces/ws-1/note", () =>
        new Promise<Response>((r) => {
          resolve = () => r(HttpResponse.json({ note: "hi" }));
        }),
      ),
    );
    renderEditor("ws-1", null);
    void user.click(screen.getByTestId("note-save-btn"));
    const button = await screen.findByRole("button", { name: "Saving…" });
    expect(button).toHaveAttribute("aria-busy", "true");
    button.focus();
    expect(button).toHaveFocus();
    resolve();
    await waitFor(() => {
      const idle = screen.getByRole("button", { name: "Save note" });
      expect(idle).not.toHaveAttribute("aria-busy");
    });
  });

  it("stringifies non-Error rejections from updateWorkspaceNote into the error alert", async () => {
    const user = userEvent.setup();
    const apiModule = await import("../../../api");
    const original = apiModule.api.updateWorkspaceNote;
    apiModule.api.updateWorkspaceNote = () =>
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      Promise.reject("plain-note-error");
    try {
      renderEditor("ws-1", null);
      await user.click(screen.getByTestId("note-save-btn"));
      const alert = await screen.findByTestId("note-error");
      expect(alert).toHaveTextContent("plain-note-error");
    } finally {
      apiModule.api.updateWorkspaceNote = original;
    }
  });

  it("re-syncs textarea when key changes (workspace navigation)", () => {
    const { rerender } = render(
      <WorkspaceNoteEditor key="ws-1" taskId="ws-1" initialNote="first" />,
    );
    rerender(<WorkspaceNoteEditor key="ws-2" taskId="ws-2" initialNote="second" />);
    expect(screen.getByRole("textbox", { name: "Personal note" })).toHaveValue("second");
  });

  it("clears Saved indicator when key changes (workspace navigation)", async () => {
    const user = userEvent.setup();
    server.use(
      http.put("/api/workspaces/ws-1/note", () => HttpResponse.json({ note: "hi" })),
    );
    const { rerender } = render(
      <WorkspaceNoteEditor key="ws-1" taskId="ws-1" initialNote={null} />,
    );
    await user.type(screen.getByRole("textbox", { name: "Personal note" }), "hi");
    await user.click(screen.getByTestId("note-save-btn"));
    await screen.findByTestId("note-saved");
    rerender(<WorkspaceNoteEditor key="ws-2" taskId="ws-2" initialNote={null} />);
    expect(screen.queryByTestId("note-saved")).not.toBeInTheDocument();
  });

  it("ignores stale success response after taskId prop changes", async () => {
    let resolveStale!: () => void;
    server.use(
      http.put("/api/workspaces/ws-1/note", () =>
        new Promise<Response>((r) => {
          resolveStale = () => r(HttpResponse.json({ note: "stale-note" }));
        }),
      ),
    );
    const user = userEvent.setup();
    const { rerender } = renderEditor("ws-1", null);
    void user.click(screen.getByTestId("note-save-btn"));
    await screen.findByRole("button", { name: "Saving…" });
    rerender(<WorkspaceNoteEditor taskId="ws-2" initialNote={null} />);
    await act(async () => {
      resolveStale();
      await new Promise<void>((r) => setTimeout(r, 0));
    });
    expect(screen.queryByTestId("note-saved")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Personal note" })).toHaveValue("");
  });

  it("ignores stale error response after taskId prop changes", async () => {
    let resolveStaleError!: () => void;
    server.use(
      http.put("/api/workspaces/ws-1/note", () =>
        new Promise<Response>((r) => {
          resolveStaleError = () =>
            r(HttpResponse.json({ detail: "gone" }, { status: 500 }));
        }),
      ),
    );
    const user = userEvent.setup();
    const { rerender } = renderEditor("ws-1", null);
    void user.click(screen.getByTestId("note-save-btn"));
    await screen.findByRole("button", { name: "Saving…" });
    rerender(<WorkspaceNoteEditor taskId="ws-2" initialNote={null} />);
    await act(async () => {
      resolveStaleError();
      await new Promise<void>((r) => setTimeout(r, 0));
    });
    expect(screen.queryByTestId("note-error")).not.toBeInTheDocument();
  });

  it("hides Saved confirmation after user edits the textarea again", async () => {
    const user = userEvent.setup();
    server.use(
      http.put("/api/workspaces/ws-1/note", () =>
        HttpResponse.json({ note: "abc" }),
      ),
    );
    renderEditor("ws-1", null);
    await user.type(screen.getByRole("textbox", { name: "Personal note" }), "abc");
    await user.click(screen.getByTestId("note-save-btn"));
    await screen.findByTestId("note-saved");
    await user.type(screen.getByRole("textbox", { name: "Personal note" }), "x");
    expect(screen.queryByTestId("note-saved")).not.toBeInTheDocument();
  });
});

describe("WorkspaceNoteEditor — unsaved cue", () => {
  it("shows no unsaved cue on mount", () => {
    renderEditor("ws-1", "existing memo");
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
  });

  it("shows the unsaved cue after an edit", async () => {
    const user = userEvent.setup();
    renderEditor("ws-1", "existing memo");
    await user.type(screen.getByRole("textbox", { name: "Personal note" }), "!");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
  });

  it("clears the unsaved cue after a successful save", async () => {
    const user = userEvent.setup();
    server.use(
      http.put("/api/workspaces/ws-1/note", () =>
        HttpResponse.json({ note: "existing memo!" }),
      ),
    );
    renderEditor("ws-1", "existing memo");
    await user.type(screen.getByRole("textbox", { name: "Personal note" }), "!");
    await user.click(screen.getByTestId("note-save-btn"));
    await screen.findByTestId("note-saved");
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
  });

  it("is absent once the text is edited back to the saved value", async () => {
    const user = userEvent.setup();
    renderEditor("ws-1", "existing memo");
    const textarea = screen.getByRole("textbox", { name: "Personal note" });
    await user.type(textarea, "!");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    await user.type(textarea, "{Backspace}");
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
  });
});

describe("WorkspaceNoteEditor — Saved status region", () => {
  it("mounts the status region on arrival, before any save", () => {
    renderEditor("ws-1", null);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByTestId("note-saved")).not.toBeInTheDocument();
  });

  it("keeps the same status region mounted and fills it with Saved after a successful save", async () => {
    const user = userEvent.setup();
    server.use(
      http.put("/api/workspaces/ws-1/note", () => HttpResponse.json({ note: "hi" })),
    );
    renderEditor("ws-1", null);
    const status = screen.getByRole("status");
    await user.type(screen.getByRole("textbox", { name: "Personal note" }), "hi");
    await user.click(screen.getByTestId("note-save-btn"));
    await screen.findByTestId("note-saved");
    expect(screen.getByRole("status")).toBe(status);
    expect(status).toHaveTextContent("Saved");
  });
});

describe("WorkspaceNoteEditor — field chrome", () => {
  it("gives the textarea the shared focus-ring class", () => {
    renderEditor();
    expect(screen.getByRole("textbox", { name: "Personal note" }).className).toContain(
      "focus-visible:ring-2",
    );
  });
});
