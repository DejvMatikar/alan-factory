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

import {
  activateDisclosure,
  expectDisclosureState,
  expectTogglesOnEveryActivation,
} from "../../../test/disclosure";
import { server } from "../../../test/server";
import { BASE, installWorkspaceDefaultHandlers, noEventsHandler, renderAt } from "../../../test/workspacePageHarness";

installWorkspaceDefaultHandlers();

const COST_BREAKDOWN = [
  {
    key: "team_runs",
    label: "Team & agent runs",
    run_summary: {
      model: "aggregate",
      effort: null,
      system_prompt: "",
      total_cost_usd: 0.0025,
      total_output_tokens: null,
      total_input_tokens: null,
      tool_counts: [],
    },
  },
];

function serveWorkspace(overrides: Record<string, unknown> = {}) {
  server.use(
    http.get("/api/workspaces/t1", () => HttpResponse.json({ ...BASE, ...overrides })),
    noEventsHandler(),
  );
}

describe("Workspace disclosure defaults", () => {
  it("hides the task description without a click and lets the user open and reclose it", async () => {
    serveWorkspace();
    renderAt();
    const details = await screen.findByTestId("workspace-task-description-disclosure");
    await expectDisclosureState(details, false);
    expect(screen.getByTestId("workspace-task-description")).not.toBeVisible();
    await expectTogglesOnEveryActivation(details);
  });

  it("shows the task description content once opened", async () => {
    serveWorkspace();
    renderAt();
    const details = await screen.findByTestId("workspace-task-description-disclosure");
    activateDisclosure(details, "Enter");
    await expectDisclosureState(details, true);
    expect(screen.getByTestId("workspace-task-description")).toBeVisible();
  });

  it("starts both the cost breakdown and the task description closed", async () => {
    serveWorkspace({ total_cost_usd: 0.0025, cost_breakdown: COST_BREAKDOWN });
    renderAt();
    const breakdown = await screen.findByTestId("workspace-cost-breakdown");
    await expectDisclosureState(breakdown, false);
    expect(screen.getByTestId("run-summary-header-team_runs")).not.toBeVisible();
    await expectDisclosureState(screen.getByTestId("workspace-task-description-disclosure"), false);
  });

  it("opens and closes the cost breakdown on click, Enter and Space and reveals its rows while open", async () => {
    serveWorkspace({ total_cost_usd: 0.0025, cost_breakdown: COST_BREAKDOWN });
    renderAt();
    const breakdown = await screen.findByTestId("workspace-cost-breakdown");
    await expectTogglesOnEveryActivation(breakdown);
    activateDisclosure(breakdown, "Space");
    await expectDisclosureState(breakdown, true);
    expect(screen.getByTestId("run-summary-header-team_runs")).toBeVisible();
  });
});
