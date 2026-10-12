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
  it("hides Stop and Resume when workspace is active without a running job", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("status-active");
    expect(screen.queryByTestId("stop-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("resume-button")).not.toBeInTheDocument();
  });

  it("shows Stop when workspace is active with a running job", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, has_running_job: true }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("stop-button")).toBeInTheDocument();
    expect(screen.queryByTestId("resume-button")).not.toBeInTheDocument();
  });

  it("shows Resume when workspace is paused", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, status: "paused" }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("resume-button")).toBeInTheDocument();
    expect(screen.queryByTestId("stop-button")).not.toBeInTheDocument();
  });

  it("stops a running workspace and refetches showing Resume", async () => {
    const user = userEvent.setup();
    let stopped = false;
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json(
          stopped
            ? { ...BASE, status: "paused", has_running_job: false }
            : { ...BASE, has_running_job: true },
        ),
      ),
      noEventsHandler(),
      http.post("/api/workspaces/t1/stop", () => {
        stopped = true;
        return HttpResponse.json({ job_id: 1 }, { status: 202 });
      }),
    );
    renderAt();
    await user.click(await screen.findByTestId("stop-button"));
    await waitFor(() => {
      expect(screen.getByTestId("status-paused")).toBeInTheDocument();
    });
    expect(screen.getByTestId("resume-button")).toBeInTheDocument();
    expect(screen.queryByTestId("stop-resume-error")).not.toBeInTheDocument();
  });

  it("resumes a paused workspace and refetches showing Stop again", async () => {
    const user = userEvent.setup();
    let resumed = false;
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json(
          resumed
            ? { ...BASE, has_running_job: true }
            : { ...BASE, status: "paused" },
        ),
      ),
      noEventsHandler(),
      http.post("/api/workspaces/t1/resume", () => {
        resumed = true;
        return HttpResponse.json({ queued_jobs: 1 }, { status: 202 });
      }),
    );
    renderAt();
    await user.click(await screen.findByTestId("resume-button"));
    await waitFor(() => {
      expect(screen.getByTestId("status-active")).toBeInTheDocument();
    });
    expect(screen.getByTestId("stop-button")).toBeInTheDocument();
  });

  it("surfaces a resume error and stays on the page", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, status: "paused" }),
      ),
      noEventsHandler(),
      http.post("/api/workspaces/t1/resume", () =>
        HttpResponse.json(
          { detail: "workspace is active; cannot resume" },
          { status: 409 },
        ),
      ),
    );
    renderAt();
    await user.click(await screen.findByTestId("resume-button"));
    const alertEl = await screen.findByTestId("stop-resume-error");
    expect(alertEl).toHaveTextContent("409");
  });

  it("surfaces a stop error and stays on the page", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, has_running_job: true }),
      ),
      noEventsHandler(),
      http.post("/api/workspaces/t1/stop", () =>
        HttpResponse.json({ detail: "no running job to stop" }, { status: 404 }),
      ),
    );
    renderAt();
    await user.click(await screen.findByTestId("stop-button"));
    const alertEl = await screen.findByTestId("stop-resume-error");
    expect(alertEl).toHaveTextContent("404");
  });

  it("stringifies non-Error rejections from stopWorkspace", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, has_running_job: true }),
      ),
      noEventsHandler(),
    );
    const originalFetch = globalThis.fetch;
    const stopStub: typeof globalThis.fetch = (input, init) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.href
            : input.url;
      if (url.endsWith("/stop")) {
        // eslint-disable-next-line @typescript-eslint/only-throw-error
        throw "stop-boom";
      }
      return originalFetch(input, init);
    };
    globalThis.fetch = stopStub;
    try {
      renderAt();
      await user.click(await screen.findByTestId("stop-button"));
      const alertEl = await screen.findByTestId("stop-resume-error");
      expect(alertEl).toHaveTextContent("stop-boom");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("shows 'Stopping…' while stop request is in-flight", async () => {
    const user = userEvent.setup();
    let resolveStop: (() => void) | undefined;
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, has_running_job: true }),
      ),
      noEventsHandler(),
      http.post(
        "/api/workspaces/t1/stop",
        () =>
          new Promise<HttpResponse<{ job_id: number }>>((resolve) => {
            resolveStop = () =>
              resolve(HttpResponse.json({ job_id: 1 }, { status: 202 }));
          }),
      ),
    );
    renderAt();
    await user.click(await screen.findByTestId("stop-button"));
    const button = await screen.findByTestId("stop-button");
    expect(button).toHaveTextContent("Stopping…");
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).not.toBeDisabled();
    resolveStop?.();
  });

  it("shows Re-queue button when workspace is awaiting_input with has_resumable_failed_job", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "awaiting_input" as const,
          has_resumable_failed_job: true,
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("requeue-button")).toBeInTheDocument();
    expect(screen.queryByTestId("stop-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("resume-button")).not.toBeInTheDocument();
  });

  it("Re-queue button label is 'Re-queue'", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "awaiting_input" as const,
          has_resumable_failed_job: true,
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("requeue-button")).toHaveTextContent("Re-queue");
  });

  it("does not show Re-queue when workspace is awaiting_input without has_resumable_failed_job", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "awaiting_input" as const,
          has_resumable_failed_job: false,
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("status-awaiting_input");
    expect(screen.queryByTestId("requeue-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("resume-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("stop-button")).not.toBeInTheDocument();
  });

  it("activating Re-queue calls resumeWorkspace and refetches", async () => {
    const user = userEvent.setup();
    let resumed = false;
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json(
          resumed
            ? { ...BASE, has_running_job: true }
            : {
                ...BASE,
                status: "awaiting_input" as const,
                has_resumable_failed_job: true,
              },
        ),
      ),
      noEventsHandler(),
      http.post("/api/workspaces/t1/resume", () => {
        resumed = true;
        return HttpResponse.json({ queued_jobs: 1 }, { status: 202 });
      }),
    );
    renderAt();
    await user.click(await screen.findByTestId("requeue-button"));
    await waitFor(() => {
      expect(screen.getByTestId("status-active")).toBeInTheDocument();
    });
    expect(screen.getByTestId("stop-button")).toBeInTheDocument();
  });

  it("shows retried N× indicator next to Re-queue when requeue_count > 0", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "awaiting_input" as const,
          has_resumable_failed_job: true,
          requeue_count: 2,
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("requeue-count")).toHaveTextContent(
      "retried 2×",
    );
  });

  it("does not show retried indicator when requeue_count is 0", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "awaiting_input" as const,
          has_resumable_failed_job: true,
          requeue_count: 0,
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("requeue-button");
    expect(screen.queryByTestId("requeue-count")).not.toBeInTheDocument();
  });

  it("shows retried indicator next to Resume (paused) when requeue_count > 0", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "paused" as const,
          requeue_count: 1,
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("requeue-count")).toHaveTextContent(
      "retried 1×",
    );
  });
});

describe("Situation card shares the header's stop/resume/requeue lifecycle state", () => {
  it("stops a running workspace from the situation card's own Stop control", async () => {
    const user = userEvent.setup();
    let stopped = false;
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json(
          stopped
            ? { ...BASE, status: "paused", has_running_job: false }
            : { ...BASE, has_running_job: true },
        ),
      ),
      noEventsHandler(),
      http.post("/api/workspaces/t1/stop", () => {
        stopped = true;
        return HttpResponse.json({ job_id: 1 }, { status: 202 });
      }),
    );
    renderAt();
    await user.click(await screen.findByTestId("situation-stop-button"));
    await waitFor(() => {
      expect(screen.getByTestId("status-paused")).toBeInTheDocument();
    });
  });

  it("shows the busy label on both the header and situation-card controls at once", async () => {
    const user = userEvent.setup();
    let resolveStop: (() => void) | undefined;
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, has_running_job: true }),
      ),
      noEventsHandler(),
      http.post(
        "/api/workspaces/t1/stop",
        () =>
          new Promise<HttpResponse<{ job_id: number }>>((resolve) => {
            resolveStop = () =>
              resolve(HttpResponse.json({ job_id: 1 }, { status: 202 }));
          }),
      ),
    );
    renderAt();
    await user.click(await screen.findByTestId("stop-button"));
    expect(await screen.findByTestId("situation-stop-button")).toHaveTextContent("Stopping…");
    expect(screen.getByTestId("stop-button")).toHaveTextContent("Stopping…");
    resolveStop?.();
  });
});
