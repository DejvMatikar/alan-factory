import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { DispatchInstructionsReadback, Handover } from "../../../api";
import {
  activateDisclosure,
  expectDisclosureState,
  expectTogglesOnEveryActivation,
} from "../../../test/disclosure";
import { makeHandover } from "../../../test/workspaceFixtures";
import { makeTask } from "../../../tests/fixtures/workflowTask";
import type { LiveInvocationState } from "../hooks/workflowLive";
import RunRow from "./RunRow";
import type { DocState } from "./InvocationCard";

const LIVE_EMPTY: LiveInvocationState = { nodeUpdates: [], liveStreams: {} };

function makeDoc(overrides: Partial<DocState> = {}): DocState {
  return {
    team: "research",
    status: "success",
    startedAt: "2026-05-12T08:00:00Z",
    completedAt: "2026-05-12T08:00:30Z",
    sourceTaskId: null,
    tasks: [],
    ...overrides,
  };
}

function renderRow(overrides: Partial<Parameters<typeof RunRow>[0]> = {}) {
  return render(
    <RunRow
      taskId="t1"
      invId="inv_001"
      doc={makeDoc()}
      loadError={null}
      liveInvocation={LIVE_EMPTY}
      readback={undefined}
      handover={null}
      repo="DejvMatikar/alan"
      {...overrides}
    />,
    { wrapper: MemoryRouter },
  );
}

describe("RunRow", () => {
  it("shows the humanized team label as the run's description", () => {
    renderRow({ doc: makeDoc({ team: "implementation_plan" }) });
    expect(screen.getByTestId("run-row-description-inv_001")).toHaveTextContent(
      "Implementation Plan",
    );
  });

  it("shows a loading description while the doc has not resolved", () => {
    renderRow({ doc: null, loadError: null });
    expect(screen.getByTestId("run-row-description-inv_001")).toHaveTextContent("loading…");
  });

  it("shows a load-failed description when the doc failed to load", () => {
    renderRow({ doc: null, loadError: "boom" });
    expect(screen.getByTestId("run-row-description-inv_001")).toHaveTextContent("load failed");
  });

  it("shows the status badge for the resolved doc", () => {
    renderRow({ doc: makeDoc({ status: "running" }) });
    expect(screen.getByTestId("node-status-running")).toBeInTheDocument();
  });

  it("shows no status badge while the doc has not resolved", () => {
    renderRow({ doc: null });
    expect(screen.queryByTestId(/node-status-/)).not.toBeInTheDocument();
  });

  it("shows an instruction badge without its details disclosure when a readback is given", () => {
    const readback: DispatchInstructionsReadback = { status: "attached", text: "do it", origin: null };
    renderRow({ readback });
    expect(screen.getByTestId("instruction-badge-inv_001")).toHaveTextContent(
      "instructions attached",
    );
    expect(screen.queryByTestId("instruction-badge-inv_001-details")).not.toBeInTheDocument();
  });

  it("shows the run's start time", () => {
    renderRow({ doc: makeDoc({ startedAt: "2026-05-12T08:00:00Z" }) });
    expect(screen.getByTestId("run-row-started-inv_001")).toBeInTheDocument();
  });

  it("shows the run's duration once completed", () => {
    renderRow({
      doc: makeDoc({ startedAt: "2026-05-12T08:00:00Z", completedAt: "2026-05-12T08:00:10Z" }),
    });
    expect(screen.getByTestId("run-row-duration-inv_001")).toHaveTextContent("10s");
  });

  it("omits the row's duration while the run is pending", () => {
    renderRow({ doc: makeDoc({ status: "pending" }) });
    expect(screen.queryByTestId("run-row-duration-inv_001")).not.toBeInTheDocument();
  });

  it("shows cost to two decimal places when cost data is present", () => {
    const doc = makeDoc({
      tasks: [makeTask("n1", { input_tokens: 100, output_tokens: 50, total_cost_usd: 1.2345 })],
    });
    renderRow({ doc });
    expect(screen.getByTestId("run-row-cost-inv_001")).toHaveTextContent("$1.23");
  });

  it("omits the cost figure when no cost data is recorded", () => {
    renderRow({ doc: makeDoc({ tasks: [] }) });
    expect(screen.queryByTestId("run-row-cost-inv_001")).not.toBeInTheDocument();
  });

  it("shows the hand-off target derived from the matching handover", () => {
    const handover: Handover = makeHandover({
      from_invocation_id: "inv_001",
      handover_type: "forward",
      to_team_suggestions: ["verification"],
    });
    renderRow({ handover });
    expect(screen.getByTestId("run-row-handoff-inv_001")).toHaveTextContent("verification");
  });

  it("reads the hand-off target as Completed for a terminal handover", () => {
    renderRow({ handover: makeHandover({ handover_type: "terminal" }) });
    expect(screen.getByTestId("run-row-handoff-inv_001")).toHaveTextContent("Completed");
  });

  it("omits the hand-off figure when there is no handover for this run", () => {
    renderRow({ handover: null });
    expect(screen.queryByTestId("run-row-handoff-inv_001")).not.toBeInTheDocument();
  });

  it("links to the full run view", () => {
    renderRow();
    const link = screen.getByTestId("run-row-full-view-inv_001");
    expect(link).toHaveAttribute("href", "/workspaces/t1/invocations/inv_001");
    expect(link).toHaveAccessibleName("Open full view of run inv_001");
  });

  it("starts the row's disclosure closed and toggles on click, Enter and Space", async () => {
    renderRow();
    const details = screen.getByTestId("run-row-disclosure-inv_001");
    await expectDisclosureState(details, false);
    await expectTogglesOnEveryActivation(details);
  });

  it("shows each step's role, status, duration and cost when expanded", async () => {
    const doc = makeDoc({
      tasks: [
        makeTask("n1", { role: "planner", status: "success", input_tokens: 10, output_tokens: 5, total_cost_usd: 0.01 }),
      ],
    });
    renderRow({ doc });
    const details = screen.getByTestId("run-row-disclosure-inv_001");
    activateDisclosure(details, "click");
    await expectDisclosureState(details, true);
    const step = screen.getByTestId("run-row-step-n1");
    expect(within(step).getByText("planner")).toBeInTheDocument();
    expect(within(step).getByTestId("node-status-success")).toBeInTheDocument();
  });

  it("omits a step's duration while it is still pending", async () => {
    const doc = makeDoc({
      tasks: [
        makeTask("n1", {
          role: "planner",
          status: "pending",
          started_at: null,
          completed_at: null,
          input_tokens: 10,
          output_tokens: 5,
          total_cost_usd: 0.01,
        }),
      ],
    });
    renderRow({ doc });
    const details = screen.getByTestId("run-row-disclosure-inv_001");
    activateDisclosure(details, "click");
    await expectDisclosureState(details, true);
    const step = screen.getByTestId("run-row-step-n1");
    expect(step.querySelectorAll(".tabular-nums")).toHaveLength(1);
  });

  it("omits a step's usage figure when it has no token or cost data", async () => {
    const doc = makeDoc({
      tasks: [makeTask("n1", { role: "planner", status: "success" })],
    });
    renderRow({ doc });
    const details = screen.getByTestId("run-row-disclosure-inv_001");
    activateDisclosure(details, "click");
    await expectDisclosureState(details, true);
    const step = screen.getByTestId("run-row-step-n1");
    expect(step.querySelectorAll(".tabular-nums")).toHaveLength(1);
  });

  it("shows no steps list when the run has no tasks", async () => {
    renderRow({ doc: makeDoc({ tasks: [] }) });
    const details = screen.getByTestId("run-row-disclosure-inv_001");
    activateDisclosure(details, "click");
    await expectDisclosureState(details, true);
    expect(screen.queryByTestId("run-row-steps-inv_001")).not.toBeInTheDocument();
  });

  it("shows the load error inside the expanded row", async () => {
    renderRow({ doc: null, loadError: "network down" });
    const details = screen.getByTestId("run-row-disclosure-inv_001");
    activateDisclosure(details, "click");
    await expectDisclosureState(details, true);
    expect(screen.getByTestId("run-row-error-inv_001")).toHaveTextContent("network down");
  });

  it("shows a bounded handover-highlights panel when expanded and a handover exists", async () => {
    const handover = makeHandover({ from_invocation_id: "inv_001", full_synthesis: "did the thing" });
    renderRow({ handover });
    const details = screen.getByTestId("run-row-disclosure-inv_001");
    activateDisclosure(details, "click");
    await expectDisclosureState(details, true);
    const panel = screen.getByTestId("run-row-handover-inv_001");
    const innerPanel = within(panel).getByTestId("handover-detail-panel");
    expect(innerPanel.className).toContain("max-h-");
    expect(innerPanel.className).toContain("overflow-y-auto");
    expect(within(panel).getByText("did the thing")).toBeInTheDocument();
  });

  it("shows no handover panel when there is no handover for this run", async () => {
    renderRow({ handover: null });
    const details = screen.getByTestId("run-row-disclosure-inv_001");
    activateDisclosure(details, "click");
    await expectDisclosureState(details, true);
    expect(screen.queryByTestId("run-row-handover-inv_001")).not.toBeInTheDocument();
  });
});
