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

import type { ParkReason } from "../../../api";
import { server } from "../../../test/server";
import { BASE, installWorkspaceDefaultHandlers, noEventsHandler, renderAt } from "../../../test/workspacePageHarness";

installWorkspaceDefaultHandlers();

describe("StuckBanner integration", () => {
  it("shows stuck-banner after router_escalate SSE event and clears on navigation away", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get(
        "/api/workspaces/t1/events",
        () =>
          new HttpResponse(
            'event: router_escalate\ndata: {"task_id":"t1","from_team":"implementation","kind":"escalate","reason":"loop detected"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    await waitFor(() => {
      expect(screen.getByTestId("stuck-banner")).toBeInTheDocument();
    });
    expect(screen.getByTestId("stuck-banner")).toHaveTextContent(
      "Escalated — loop detected",
    );
    await userEvent.click(screen.getByTestId("workspace-back-link"));
    await screen.findByTestId("repo-dashboard-page");
    expect(screen.queryByTestId("stuck-banner")).not.toBeInTheDocument();
  });
});

describe("Situation card terminal-outcome integration", () => {
  it("shows the escalation reason in the situation body for an escalated workspace", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "escalated",
          terminal_outcome: { outcome: "escalated", reason: "Could not proceed autonomously" },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const body = await screen.findByTestId("situation-body");
    expect(screen.getByTestId("situation-headline")).toHaveTextContent("Escalated");
    expect(body).toHaveTextContent("Could not proceed autonomously");
  });

  it("shows the failure reason in the situation body for a failed workspace", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "failed",
          terminal_outcome: { outcome: "failed", reason: "Build step failed" },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const body = await screen.findByTestId("situation-body");
    expect(screen.getByTestId("situation-headline")).toHaveTextContent("Failed");
    expect(body).toHaveTextContent("Build step failed");
  });

  it("falls back to a generic body sentence when terminal_outcome is null", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, status: "failed", terminal_outcome: null }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("situation-body")).toHaveTextContent("This run failed.");
  });

  it("does not render the merge-block-class chip when terminal_outcome field is absent", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("merge-block-class")).toBeNull();
  });

  it("situation body appears before the manager-summary fact in DOM", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "failed",
          terminal_outcome: { outcome: "failed", reason: "Crashed" },
          manager_summary: { ok: true, text: "What happened summary." },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("situation-body");
    const body = screen.getByTestId("situation-body");
    const panel = screen.getByTestId("manager-summary-panel");
    expect(
      body.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});

describe("PendingDeployGateNotice integration", () => {
  it("shows the notice for a deliberately-parked verification attempt", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          pending_deploy_gate: {
            path: "awaiting_live",
            deadline: 2_000,
            race_deadline: null,
            merged_at: 1_800,
            waited_s: 90,
          },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const notice = await screen.findByTestId("pending-deploy-gate-notice");
    expect(notice).toBeInTheDocument();
    expect(notice).toHaveTextContent("waiting on purpose");
    expect(notice).toHaveTextContent("confirming the merge is live via local observation");
  });

  it("renders no notice for an attempt that has never evaluated", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, pending_deploy_gate: null }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("pending-deploy-gate-notice")).toBeNull();
  });

  it("renders no notice when the pending_deploy_gate field is absent", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("pending-deploy-gate-notice")).toBeNull();
  });

  it("a failed workspace does not surface a deploy-gate notice, even with a stale pending_deploy_gate", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "failed",
          terminal_outcome: { outcome: "failed", reason: "Build error" },
          pending_deploy_gate: {
            path: "awaiting_restart",
            deadline: null,
            race_deadline: 2_000,
            merged_at: null,
            waited_s: 30,
          },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("situation-body");
    expect(screen.queryByTestId("pending-deploy-gate-notice")).toBeNull();
  });

  it("renders the deploy-gate notice before the manager-summary fact, when both are present", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          pending_deploy_gate: {
            path: "awaiting_restart",
            deadline: null,
            race_deadline: 2_000,
            merged_at: null,
            waited_s: 30,
          },
          manager_summary: { ok: true, text: "What happened summary." },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const notice = await screen.findByTestId("pending-deploy-gate-notice");
    const panel = screen.getByTestId("manager-summary-panel");
    expect(
      notice.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("presents a repeated attempt as one updated notice, not a duplicate", async () => {
    const user = userEvent.setup();
    let waitedS = 90;
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          pending_deploy_gate: {
            path: "awaiting_live",
            deadline: 2_000,
            race_deadline: null,
            merged_at: 1_800,
            waited_s: waitedS,
          },
        }),
      ),
      noEventsHandler(),
      http.post("/api/workspaces/t1/archive", () => {
        waitedS = 3_600;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderAt();
    await screen.findByTestId("pending-deploy-gate-notice");
    expect(screen.getByTestId("pending-deploy-gate-waited")).toHaveTextContent("2m");

    await user.click(await screen.findByRole("button", { name: "Archive" }));
    await waitFor(() => {
      expect(screen.getByTestId("pending-deploy-gate-waited")).toHaveTextContent("1h 0m");
    });
    expect(screen.getAllByTestId("pending-deploy-gate-notice")).toHaveLength(1);
  });
});

describe("ParkReasonPanel", () => {
  it("renders the typed park reason and every blocker for a merge-blocked workspace", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "awaiting_input",
          park_reason: "merge_block",
          blockers: [
            {
              kind: "failing_check",
              name: "Tests",
              detail: "check failing: Tests",
            },
            {
              kind: "review_decision",
              name: "CHANGES_REQUESTED",
              detail: "PR review decision: CHANGES_REQUESTED",
            },
            {
              kind: "merge_state_status",
              name: "BEHIND",
              detail: "mergeStateStatus: BEHIND",
            },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const panel = await screen.findByTestId("park-reason-panel");
    expect(panel).toBeInTheDocument();
    expect(screen.getByTestId("park-reason-label")).toHaveTextContent("Merge blocked");
    const items = screen.getAllByTestId("blocker-item");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Tests");
    expect(items[0]).toHaveTextContent("check failing: Tests");
    expect(items[1]).toHaveTextContent("CHANGES_REQUESTED");
    expect(items[2]).toHaveTextContent("BEHIND");
  });

  it("renders a failing_test blocker naming the spec file and test title", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "awaiting_input",
          park_reason: "merge_block",
          blockers: [
            {
              kind: "failing_test",
              name: "e2e/explore-chat.spec.ts",
              detail: "Explore chat renders",
            },
          ],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const item = await screen.findByTestId("blocker-item");
    expect(item).toHaveTextContent("failing_test");
    expect(item).toHaveTextContent("e2e/explore-chat.spec.ts");
    expect(item).toHaveTextContent("Explore chat renders");
  });

  it("labels every ParkReason value distinctly (exhaustive-map coverage)", async () => {
    const expectedLabels: Record<ParkReason, string> = {
      question: "Question for you",
      crash: "Worker crashed",
      merge_block: "Merge blocked",
      stall: "Dependency graph stalled",
      routing_error: "Routing error",
      escalated: "Escalated",
      frozen_chain: "Frozen dependency chain",
      credential: "Credential problem",
      unarchived: "Restored from archive",
    };
    const reasons = Object.entries(expectedLabels) as [ParkReason, string][];
    for (const [reason, label] of reasons) {
      server.use(
        http.get("/api/workspaces/t1", () =>
          HttpResponse.json({
            ...BASE,
            status: "awaiting_input",
            park_reason: reason,
            blockers: [],
          }),
        ),
        noEventsHandler(),
      );
      const { unmount } = renderAt();
      expect(await screen.findByTestId("park-reason-label")).toHaveTextContent(label);
      unmount();
    }
  });

  it("does not show the panel when the workspace is not parked", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "active",
          park_reason: "crash",
          blockers: [{ kind: "reason", name: "reason", detail: "worker died" }],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("park-reason-panel")).toBeNull();
  });

  it("renders cleanly with no park_reason and no blockers on a parked workspace", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, status: "awaiting_input" }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("park-reason-panel")).toBeNull();
  });

  it("shows blockers even when park_reason is absent", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "escalated",
          blockers: [{ kind: "reason", name: "reason", detail: "loop guard exhausted" }],
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const panel = await screen.findByTestId("park-reason-panel");
    expect(panel).toBeInTheDocument();
    expect(screen.queryByTestId("park-reason-label")).toBeNull();
    expect(screen.getByTestId("blocker-item")).toHaveTextContent("loop guard exhausted");
  });
});

function decisionQuestion(overrides: Record<string, unknown> = {}) {
  return {
    id: "q1",
    question: "Which storage backend?",
    proposed_answer_space: ["duckdb", "postgres"],
    resolver_role: "human",
    blocking: true,
    prior_attempts: [],
    question_class: null,
    recommended: "duckdb",
    ...overrides,
  };
}

function decisionHandover(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: "1.11",
    handover_id: "h1",
    handover_type: "clarification_request",
    task_id: "t1",
    workspace_id: "t1",
    from_team: "research",
    from_invocation_id: "inv_001",
    to_team_suggestions: [],
    iteration: 1,
    revisit_of: null,
    timestamp_utc: "2026-05-12T08:00:00Z",
    next_hop: null,
    decisions: [],
    open_questions: [decisionQuestion()],
    assumptions: [],
    constraints_inherited: [],
    artifacts_produced: [],
    full_synthesis: "needs a human answer",
    forbidden_actions: [],
    prior_attempts_summary: null,
    pr_number: null,
    files_to_edit: [],
    test_scenarios: [],
    ...overrides,
  };
}

describe("DecisionCard", () => {
  it("renders the blocking question read-only for a parked workspace, with no radiogroup or submit control", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, status: "escalated", latest_handover: decisionHandover() }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("decision-card")).toBeInTheDocument();
    expect(screen.getByText("Blocking decision")).toBeInTheDocument();
    expect(screen.getByText("Which storage backend?")).toBeInTheDocument();
    expect(screen.getByText("blocking")).toBeInTheDocument();
    expect(screen.getByText(/resolver: human/)).toBeInTheDocument();
    const options = screen.getAllByTestId("open-question-option");
    expect(options.map((el) => el.textContent)).toEqual(["duckdb", "postgres"]);
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
    expect(screen.queryByTestId("decision-form-submit")).not.toBeInTheDocument();
    expect(screen.queryByTestId("decision-recommended-badge")).not.toBeInTheDocument();
    expect(screen.queryByTestId("decision-resolver-chip")).not.toBeInTheDocument();
  });

  it("shows the recorded answer read-only once one is present on the handover", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "escalated",
          latest_handover: decisionHandover({
            open_questions: [decisionQuestion({ answer: "postgres" })],
          }),
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("open-question-answer")).toHaveTextContent("Answer: postgres");
  });

  it("does not render for a parked workspace whose latest handover has no blocking question", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          status: "escalated",
          latest_handover: decisionHandover({
            open_questions: [decisionQuestion({ blocking: false })],
          }),
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("decision-card")).toBeNull();
  });

  it("does not render for a non-parked workspace even if latest_handover carries a blocking question", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, status: "active", latest_handover: decisionHandover() }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("decision-card")).toBeNull();
  });
});
