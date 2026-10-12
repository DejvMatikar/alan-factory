import { screen, waitFor, within } from "@testing-library/react";
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
  it("shows empty-state when there are no invocations", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("invocation-graph-empty")).toBeInTheDocument();
  });

  it("renders one invocation node per inv_id, oldest first", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () =>
        HttpResponse.json(["inv_001", "inv_002"]),
      ),
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_001",
          namespace: "alan.workspace.t1",
          team: "research",
          status: "success",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: "2026-05-12T08:00:30Z",
          tasks: [],
        }),
      ),
      http.get("/api/workspaces/t1/invocations/inv_002", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_002",
          namespace: "alan.workspace.t1",
          team: "research",
          status: "running",
          started_at: "2026-05-12T08:01:00Z",
          completed_at: null,
          tasks: [],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("run-row-inv_001");
    await screen.findByTestId("run-row-inv_002");
    const list = screen.getByTestId("runs-list");
    const rows = list.querySelectorAll("[data-testid^='run-row-inv_']");
    expect(rows).toHaveLength(2);
    expect(rows[0]!.getAttribute("data-testid")).toBe("run-row-inv_001");
    expect(rows[1]!.getAttribute("data-testid")).toBe("run-row-inv_002");
  });

  it("shows an instruction badge on the invocation node when the workspace's dispatch-instructions readback carries one", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json(["inv_001"])),
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_001",
          namespace: "alan.workspace.t1",
          team: "research",
          status: "success",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: "2026-05-12T08:00:30Z",
          tasks: [],
        }),
      ),
      http.get("/api/workspaces/t1/dispatch-instructions", () =>
        HttpResponse.json({ inv_001: { status: "attached", text: "build the widget" } }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const row = await screen.findByTestId("run-row-inv_001");
    expect(
      await within(row).findByTestId("instruction-badge-inv_001"),
    ).toHaveTextContent("instructions attached");
  });

  it("renders no instruction badge when the dispatch-instructions fetch fails", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json(["inv_001"])),
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({
          schema_version: "1.0",
          id: "inv_001",
          namespace: "alan.workspace.t1",
          team: "research",
          status: "success",
          started_at: "2026-05-12T08:00:00Z",
          completed_at: "2026-05-12T08:00:30Z",
          tasks: [],
        }),
      ),
      http.get("/api/workspaces/t1/dispatch-instructions", () => HttpResponse.error()),
      noEventsHandler(),
    );
    renderAt();
    const row = await screen.findByTestId("run-row-inv_001");
    expect(within(row).queryByTestId("instruction-badge-inv_001")).not.toBeInTheDocument();
  });

  it("renders the sticky instruction card when the workspace has a persisted instruction", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: "dispatch" }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("sticky-instruction-card")).toHaveTextContent(
      "focus here",
    );
  });

  it("refetches the sticky instruction card once the workspace's updated_at advances", async () => {
    const user = userEvent.setup();
    let archived = false;
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json(
          archived
            ? { ...BASE, status: "archived", updated_at: "2026-05-12T09:00:00Z" }
            : BASE,
        ),
      ),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json(
          archived
            ? { text: "after archive", origin: "dispatch" }
            : { text: "before archive", origin: "dispatch" },
        ),
      ),
      http.post("/api/workspaces/t1/archive", () => {
        archived = true;
        return new HttpResponse(null, { status: 204 });
      }),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("sticky-instruction-card")).toHaveTextContent(
      "before archive",
    );
    await user.click(await screen.findByRole("button", { name: "Archive" }));
    await waitFor(() =>
      expect(screen.getByTestId("sticky-instruction-card")).toHaveTextContent(
        "after archive",
      ),
    );
  });

  it("removes the sticky instruction card and moves focus to the cleared announcement after Clear", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: "dispatch" }),
      ),
      http.delete(
        "/api/workspaces/t1/sticky-instructions",
        () => new HttpResponse(null, { status: 204 }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("sticky-instruction-card");
    await user.click(screen.getByTestId("sticky-instruction-clear"));
    const notice = await screen.findByTestId("sticky-instruction-cleared-notice");
    expect(screen.queryByTestId("sticky-instruction-card")).not.toBeInTheDocument();
    await waitFor(() => expect(notice).toHaveFocus());
  });

  it("shows the invocation-graph-empty state when listInvocations returns []", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByTestId("invocation-graph-empty")).toBeInTheDocument();
  });

  it("surfaces a /invocations error through the page-level load alert", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () =>
        HttpResponse.json({ detail: "bad" }, { status: 500 }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent(/bad/);
  });

  it("ignores listInvocations resolution after unmount", async () => {
    let resolve: ((value: string[]) => void) | null = null;
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () =>
        new Promise<Response>((r) => {
          resolve = (list) => r(HttpResponse.json(list));
        }),
      ),
      noEventsHandler(),
    );
    const { unmount } = renderAt();
    await waitFor(() => expect(resolve).not.toBeNull());
    unmount();
    resolve!(["inv_001"]);
    await new Promise((r) => setTimeout(r, 10));
  });

  it("ignores listInvocations rejection after unmount", async () => {
    let reject: ((reason: unknown) => void) | null = null;
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () =>
        new Promise<Response>((_res, rej) => {
          reject = rej;
        }),
      ),
      noEventsHandler(),
    );
    const { unmount } = renderAt();
    await waitFor(() => expect(reject).not.toBeNull());
    unmount();
    reject!(new Error("late"));
    await new Promise((r) => setTimeout(r, 10));
  });

  it("stringifies non-Error rejections from listInvocations into the load alert", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    const apiModule = await import("../../../api");
    const original = apiModule.api.listInvocations;
    apiModule.api.listInvocations = () =>
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      Promise.reject("plain-error");
    try {
      renderAt();
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "plain-error",
      );
    } finally {
      apiModule.api.listInvocations = original;
    }
  });
});
