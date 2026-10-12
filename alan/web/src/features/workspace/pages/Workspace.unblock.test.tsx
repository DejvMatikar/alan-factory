import { render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  function ReactFlow({
    nodes,
    nodeTypes,
  }: {
    nodes: { id: string; type: string; data: Record<string, unknown> }[];
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

import { server } from "../../../test/server";
import Workspace from "./Workspace";

const BASE = {
  task_id: "t1",
  title: "T1 Title",
  description: "do it",
  status: "awaiting_input" as const,
  revisit_counts: {},
  created_at: "2026-09-19T08:00:00Z",
  updated_at: "2026-09-19T08:00:00Z",
  history: [],
  latest_handover: null,
  has_running_job: false,
  has_resumable_failed_job: false,
  requeue_count: 0,
  tasks: [],
  scope_audit: [],
  requirements: null,
  total_cost_usd: null,
};

function renderAt(taskId = "t1") {
  return render(
    <MemoryRouter initialEntries={[`/workspaces/${taskId}`]}>
      <Routes>
        <Route path="/workspaces/:taskId" element={<Workspace />} />
        <Route path="/" element={<div data-testid="list-page">list</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function noEventsHandler() {
  return http.get("/api/workspaces/t1/events", () => HttpResponse.error());
}

beforeEach(() => {
  server.use(
    http.get(/\/api\/workspaces\/[^/]+\/dispatch-instructions$/, () => HttpResponse.json({})),
    http.get(/\/api\/workspaces\/[^/]+\/sticky-instructions$/, () =>
      HttpResponse.json({ text: null, origin: null }),
    ),
  );
});

describe("Workspace page — UnblockNotice mounting", () => {
  it("renders no unblock notice when the workspace has no unblock record", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json({ ...BASE, unblock: null })),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("unblock-notice")).not.toBeInTheDocument();
  });

  it("renders the host-owned notice for a not_yet unblock record", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          unblock: {
            shape: "stale_review",
            attempts: 1,
            ceiling: 5,
            outcome: "not_yet",
            summary: "waiting · stale_review · re-probing · 2026-09-19T11:55Z",
            issue_url: null,
            at: Date.now() / 1000,
            action: null,
            reason: "readiness pending",
            authority_holder: null,
          },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const notice = await screen.findByTestId("unblock-notice");
    expect(notice).toBeVisible();
    expect(screen.getByTestId("unblock-state-label")).toHaveTextContent(
      "Host is waiting and re-probing",
    );
  });

  it("renders the needs-you notice, naming the authority holder, for a reported unblock record", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "escalated",
          unblock: {
            shape: "no_commit_escalation",
            attempts: 5,
            ceiling: 5,
            outcome: "needs_human",
            summary: "needs human · no_commit_escalation · per-workspace ceiling · 2026-09-19T11:00Z",
            issue_url: "https://github.com/DejvMatikar/alan/issues/1701",
            at: Date.now() / 1000,
            action: null,
            reason: "per-workspace attempt ceiling reached",
            authority_holder: "human_operator",
          },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const notice = await screen.findByTestId("unblock-notice");
    expect(notice).toBeVisible();
    expect(screen.getByTestId("unblock-state-label")).toHaveTextContent("Needs you");
    expect(screen.getByTestId("unblock-authority-holder")).toHaveTextContent(
      "you, the human operator",
    );
    expect(screen.getByTestId("unblock-issue-link")).toHaveAttribute(
      "href",
      "https://github.com/DejvMatikar/alan/issues/1701",
    );
  });
});
