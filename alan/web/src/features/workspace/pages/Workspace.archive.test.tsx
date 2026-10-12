import { screen, waitFor } from "@testing-library/react";
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
  it("archives the workspace when the Archive button is clicked and refetches", async () => {
    const user = userEvent.setup();
    let archived = false;
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json(archived ? { ...BASE, status: "archived" } : BASE),
      ),
      noEventsHandler(),
      http.post("/api/workspaces/t1/archive", () => {
        archived = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderAt();
    await user.click(await screen.findByRole("button", { name: "Archive" }));
    await waitFor(() => {
      expect(screen.getByTestId("status-archived")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("archive-error")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { level: 3, name: "Dispatch" }),
    ).not.toBeInTheDocument();
  });

  it("surfaces an archive error and stays on the page", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
      http.post("/api/workspaces/t1/archive", () =>
        HttpResponse.json(
          { detail: "workspace has active jobs" },
          { status: 409 },
        ),
      ),
    );
    renderAt();
    await user.click(await screen.findByRole("button", { name: "Archive" }));
    const alertEl = await screen.findByTestId("archive-error");
    expect(alertEl).toHaveTextContent("409");
    expect(alertEl).toHaveTextContent("workspace has active jobs");
  });

  it("stringifies non-Error rejections from archiveWorkspace", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
      http.post("/api/workspaces/t1/archive", () => HttpResponse.error()),
    );
    const originalFetch = globalThis.fetch;
    const archiveStub: typeof globalThis.fetch = (input, init) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.href
            : input.url;
      if (url.endsWith("/archive")) {
        // eslint-disable-next-line @typescript-eslint/only-throw-error
        throw "boom";
      }
      return originalFetch(input, init);
    };
    globalThis.fetch = archiveStub;
    try {
      renderAt();
      await user.click(await screen.findByRole("button", { name: "Archive" }));
      const alertEl = await screen.findByTestId("archive-error");
      expect(alertEl).toHaveTextContent("boom");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("shows Unarchive button when workspace is archived", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, status: "archived" }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(
      await screen.findByRole("button", { name: "Unarchive" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archive" })).toBeNull();
  });

  it("shows 'Archiving…' while archive request is in-flight", async () => {
    const user = userEvent.setup();
    let resolveArchive!: () => void;
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
      http.post("/api/workspaces/t1/archive", async () => {
        await new Promise<void>((res) => {
          resolveArchive = res;
        });
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderAt();
    void user.click(await screen.findByRole("button", { name: "Archive" }));
    expect(
      await screen.findByRole("button", { name: "Archiving…" }),
    ).toBeInTheDocument();
    resolveArchive();
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Archiving…" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("shows 'Unarchiving…' while unarchive request is in-flight", async () => {
    const user = userEvent.setup();
    let resolveUnarchive!: () => void;
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, status: "archived" }),
      ),
      noEventsHandler(),
      http.post("/api/workspaces/t1/unarchive", async () => {
        await new Promise<void>((res) => {
          resolveUnarchive = res;
        });
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderAt();
    void user.click(await screen.findByRole("button", { name: "Unarchive" }));
    expect(
      await screen.findByRole("button", { name: "Unarchiving…" }),
    ).toBeInTheDocument();
    resolveUnarchive();
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Unarchiving…" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("unarchives the workspace when the Unarchive button is clicked and refetches", async () => {
    const user = userEvent.setup();
    let unarchived = false;
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json(
          unarchived ? BASE : { ...BASE, status: "archived" },
        ),
      ),
      noEventsHandler(),
      http.post("/api/workspaces/t1/unarchive", () => {
        unarchived = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderAt();
    await user.click(await screen.findByRole("button", { name: "Unarchive" }));
    await waitFor(() => {
      expect(screen.getByTestId("status-active")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("archive-error")).not.toBeInTheDocument();
  });

  it("surfaces an unarchive error and stays on the page", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, status: "archived" }),
      ),
      noEventsHandler(),
      http.post("/api/workspaces/t1/unarchive", () =>
        HttpResponse.json(
          { detail: "workspace is not archived" },
          { status: 409 },
        ),
      ),
    );
    renderAt();
    await user.click(await screen.findByRole("button", { name: "Unarchive" }));
    const alertEl = await screen.findByTestId("archive-error");
    expect(alertEl).toHaveTextContent("409");
    expect(alertEl).toHaveTextContent("workspace is not archived");
  });

  it("stringifies non-Error rejections from unarchiveWorkspace", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, status: "archived" }),
      ),
      noEventsHandler(),
    );
    const originalFetch = globalThis.fetch;
    const unarchiveStub: typeof globalThis.fetch = (input, init) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.href
            : input.url;
      if (url.endsWith("/unarchive")) {
        // eslint-disable-next-line @typescript-eslint/only-throw-error
        throw "unarchive-boom";
      }
      return originalFetch(input, init);
    };
    globalThis.fetch = unarchiveStub;
    try {
      renderAt();
      await user.click(await screen.findByRole("button", { name: "Unarchive" }));
      const alertEl = await screen.findByTestId("archive-error");
      expect(alertEl).toHaveTextContent("unarchive-boom");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
