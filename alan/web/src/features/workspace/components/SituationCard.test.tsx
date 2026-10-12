import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { WorkspaceDetail } from "../../../api";
import { makeWorkspaceDetail } from "../../../test/workspaceFixtures";
import type { WorkspaceLifecycle } from "../hooks/useWorkspaceLifecycle";
import type { LiveWorkspaceState } from "../hooks/workflowLive";
import SituationCard from "./SituationCard";

const NO_LIVE: LiveWorkspaceState = {
  invocations: {},
  streamError: null,
  notices: [],
  stickyNotice: null,
};

function makeLifecycle(overrides: Partial<WorkspaceLifecycle> = {}): WorkspaceLifecycle {
  return {
    toggleBusy: false,
    toggleError: null,
    pendingIsArchived: false,
    handleToggle: vi.fn(),
    stopResumeMode: null,
    stopResumeBusy: false,
    stopResumeError: null,
    handleStopResume: vi.fn(),
    deleteOpen: false,
    openDelete: vi.fn(),
    closeDelete: vi.fn(),
    ...overrides,
  };
}

function renderCard(
  wsOverrides: Partial<WorkspaceDetail>,
  lifecycleOverrides: Partial<WorkspaceLifecycle> = {},
  live: LiveWorkspaceState = NO_LIVE,
) {
  const ws = makeWorkspaceDetail(wsOverrides);
  const lifecycle = makeLifecycle(lifecycleOverrides);
  const onRefresh = vi.fn();
  render(<SituationCard ws={ws} taskId="t1" live={live} lifecycle={lifecycle} onRefresh={onRefresh} />);
  return { lifecycle, onRefresh };
}

describe("SituationCard", () => {
  it("renders exactly one card with a tone-carrying headline", () => {
    renderCard({ status: "active" });
    const card = screen.getByTestId("situation-card");
    expect(card).toBeInTheDocument();
    const headline = screen.getByTestId("situation-headline");
    expect(headline).toHaveTextContent("Running");
    expect(headline.className).toContain("status-active");
    expect(screen.getByTestId("situation-body")).toBeInTheDocument();
  });

  it("does not duplicate the page's status badge testid", () => {
    renderCard({ status: "active" });
    expect(screen.queryByTestId("status-active")).not.toBeInTheDocument();
  });

  it("mounts the facts region", () => {
    renderCard({ status: "active" });
    expect(screen.getByTestId("situation-facts")).toBeInTheDocument();
  });

  it("shows a Stop primary action while running with a live job and wires it to the shared lifecycle handler", async () => {
    const user = userEvent.setup();
    const { lifecycle } = renderCard(
      { status: "active", has_running_job: true },
      { stopResumeMode: "stop" },
    );
    const button = screen.getByTestId("situation-stop-button");
    expect(button).toHaveTextContent("Stop");
    await user.click(button);
    expect(lifecycle.handleStopResume).toHaveBeenCalledTimes(1);
  });

  it("shows a Resume primary action while paused", () => {
    renderCard({ status: "paused" }, { stopResumeMode: "resume" });
    expect(screen.getByTestId("situation-resume-button")).toHaveTextContent("Resume");
  });

  it("shows a Re-queue primary action for needs_you with a resumable failed job", () => {
    renderCard(
      { status: "awaiting_input", has_resumable_failed_job: true },
      { stopResumeMode: "requeue" },
    );
    expect(screen.getByTestId("situation-requeue-button")).toHaveTextContent("Re-queue");
  });

  it("renders no primary action button for a completed workspace", () => {
    renderCard({ status: "completed" });
    expect(screen.queryByTestId("situation-stop-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("situation-resume-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("situation-requeue-button")).not.toBeInTheDocument();
  });

  it("shows the busy label while a stop/resume/requeue action is pending", () => {
    renderCard(
      { status: "active", has_running_job: true },
      { stopResumeMode: "stop", stopResumeBusy: true },
    );
    expect(screen.getByTestId("situation-stop-button")).toHaveTextContent("Stopping…");
  });

  it("renders the deploy_wait body as a status output naming the deploy verification wait", () => {
    renderCard({
      status: "active",
      pending_deploy_gate: {
        path: "awaiting_live",
        deadline: null,
        race_deadline: null,
        merged_at: null,
        waited_s: 90,
      },
    });
    const body = screen.getByTestId("pending-deploy-gate-notice");
    expect(body).toHaveAttribute("aria-label", "Deploy verification status");
    expect(body).toHaveTextContent("waiting on purpose");
    expect(screen.getByRole("status")).toBe(body);
  });

  it("gives escalated a glyph, matching the status badge's own glyph table", () => {
    renderCard({ status: "escalated" });
    const headline = screen.getByTestId("situation-headline");
    expect(headline).toHaveTextContent("Escalated");
    expect(headline).toHaveTextContent("↑");
  });

  it("gives partial the same headline word the status badge uses, with no glyph", () => {
    renderCard({ status: "partial" });
    const headline = screen.getByTestId("situation-headline");
    expect(headline).toHaveTextContent("Partial");
    expect(headline.querySelector('[aria-hidden="true"]')).toBeNull();
  });
});
