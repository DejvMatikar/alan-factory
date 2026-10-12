import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  function ReactFlow({
    nodes,
    nodeTypes,
  }: {
    nodes: { id: string; type: string; data: Record<string, unknown> }[];
    nodeTypes: Record<
      string,
      React.ComponentType<{ data: Record<string, unknown> }>
    >;
  }) {
    return React.createElement(
      "div",
      { "data-testid": "rf-mock" },
      nodes.map((n) => {
        const Component = nodeTypes[n.type]!;
        return React.createElement(Component, { key: n.id, data: n.data });
      }),
    );
  }
  function Stub() {
    return null;
  }
  return { ReactFlow, Background: Stub, Controls: Stub };
});

import { server } from "../../../test/server";
import { BASE, installWorkspaceDefaultHandlers, noEventsHandler, renderAt } from "../../../test/workspacePageHarness";

installWorkspaceDefaultHandlers();

describe("Workspace detail", () => {
  it("opens the delete dialog with the CC-sessions caveat when Delete is clicked", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    const deleteBtn = await screen.findByRole("button", { name: "Delete" });
    await userEvent.click(deleteBtn);
    const dialog = await screen.findByTestId("delete-dialog");
    expect(dialog).toHaveAttribute("open");
    expect(dialog).toHaveTextContent(/Claude Code sessions/);
    expect(dialog).toHaveTextContent(/are not removed/);
  });

  it("closes the delete dialog when Cancel is clicked without calling the API", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    const apiModule = await import("../../../api");
    const original = apiModule.api.deleteWorkspace;
    let called = 0;
    apiModule.api.deleteWorkspace = () => {
      called += 1;
      return Promise.resolve();
    };
    try {
      renderAt();
      await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
      await screen.findByTestId("delete-dialog");
      await userEvent.click(
        screen.getByRole("button", { name: "Cancel" }),
      );
      await waitFor(() => {
        expect(screen.queryByTestId("delete-dialog")).not.toBeInTheDocument();
      });
      expect(called).toBe(0);
    } finally {
      apiModule.api.deleteWorkspace = original;
    }
  });

  it("calls deleteWorkspace and navigates to / on Confirm", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      http.delete("/api/workspaces/t1", () => new HttpResponse(null, { status: 204 })),
      noEventsHandler(),
    );
    renderAt();
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await userEvent.click(
      await screen.findByRole("button", { name: /^Delete workspace/ }),
    );
    expect(await screen.findByTestId("list-page")).toBeInTheDocument();
  });

  it("shows the 409 detail inline inside the dialog and keeps it open", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      http.delete("/api/workspaces/t1", () =>
        HttpResponse.json({ detail: "workspace has active jobs" }, { status: 409 }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await userEvent.click(
      await screen.findByRole("button", { name: /^Delete workspace/ }),
    );
    const dialog = await screen.findByTestId("delete-dialog");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/409: workspace has active jobs/);
    expect(dialog).toHaveAttribute("open");
    expect(screen.queryByTestId("list-page")).not.toBeInTheDocument();
  });

  it("stringifies non-Error rejections from deleteWorkspace into the dialog alert", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    const apiModule = await import("../../../api");
    const original = apiModule.api.deleteWorkspace;
    apiModule.api.deleteWorkspace = () =>
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      Promise.reject("plain-error");
    try {
      renderAt();
      await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
      await userEvent.click(
        await screen.findByRole("button", { name: /^Delete workspace/ }),
      );
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("plain-error");
    } finally {
      apiModule.api.deleteWorkspace = original;
    }
  });

  it("dismisses the delete dialog when ESC fires the native cancel event", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    renderAt();
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    const dialog = await screen.findByTestId("delete-dialog");
    fireEvent(dialog, new Event("cancel", { bubbles: true, cancelable: true }));
    await waitFor(() => {
      expect(screen.queryByTestId("delete-dialog")).not.toBeInTheDocument();
    });
  });

  it("ignores the native cancel event while a delete request is in flight", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
    );
    let resolveDelete: (() => void) | undefined;
    const apiModule = await import("../../../api");
    const original = apiModule.api.deleteWorkspace;
    apiModule.api.deleteWorkspace = () =>
      new Promise<void>((res) => {
        resolveDelete = res;
      });
    try {
      renderAt();
      await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
      await userEvent.click(
        await screen.findByRole("button", { name: /^Delete workspace/ }),
      );
      const dialog = await screen.findByTestId("delete-dialog");
      await screen.findByRole("button", { name: "Deleting…" });
      const dispatched = fireEvent(
        dialog,
        new Event("cancel", { bubbles: true, cancelable: true }),
      );
      expect(dispatched).toBe(false);
      expect(screen.getByTestId("delete-dialog")).toBeInTheDocument();
      resolveDelete?.();
    } finally {
      apiModule.api.deleteWorkspace = original;
    }
  });
});
