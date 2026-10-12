import { screen } from "@testing-library/react";
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

import type { TaskStatus } from "../../../api";
import { server } from "../../../test/server";
import { makeWorkspaceTask } from "../../../test/workspaceFixtures";
import { workspaceStatusLine } from "./Workspace";
import { BASE, installWorkspaceDefaultHandlers, noEventsHandler, renderAt } from "../../../test/workspacePageHarness";

installWorkspaceDefaultHandlers();

describe("workspaceStatusLine", () => {
  const makeTasks = (statuses: TaskStatus[]) =>
    statuses.map((status, i) => makeWorkspaceTask({ id: `t${i}`, status }));

  it("returns empty string when tasks.length <= 1", () => {
    expect(workspaceStatusLine({ ...BASE, tasks: [] })).toBe("");
    expect(workspaceStatusLine({ ...BASE, tasks: makeTasks(["completed"]) })).toBe("");
  });

  it("shows X of N done for all-completed", () => {
    const ws = { ...BASE, tasks: makeTasks(["completed", "completed"]) };
    expect(workspaceStatusLine(ws)).toBe("2 of 2 done");
  });

  it("includes in-progress bucket when running tasks present", () => {
    const ws = { ...BASE, tasks: makeTasks(["completed", "running", "pending"]) };
    const line = workspaceStatusLine(ws);
    expect(line).toContain("1 of 3 done");
    expect(line).toContain("2 in progress");
  });

  it("includes needs-you bucket for awaiting_input", () => {
    const ws = { ...BASE, tasks: makeTasks(["completed", "awaiting_input"]) };
    const line = workspaceStatusLine(ws);
    expect(line).toContain("1 of 2 done");
    expect(line).toContain("1 needs you");
  });

  it("includes failed bucket for failed tasks", () => {
    const ws = { ...BASE, tasks: makeTasks(["completed", "failed"]) };
    const line = workspaceStatusLine(ws);
    expect(line).toContain("1 of 2 done");
    expect(line).toContain("1 failed");
  });

  it("shows all buckets when present", () => {
    const ws = { ...BASE, tasks: makeTasks(["completed", "running", "awaiting_input", "failed"]) };
    const line = workspaceStatusLine(ws);
    expect(line).toContain("1 of 4 done");
    expect(line).toContain("1 needs you");
    expect(line).toContain("1 in progress");
    expect(line).toContain("1 failed");
  });
});

describe("Workspace detail", () => {
  it("mounts delivery-summary-panel when completed, absent otherwise", async () => {
    const apiMod = await import("../../../api");
    const original = apiMod.api.getDelivery;
    apiMod.api.getDelivery = () =>
      Promise.resolve({
        pr_number: 1,
        repo: "DejvMatikar/alan",
        pr_url: null,
        pr_title: null,
        merged_at: null,
        files_changed: [],
        commits: [],
      });
    try {
      server.use(
        http.get("/api/workspaces/t1", () =>
          HttpResponse.json({ ...BASE, status: "completed", live_at: "2026-05-20T10:00:00Z" }),
        ),
        noEventsHandler(),
      );
      const { unmount } = renderAt();
      expect(
        await screen.findByTestId("delivery-summary-panel"),
      ).toBeInTheDocument();
      unmount();

      server.use(
        http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
        noEventsHandler(),
      );
      renderAt();
      await screen.findByRole("heading", { level: 1, name: "T1 Title" });
      expect(screen.queryByTestId("delivery-summary-panel")).toBeNull();
    } finally {
      apiMod.api.getDelivery = original;
    }
  });

  it("shows the PR exactly once in the main column, on the situation card, not duplicated in the delivery summary", async () => {
    const apiMod = await import("../../../api");
    const original = apiMod.api.getDelivery;
    apiMod.api.getDelivery = () =>
      Promise.resolve({
        pr_number: 1,
        repo: "DejvMatikar/alan",
        pr_url: null,
        pr_title: "feat: ship it",
        merged_at: null,
        files_changed: ["a.ts"],
        commits: [{ sha: "abc1234", message: "feat: scaffold" }],
      });
    try {
      server.use(
        http.get("/api/workspaces/t1", () =>
          HttpResponse.json({ ...BASE, status: "completed", live_at: "2026-05-20T10:00:00Z" }),
        ),
        noEventsHandler(),
      );
      renderAt();
      expect(await screen.findByTestId("situation-pr-title-link")).toBeInTheDocument();
      await screen.findByTestId("delivery-summary-panel");
      expect(screen.getAllByRole("link", { name: "feat: ship it" })).toHaveLength(1);
      expect(screen.queryByTestId("what-shipped")).toBeNull();
    } finally {
      apiMod.api.getDelivery = original;
    }
  });

  it("renders a 'Live since' label from the workspace's live_at, and omits it when live_at is null", async () => {
    const apiMod = await import("../../../api");
    const original = apiMod.api.getDelivery;
    apiMod.api.getDelivery = () =>
      Promise.resolve({
        pr_number: 1,
        repo: "DejvMatikar/alan",
        pr_url: null,
        pr_title: null,
        merged_at: null,
        files_changed: [],
        commits: [],
      });
    try {
      server.use(
        http.get("/api/workspaces/t1", () =>
          HttpResponse.json({ ...BASE, status: "completed", live_at: "2026-05-21T09:30:00Z" }),
        ),
        noEventsHandler(),
      );
      const { unmount } = renderAt();
      expect(await screen.findByTestId("live-since")).toHaveTextContent("Live since");
      unmount();

      server.use(
        http.get("/api/workspaces/t1", () =>
          HttpResponse.json({ ...BASE, status: "completed", live_at: null }),
        ),
        noEventsHandler(),
      );
      renderAt();
      await screen.findByTestId("delivery-summary-panel");
      expect(screen.queryByTestId("live-since")).toBeNull();
    } finally {
      apiMod.api.getDelivery = original;
    }
  });

  it("renders requirements checklist when requirements is non-empty", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          requirements: [
            {
              text: "It works",
              test_ref: "test_it_works",
              status: "PASS",
              merged_pr_url: null,
              withdrawn_by_question_id: null,
            },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(
      await screen.findByTestId("requirements-checklist"),
    ).toBeInTheDocument();
    expect(screen.getByText("It works")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 3, name: "Requirements" }),
    ).toBeInTheDocument();
  });

  it("does not render requirements section when requirements is null", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, requirements: null }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("requirements-checklist")).toBeNull();
  });

  it("omits the acceptance checklist entirely when there are no requirements, DoD clauses, or assumptions", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, requirements: [] }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("acceptance-checklist")).toBeNull();
  });

  it("marks a requirement as not mechanically checked when its id is in unproven_clause_ids", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          requirements: [
            {
              id: "req-1",
              text: "It works",
              test_ref: null,
              status: "PENDING",
              merged_pr_url: null,
              withdrawn_by_question_id: null,
            },
          ],
          unproven_clause_ids: ["req-1"],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(
      await screen.findByLabelText("status: not mechanically checked"),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("status: pending")).not.toBeInTheDocument();
  });

  it("treats every requirement as proven when unproven_clause_ids is absent from the response", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          requirements: [
            {
              id: "req-1",
              text: "It works",
              test_ref: null,
              status: "PENDING",
              merged_pr_url: null,
              withdrawn_by_question_id: null,
            },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByLabelText("status: pending")).toBeInTheDocument();
    expect(
      screen.queryByLabelText("status: not mechanically checked"),
    ).not.toBeInTheDocument();
  });

  it("summarizes requirements and per-task DoD clauses together under one 'N of M met' heading", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          requirements: [
            {
              id: "req-1",
              text: "It works",
              test_ref: "test_it_works",
              status: "PASS",
              merged_pr_url: null,
              withdrawn_by_question_id: null,
            },
          ],
          tasks: [
            {
              id: "be",
              depends_on: [],
              team: "implementation",
              status: "completed",
              dod_clauses: [{ id: "dod-1", text: "endpoint works", covers: [], evidence: "PR #5" }],
            },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("acceptance-checklist-summary")).toHaveTextContent(
      "2 of 2 met",
    );
    expect(screen.getByTestId("task-dod-be")).toBeInTheDocument();
  });

  it("renders assumptions checklist derived from raw backend strings without the always-open chip", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          assumptions: ["Assumed retries are safe"],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(
      await screen.findByTestId("assumptions-checklist"),
    ).toBeInTheDocument();
    expect(screen.getByText("Assumed retries are safe")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 3, name: "Assumptions" }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("status: open")).toBeNull();
  });

  it("does not render assumptions section when assumptions is absent", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("assumptions-checklist")).toBeNull();
  });

  it("does not render assumptions section when assumptions is an empty list", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, assumptions: [] }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("assumptions-checklist")).toBeNull();
    expect(
      screen.queryByRole("heading", { level: 3, name: "Assumptions" }),
    ).toBeNull();
  });

  it("shows workspace-status-line with bucketed counts for multi-task workspace", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "a", depends_on: [], team: "implementation_plan", status: "completed" },
            { id: "b", depends_on: ["a"], team: null, status: "running" },
            { id: "c", depends_on: [], team: "research", status: "failed" },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const statusLine = await screen.findByTestId("workspace-status-line");
    expect(statusLine).toHaveTextContent("1 of 3 done");
    expect(statusLine).toHaveTextContent("1 in progress");
    expect(statusLine).toHaveTextContent("1 failed");
  });

  it("does not show workspace-status-line for single-task workspace", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("workspace-status-line")).toBeNull();
  });

  it("workspace-status-line shows needs-you bucket for awaiting_input tasks", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          tasks: [
            { id: "a", depends_on: [], team: "implementation_plan", status: "completed" },
            { id: "b", depends_on: ["a"], team: null, status: "awaiting_input" },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const statusLine = await screen.findByTestId("workspace-status-line");
    expect(statusLine).toHaveTextContent("1 of 2 done");
    expect(statusLine).toHaveTextContent("1 needs you");
  });

  it("renders WorkspaceNoteEditor on the detail page", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.getByTestId("note-editor")).toBeInTheDocument();
  });

  it("pre-fills note editor with existing note from workspace", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json({ ...BASE, note: "existing memo" })),
      noEventsHandler(),
    );
    renderAt();
    const textarea = await screen.findByRole("textbox", { name: "Personal note" });
    expect(textarea).toHaveValue("existing memo");
  });

  it("pre-fills note editor with empty string when note is null", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json({ ...BASE, note: null })),
      noEventsHandler(),
    );
    renderAt();
    const textarea = await screen.findByRole("textbox", { name: "Personal note" });
    expect(textarea).toHaveValue("");
  });
});
