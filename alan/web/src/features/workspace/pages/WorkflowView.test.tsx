import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, delay, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StreamEvent, WorkflowDoc, WorkflowTask } from "../../../api";
import { server } from "../../../test/server";
import WorkflowView from "./WorkflowView";

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  function ReactFlow({
    nodes,
    nodeTypes,
  }: {
    nodes: {
      id: string;
      type: string;
      data: Record<string, unknown>;
    }[];
    nodeTypes: Record<string, React.ComponentType<{ data: Record<string, unknown> }>>;
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

function makeTask(
  id: string,
  overrides: Partial<WorkflowTask> = {},
): WorkflowTask {
  const base: WorkflowTask = {
    id,
    type: "io.alan.LLMStep",
    role: id,
    model: "sonnet",
    depends_on: [],
    status: "success",
    started_at: "2026-05-12T08:00:00Z",
    completed_at: "2026-05-12T08:00:05Z",
    stream_path: `agents/${id}.stream.jsonl`,
    dispatched: [],
    error: null,
    input_tokens: null,
    output_tokens: null,
    cache_creation_input_tokens: null,
    cache_read_input_tokens: null,
    total_cost_usd: null,
    usage_raw: null,
  };
  return { ...base, ...overrides };
}

const BASE_DOC: WorkflowDoc = {
  schema_version: "1.0",
  id: "inv_001",
  namespace: "alan.workspace.t1",
  team: "research",
  status: "success",
  started_at: "2026-05-12T08:00:00Z",
  completed_at: "2026-05-12T08:00:30Z",
  tasks: [
    makeTask("orchestrator", { model: "opus", dispatched: ["alpha"] }),
    makeTask("alpha", { depends_on: ["orchestrator"] }),
    makeTask("synthesizer", {
      model: "opus",
      depends_on: ["alpha"],
    }),
  ],
};

const STREAM_EVENTS: StreamEvent[] = [
  { type: "system", subtype: "init" },
  { type: "result", result: "ok" },
];

const RUNNING_DOC: WorkflowDoc = {
  ...BASE_DOC,
  tasks: BASE_DOC.tasks!.map((t) => ({ ...t, status: "running" })),
};

function renderAt(path = "/workspaces/t1/invocations/inv_001") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/workspaces/:taskId/invocations/:invId"
          element={<WorkflowView />}
        />
        <Route
          path="/workspaces/:taskId"
          element={<div data-testid="back-page">back</div>}
        />
        <Route
          path="*"
          element={<div data-testid="match-all">no-match</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

async function selectStep(id: string) {
  const card = await screen.findByTestId(`workflow-node-${id}`);
  await userEvent.click(within(card).getByRole("button", { name: /^Show activity of / }));
}

function noEvents() {
  return http.get("/api/workspaces/t1/events", () => HttpResponse.error());
}

function happyInvocation(doc: WorkflowDoc = BASE_DOC) {
  return http.get("/api/workspaces/t1/invocations/inv_001", () =>
    HttpResponse.json(doc),
  );
}

function happyStream(role = "alpha", events: StreamEvent[] = STREAM_EVENTS) {
  return http.get(
    `/api/workspaces/t1/invocations/inv_001/agents/${role}/stream`,
    () => HttpResponse.json(events),
  );
}

afterEach(() => {
  vi.useRealTimers();
});

beforeEach(() => {
  server.use(
    http.get("/api/workspaces/t1/dispatch-instructions", () => HttpResponse.json({})),
  );
});

describe("WorkflowView", () => {
  it("renders the page heading and the invocation card with team + step count", async () => {
    server.use(happyInvocation(), noEvents());
    renderAt();
    expect(await screen.findByTestId("workflow-page-title")).toHaveTextContent(
      /t1 · inv_001/,
    );
    expect(
      await screen.findByTestId("invocation-card-inv_001"),
    ).toBeInTheDocument();
    expect(screen.getByText("Research")).toBeInTheDocument();
    expect(screen.getByText(/3 steps/)).toBeInTheDocument();
  });

  it("shows an instruction badge in the card header when the readback for this invocation is attached", async () => {
    server.use(
      happyInvocation(),
      noEvents(),
      http.get("/api/workspaces/t1/dispatch-instructions", () =>
        HttpResponse.json({ inv_001: { status: "attached", text: "build the widget" } }),
      ),
    );
    renderAt();
    expect(
      await screen.findByTestId("instruction-badge-inv_001"),
    ).toHaveTextContent("instructions attached");
  });

  it("omits the instruction badge when the dispatch-instructions fetch fails", async () => {
    server.use(
      happyInvocation(),
      noEvents(),
      http.get("/api/workspaces/t1/dispatch-instructions", () => HttpResponse.error()),
    );
    renderAt();
    await screen.findByTestId("invocation-card-inv_001");
    expect(screen.queryByTestId("instruction-badge-inv_001")).not.toBeInTheDocument();
  });

  it("renders each task node with status + model", async () => {
    server.use(happyInvocation(), noEvents());
    renderAt();
    await screen.findByTestId("workflow-node-orchestrator");
    expect(screen.getByTestId("workflow-node-alpha")).toBeInTheDocument();
    expect(
      screen.getByTestId("workflow-node-synthesizer"),
    ).toBeInTheDocument();
    expect(
      screen.getAllByTestId("node-status-success").length,
    ).toBeGreaterThanOrEqual(3);
  });

  it("shows '1 step' label in singular form", async () => {
    server.use(
      happyInvocation({ ...BASE_DOC, tasks: [BASE_DOC.tasks![0]!] }),
      noEvents(),
    );
    renderAt();
    expect(await screen.findByText(/1 step/)).toBeInTheDocument();
  });

  it("clicking a node opens the stream panel and fetches events", async () => {
    server.use(happyInvocation(RUNNING_DOC), happyStream("alpha"), noEvents());
    renderAt();
    await selectStep("alpha");
    expect(await screen.findByTestId("stream-panel")).toBeInTheDocument();
    expect(
      await screen.findByTestId("stream-row-system"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("stream-row-result")).toBeInTheDocument();
  });

  it("renders error inside the invocation card when the workflow fetch fails", async () => {
    server.use(
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
      noEvents(),
    );
    renderAt();
    expect(
      await screen.findByTestId("invocation-error-inv_001"),
    ).toHaveTextContent(/boom/);
  });

  it("ignores getInvocation resolution after unmount", async () => {
    let resolve: ((value: WorkflowDoc) => void) | null = null;
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001",
        () =>
          new Promise<Response>((r) => {
            resolve = (doc) => r(HttpResponse.json(doc));
          }),
      ),
      noEvents(),
    );
    const { unmount } = renderAt();
    await screen.findByTestId("invocation-card-inv_001");
    unmount();
    resolve!(BASE_DOC);
    await delay(20);
  });

  it("ignores getInvocation rejection after unmount", async () => {
    let reject: ((reason: unknown) => void) | null = null;
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001",
        () =>
          new Promise<Response>((_res, rej) => {
            reject = rej;
          }),
      ),
      noEvents(),
    );
    const { unmount } = renderAt();
    await screen.findByTestId("invocation-card-inv_001");
    unmount();
    reject!(new Error("late"));
    await delay(20);
  });

  it("stringifies non-Error rejections from the workflow fetch", async () => {
    const apiModule = await import("../../../api");
    const original = apiModule.api.getInvocation;
    apiModule.api.getInvocation = () =>
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      Promise.reject("plain-string");
    try {
      renderAt();
      expect(
        await screen.findByTestId("invocation-error-inv_001"),
      ).toHaveTextContent("plain-string");
    } finally {
      apiModule.api.getInvocation = original;
    }
  });

  it("stringifies non-Error stream rejections", async () => {
    server.use(happyInvocation(), noEvents());
    const apiModule = await import("../../../api");
    const original = apiModule.api.getAgentStream;
    apiModule.api.getAgentStream = () =>
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      Promise.reject("stream-string");
    try {
      renderAt();
      await selectStep("alpha");
      expect(await screen.findByTestId("stream-error")).toHaveTextContent(
        "stream-string",
      );
    } finally {
      apiModule.api.getAgentStream = original;
    }
  });

  it("renders stream error when fetching events fails", async () => {
    server.use(
      happyInvocation(),
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/alpha/stream",
        () => HttpResponse.json({ detail: "no stream" }, { status: 404 }),
      ),
      noEvents(),
    );
    renderAt();
    await selectStep("alpha");
    expect(await screen.findByTestId("stream-error")).toHaveTextContent(
      /no stream/,
    );
  });

  it("renders empty-stream notice when no events on disk", async () => {
    server.use(happyInvocation(RUNNING_DOC), happyStream("alpha", []), noEvents());
    renderAt();
    await selectStep("alpha");
    expect(await screen.findByTestId("stream-empty")).toBeInTheDocument();
  });

  it("shows a missing-ids message when the route lacks both segments", () => {
    render(
      <MemoryRouter initialEntries={["/workspaces//invocations/"]}>
        <Routes>
          <Route
            path="/workspaces/:taskId/invocations/:invId"
            element={<WorkflowView />}
          />
          <Route path="*" element={<WorkflowView />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("missing-ids")).toBeInTheDocument();
  });

  it("merges live node_started/node_completed events", async () => {
    let send: ((chunk: string) => void) | null = null;
    let close: (() => void) | null = null;
    server.use(
      happyInvocation({
        ...BASE_DOC,
        tasks: [makeTask("orchestrator", { status: "running" })],
      }),
      http.get("/api/workspaces/t1/events", () => {
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            const enc = new TextEncoder();
            send = (chunk) => controller.enqueue(enc.encode(chunk));
            close = () => controller.close();
          },
        });
        return new HttpResponse(stream, {
          headers: { "Content-Type": "text/event-stream" },
        });
      }),
    );
    renderAt();
    const node = await screen.findByTestId("workflow-node-orchestrator");
    expect(node.dataset.status).toBe("running");
    const update = makeTask("orchestrator", { status: "success" });
    const payload = JSON.stringify({
      inv_id: "inv_001",
      role: "orchestrator",
      node: JSON.stringify(update),
    });
    send!(`event: node_completed\ndata: ${payload}\n\n`);
    await waitFor(() => {
      expect(
        screen.getByTestId("workflow-node-orchestrator").dataset.status,
      ).toBe("success");
    });
    close!();
  });

  it("does not duplicate live events that arrived before the loaded fetch", async () => {
    let sendStream: ((chunk: string) => void) | null = null;
    let closeStream: (() => void) | null = null;
    let resolveStream: ((v: StreamEvent[]) => void) | null = null;
    server.use(
      happyInvocation(RUNNING_DOC),
      http.get(
        "/api/workspaces/t1/invocations/inv_001/agents/alpha/stream",
        () =>
          new Promise<Response>((r) => {
            resolveStream = (events) => r(HttpResponse.json(events));
          }),
      ),
      http.get("/api/workspaces/t1/events", () => {
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            const enc = new TextEncoder();
            sendStream = (chunk) => controller.enqueue(enc.encode(chunk));
            closeStream = () => controller.close();
          },
        });
        return new HttpResponse(stream, {
          headers: { "Content-Type": "text/event-stream" },
        });
      }),
    );
    renderAt();
    const sendEarly = (text: string) => {
      const payload = JSON.stringify({
        inv_id: "inv_001",
        role: "alpha",
        event: JSON.stringify({ type: text }),
      });
      sendStream!(`event: stream\ndata: ${payload}\n\n`);
    };
    await screen.findByTestId("workflow-node-alpha");
    sendEarly("early-pre-open");
    await selectStep("alpha");
    resolveStream!([{ type: "early-pre-open" }]);
    await screen.findByTestId("stream-row-early-pre-open");
    expect(
      screen.getAllByTestId("stream-row-early-pre-open"),
    ).toHaveLength(1);
    closeStream!();
  });

  it("appends live stream events to the open panel", async () => {
    let send: ((chunk: string) => void) | null = null;
    let close: (() => void) | null = null;
    server.use(
      happyInvocation(RUNNING_DOC),
      happyStream("alpha", []),
      http.get("/api/workspaces/t1/events", () => {
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            const enc = new TextEncoder();
            send = (chunk) => controller.enqueue(enc.encode(chunk));
            close = () => controller.close();
          },
        });
        return new HttpResponse(stream, {
          headers: { "Content-Type": "text/event-stream" },
        });
      }),
    );
    renderAt();
    await selectStep("alpha");
    const payload = JSON.stringify({
      inv_id: "inv_001",
      role: "alpha",
      event: JSON.stringify({ type: "assistant", text: "thinking" }),
    });
    send!(`event: stream\ndata: ${payload}\n\n`);
    expect(
      await screen.findByTestId("stream-row-assistant"),
    ).toBeInTheDocument();
    close!();
  });

  it("ignores malformed live stream payload", async () => {
    let send: ((chunk: string) => void) | null = null;
    let close: (() => void) | null = null;
    server.use(
      happyInvocation(),
      happyStream("alpha", []),
      http.get("/api/workspaces/t1/events", () => {
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            const enc = new TextEncoder();
            send = (chunk) => controller.enqueue(enc.encode(chunk));
            close = () => controller.close();
          },
        });
        return new HttpResponse(stream, {
          headers: { "Content-Type": "text/event-stream" },
        });
      }),
    );
    renderAt();
    await selectStep("alpha");
    send!(
      `event: stream\ndata: {"inv_id":"inv_001","role":"alpha","event":"not-json"}\n\n`,
    );
    expect(screen.queryByTestId(/stream-row-assistant/)).toBeNull();
    close!();
  });

  it("ignores unrelated SSE events", async () => {
    let send: ((chunk: string) => void) | null = null;
    let close: (() => void) | null = null;
    server.use(
      happyInvocation(),
      http.get("/api/workspaces/t1/events", () => {
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            const enc = new TextEncoder();
            send = (chunk) => controller.enqueue(enc.encode(chunk));
            close = () => controller.close();
          },
        });
        return new HttpResponse(stream, {
          headers: { "Content-Type": "text/event-stream" },
        });
      }),
    );
    renderAt();
    await screen.findByTestId("workflow-node-orchestrator");
    send!(`event: job_completed\ndata: {"team":"research"}\n\n`);
    send!(`event: weird_kind\ndata: {}\n\n`);
    expect(screen.queryByTestId("stream-warning")).toBeNull();
    close!();
  });

  it("ignores malformed live node events", async () => {
    let send: ((chunk: string) => void) | null = null;
    let close: (() => void) | null = null;
    server.use(
      happyInvocation({
        ...BASE_DOC,
        tasks: [makeTask("orchestrator", { status: "running" })],
      }),
      http.get("/api/workspaces/t1/events", () => {
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            const enc = new TextEncoder();
            send = (chunk) => controller.enqueue(enc.encode(chunk));
            close = () => controller.close();
          },
        });
        return new HttpResponse(stream, {
          headers: { "Content-Type": "text/event-stream" },
        });
      }),
    );
    renderAt();
    await screen.findByTestId("workflow-node-orchestrator");
    send!(`event: node_completed\ndata: not json\n\n`);
    send!(`event: stream\ndata: not json\n\n`);
    expect(
      screen.getByTestId("workflow-node-orchestrator").dataset.status,
    ).toBe("running");
    close!();
  });

  it("shows the stream-warning when the event stream errors", async () => {
    server.use(happyInvocation(), noEvents());
    renderAt();
    expect(await screen.findByTestId("stream-warning")).toBeInTheDocument();
  });

  it("shows 'running…' duration when a node has no completed_at", async () => {
    server.use(
      happyInvocation({
        ...BASE_DOC,
        tasks: [
          makeTask("orchestrator", {
            status: "running",
            completed_at: null,
          }),
        ],
      }),
      noEvents(),
    );
    renderAt();
    expect(await screen.findByText("running…")).toBeInTheDocument();
  });

  it.each([
    {
      label: "response",
      settle: (resolve: (response: Response) => void) =>
        resolve(HttpResponse.json([{ type: "stale-alpha" }])),
      staleTestId: "stream-row-stale-alpha",
    },
    {
      label: "rejection",
      settle: (resolve: (response: Response) => void) =>
        resolve(HttpResponse.json({ detail: "late-fail" }, { status: 500 })),
      staleTestId: "stream-error",
    },
  ])(
    "discards a delayed stream $label when the user switched nodes",
    async ({ settle, staleTestId }) => {
      const gate: { resolve: ((response: Response) => void) | null } = {
        resolve: null,
      };
      server.use(
        happyInvocation(RUNNING_DOC),
        http.get(
          "/api/workspaces/t1/invocations/inv_001/agents/alpha/stream",
          () =>
            new Promise<Response>((resolve) => {
              gate.resolve = resolve;
            }),
        ),
        happyStream("synthesizer", [{ type: "fresh-synth" }]),
        noEvents(),
      );
      renderAt();
      await selectStep("alpha");
      await selectStep("synthesizer");
      expect(
        await screen.findByTestId("stream-row-fresh-synth"),
      ).toBeInTheDocument();
      settle(gate.resolve!);
      await delay(180);
      expect(screen.queryByTestId(staleTestId)).toBeNull();
    },
  );

  it("falls back to 'event' row label when type is missing", async () => {
    server.use(
      happyInvocation(RUNNING_DOC),
      happyStream("alpha", [{ result: "no-type-here" }]),
      noEvents(),
    );
    renderAt();
    await selectStep("alpha");
    expect(await screen.findByTestId("stream-row-event")).toBeInTheDocument();
  });

  it("hides duration when started_at is unparseable", async () => {
    server.use(
      happyInvocation({
        ...BASE_DOC,
        tasks: [
          makeTask("orchestrator", {
            status: "running",
            started_at: "not-a-date",
            completed_at: null,
          }),
        ],
      }),
      noEvents(),
    );
    renderAt();
    await screen.findByTestId("workflow-node-orchestrator");
    expect(screen.queryByText("running…")).not.toBeInTheDocument();
  });

  it("hides duration when started_at is null", async () => {
    server.use(
      happyInvocation({
        ...BASE_DOC,
        tasks: [
          makeTask("orchestrator", {
            status: "pending",
            started_at: null,
            completed_at: null,
          }),
        ],
      }),
      noEvents(),
    );
    renderAt();
    await screen.findByTestId("workflow-node-orchestrator");
    expect(screen.queryByText("running…")).not.toBeInTheDocument();
  });
});
