import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type OpenQuestion, type ParkReason, type WorkspaceDelivery } from "../../../api";
import { makeHandover } from "../../../test/workspaceFixtures";
import type { Situation } from "../hooks/situation";
import SituationFacts from "./SituationFacts";

const NOW_ISO = "2026-09-19T12:00:00.000Z";
const NOW_MS = Date.parse(NOW_ISO);

function baseSituation(overrides: Partial<Situation> = {}): Situation {
  return {
    state: "running",
    headline: "Running",
    glyph: null,
    toneClass: "bg-status-active-tint/15 text-status-active-fg",
    body: "Working now.",
    primaryAction: null,
    activeSeconds: null,
    terminalOutcome: null,
    parkReason: null,
    blockers: [],
    unblock: null,
    decision: null,
    managerSummary: null,
    deployGate: null,
    stickyNotice: null,
    activityNotices: [],
    ...overrides,
  };
}

function renderFacts(overrides: Partial<Situation> = {}) {
  const onRefresh = vi.fn();
  render(
    <SituationFacts situation={baseSituation(overrides)} taskId="t1" onRefresh={onRefresh} />,
  );
  return { onRefresh };
}

describe("SituationFacts — no facts", () => {
  it("renders no fact blocks when nothing applies", () => {
    renderFacts();
    expect(screen.queryByTestId("stuck-banner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("park-reason-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("decision-card")).not.toBeInTheDocument();
    expect(screen.queryByTestId("unblock-notice")).not.toBeInTheDocument();
    expect(screen.queryByTestId("manager-summary-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pending-deploy-gate-waited")).not.toBeInTheDocument();
  });
});

describe("SituationFacts — sticky notice and activity", () => {
  it("renders the sticky notice as an alert", () => {
    renderFacts({
      stickyNotice: { id: "n-1", timestamp: NOW_ISO, text: "Escalated — loop", severity: "error" },
    });
    const banner = screen.getByTestId("stuck-banner");
    expect(banner).toHaveAttribute("role", "alert");
    expect(banner).toHaveTextContent("Escalated — loop");
  });

  it("renders queued activity notices", () => {
    renderFacts({
      activityNotices: [{ id: "n-1", timestamp: NOW_ISO, text: "Job started", severity: "info" }],
    });
    expect(screen.getByTestId("activity-feed")).toHaveTextContent("Job started");
  });
});

describe("SituationFacts — deploy gate", () => {
  it("shows waited and backstop lines only in the deploy_wait state", () => {
    renderFacts({
      state: "deploy_wait",
      deployGate: {
        path: "awaiting_live",
        deadline: 2_000,
        race_deadline: null,
        merged_at: 1_800,
        waited_s: 125,
      },
    });
    expect(screen.getByTestId("pending-deploy-gate-waited")).toHaveTextContent("2m");
    expect(screen.getByTestId("pending-deploy-gate-backstop")).toHaveTextContent(
      "escalate automatically",
    );
  });

  it("does not render the deploy gate detail outside the deploy_wait state", () => {
    renderFacts({
      state: "running",
      deployGate: {
        path: "awaiting_live",
        deadline: 2_000,
        race_deadline: null,
        merged_at: 1_800,
        waited_s: 125,
      },
    });
    expect(screen.queryByTestId("pending-deploy-gate-waited")).not.toBeInTheDocument();
  });
});

describe("SituationFacts — terminal block-class chip", () => {
  it("renders the human-readable block-class label", () => {
    renderFacts({
      terminalOutcome: { outcome: "escalated", reason: "stalled", block_class: "stale_review" },
    });
    expect(screen.getByTestId("merge-block-class")).toHaveTextContent("Stale Review");
  });

  it("omits the chip when block_class is absent", () => {
    renderFacts({ terminalOutcome: { outcome: "failed", reason: "oops" } });
    expect(screen.queryByTestId("merge-block-class")).not.toBeInTheDocument();
  });
});

describe("SituationFacts — park reason and blockers", () => {
  it("renders the park reason label and every blocker", () => {
    renderFacts({
      parkReason: "merge_block",
      blockers: [
        { kind: "failing_check", name: "Tests", detail: "check failing: Tests" },
        { kind: "review_decision", name: "CHANGES_REQUESTED", detail: "PR review decision" },
      ],
    });
    expect(screen.getByTestId("park-reason-label")).toHaveTextContent("Merge blocked");
    expect(screen.getAllByTestId("blocker-item")).toHaveLength(2);
  });

  it.each<[ParkReason, string]>([
    ["merge_block", "Merge blocked"],
    ["unarchived", "Restored from archive"],
  ])("labels the %s park reason as %s", (reason, label) => {
    renderFacts({ parkReason: reason });
    expect(screen.getByTestId("park-reason-label")).toHaveTextContent(label);
  });

  it("renders blockers with no park-reason label when reason is absent", () => {
    renderFacts({ blockers: [{ kind: "reason", name: "n", detail: "d" }] });
    expect(screen.queryByTestId("park-reason-label")).not.toBeInTheDocument();
    expect(screen.getByTestId("blocker-item")).toBeInTheDocument();
  });
});

function blockingQuestion(overrides: Partial<OpenQuestion> = {}): OpenQuestion {
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

describe("SituationFacts — decision", () => {
  it("renders the blocking question read-only inside the decision card", () => {
    renderFacts({
      decision: { question: blockingQuestion(), latest: makeHandover() },
    });
    const card = screen.getByTestId("decision-card");
    expect(within(card).getByText("Which storage backend?")).toBeInTheDocument();
    expect(within(card).getAllByTestId("open-question-option")).toHaveLength(2);
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
  });

  it("shows the pending-external card and restarts the check on click", async () => {
    const dispatchTask = vi.spyOn(api, "dispatchTask").mockResolvedValue({
      job_id: 1,
      invocation_id: "inv_002",
    });
    const question = blockingQuestion({
      answer: "duckdb",
      option_resolvers: { duckdb: "verification" },
      option_effects: { duckdb: "pending_external" },
    });
    const { onRefresh } = renderFacts({
      decision: { question, latest: makeHandover({ task_id: "t1", from_team: "research" }) },
    });
    expect(screen.getByTestId("decision-card-pending-external")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("decision-card-restart-check"));
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(dispatchTask).toHaveBeenCalledWith("t1", "t1", "verification", {});
    dispatchTask.mockRestore();
  });

  it("omits the pending-external card for a closes-effect answer", () => {
    const question = blockingQuestion({
      answer: "duckdb",
      option_effects: { duckdb: "closes" },
    });
    renderFacts({ decision: { question, latest: makeHandover() } });
    expect(screen.queryByTestId("decision-card-pending-external")).not.toBeInTheDocument();
  });
});

describe("SituationFacts — unblock", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_ISO));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the waiting state for a host-owned not_yet outcome", () => {
    renderFacts({
      unblock: {
        shape: "stale_review",
        attempts: 2,
        ceiling: 5,
        outcome: "not_yet",
        summary: "waiting",
        issue_url: null,
        at: NOW_MS / 1000,
        reason: "readiness pending",
      },
    });
    expect(screen.getByTestId("unblock-state-label")).toHaveTextContent(
      "Host is waiting and re-probing",
    );
    expect(screen.getByTestId("unblock-what-happened")).toHaveTextContent(
      "Waiting on: readiness pending",
    );
  });

  it("falls back to a generic waiting message when the record carries no reason", () => {
    renderFacts({
      unblock: {
        shape: "stale_review",
        attempts: 2,
        ceiling: 5,
        outcome: "not_yet",
        summary: "waiting",
        issue_url: null,
        at: NOW_MS / 1000,
        reason: null,
      },
    });
    expect(screen.getByTestId("unblock-what-happened")).toHaveTextContent(
      "Waiting and re-probing.",
    );
  });

  it("does not schedule a silence-boundary timer when the record is already past the deadline", () => {
    const staleAt = (NOW_MS - 91 * 60 * 1000) / 1000;
    renderFacts({
      unblock: {
        shape: "stale_review",
        attempts: 2,
        ceiling: 5,
        outcome: "not_yet",
        summary: "waiting",
        issue_url: null,
        at: staleAt,
        reason: null,
      },
    });
    expect(screen.getByTestId("unblock-state-label")).toHaveTextContent("Needs you");
    expect(screen.getByTestId("unblock-what-happened")).toHaveTextContent(
      "No action or report for over 90 minutes",
    );
  });

  it("transitions from waiting to needs_human once the silence threshold elapses", () => {
    renderFacts({
      unblock: {
        shape: "stale_review",
        attempts: 2,
        ceiling: 5,
        outcome: "not_yet",
        summary: "waiting",
        issue_url: null,
        at: NOW_MS / 1000,
        reason: "readiness pending",
      },
    });
    expect(screen.getByTestId("unblock-state-label")).toHaveTextContent(
      "Host is waiting and re-probing",
    );
    act(() => {
      vi.advanceTimersByTime(91 * 60 * 1000);
    });
    expect(screen.getByTestId("unblock-state-label")).toHaveTextContent("Needs you");
  });

  it("shows the action taken for a host-owned acted outcome", () => {
    renderFacts({
      unblock: {
        shape: "stale_review",
        attempts: 1,
        ceiling: 5,
        outcome: "acted",
        summary: "acted · fallback summary",
        issue_url: null,
        at: NOW_MS / 1000,
        action: "dispatch:pr_merge",
      },
    });
    expect(screen.getByTestId("unblock-state-label")).toHaveTextContent("Host acted");
    expect(screen.getByTestId("unblock-what-happened")).toHaveTextContent(
      "Action taken: dispatch:pr_merge",
    );
  });

  it("falls back to the record's own summary when acted with no recorded action", () => {
    renderFacts({
      unblock: {
        shape: "stale_review",
        attempts: 1,
        ceiling: 5,
        outcome: "acted",
        summary: "acted · fallback summary",
        issue_url: null,
        at: NOW_MS / 1000,
        action: null,
      },
    });
    expect(screen.getByTestId("unblock-what-happened")).toHaveTextContent(
      "acted · fallback summary",
    );
  });

  it("names the authority holder for a reported needs_human outcome", () => {
    renderFacts({
      unblock: {
        shape: "no_commit_escalation",
        attempts: 5,
        ceiling: 5,
        outcome: "needs_human",
        summary: "needs human",
        issue_url: "https://github.com/DejvMatikar/alan/issues/1701",
        at: NOW_MS / 1000,
        reason: "per-workspace ceiling reached",
        authority_holder: "human_operator",
      },
    });
    expect(screen.getByTestId("unblock-authority-holder")).toHaveTextContent(
      "you, the human operator",
    );
    expect(screen.getByTestId("unblock-issue-link")).toHaveAttribute(
      "href",
      "https://github.com/DejvMatikar/alan/issues/1701",
    );
  });

  it("falls back to the record's own summary when reported with no reason", () => {
    renderFacts({
      unblock: {
        shape: "no_commit_escalation",
        attempts: 5,
        ceiling: 5,
        outcome: "needs_human",
        summary: "needs human · fallback summary",
        issue_url: null,
        at: NOW_MS / 1000,
        reason: null,
      },
    });
    expect(screen.getByTestId("unblock-what-happened")).toHaveTextContent(
      "needs human · fallback summary",
    );
  });
});

describe("SituationFacts — manager summary", () => {
  it("shows the summary text, when it was written, and no cost figure", () => {
    renderFacts({
      managerSummary: { ok: true, text: "It shipped.", written_at: "2026-05-12T08:00:00Z", stale: false },
    });
    const panel = screen.getByTestId("manager-summary-panel");
    expect(within(panel).getByText("It shipped.")).toBeInTheDocument();
    expect(screen.getByTestId("manager-summary-written-at")).toBeInTheDocument();
    expect(within(panel).queryByTestId("run-summary-header")).not.toBeInTheDocument();
    expect(screen.queryByTestId("manager-summary-stale")).not.toBeInTheDocument();
  });

  it("flags a stale summary as not current", () => {
    renderFacts({
      managerSummary: { ok: true, text: "Old summary.", written_at: "2026-05-12T08:00:00Z", stale: true },
    });
    expect(screen.getByTestId("manager-summary-stale")).toBeInTheDocument();
  });

  it("shows the unavailable indicator when the summary failed", () => {
    renderFacts({ managerSummary: { ok: false, text: null, stale: false } });
    expect(screen.getByTestId("manager-summary-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("manager-summary-panel")).not.toBeInTheDocument();
  });
});

describe("SituationFacts — completed delivery", () => {
  const delivery: WorkspaceDelivery = {
    pr_number: 42,
    repo: "DejvMatikar/alan",
    pr_url: "https://github.com/DejvMatikar/alan/pull/42",
    pr_title: "feat: situation card",
    files_changed: ["alan/web/src/features/workspace/components/SituationCard.tsx"],
    commits: [{ sha: "abc12345", message: "feat: add situation card" }],
  };

  it("renders the PR title link, changed files, commits, and an Open PR link", async () => {
    const getDelivery = vi.spyOn(api, "getDelivery").mockResolvedValue(delivery);
    renderFacts({ state: "completed" });
    const container = await screen.findByTestId("situation-delivery");
    expect(within(container).getByTestId("situation-pr-title-link")).toHaveTextContent(
      "feat: situation card",
    );
    expect(within(container).getByTestId("situation-files-changed")).toHaveTextContent(
      "1 file changed",
    );
    expect(within(container).getByTestId("situation-commits")).toHaveTextContent(
      "feat: add situation card",
    );
    expect(within(container).getByTestId("situation-open-pr-link")).toHaveAttribute(
      "href",
      delivery.pr_url,
    );
    getDelivery.mockRestore();
  });

  it("pluralizes the changed-files count", async () => {
    const getDelivery = vi.spyOn(api, "getDelivery").mockResolvedValue({
      ...delivery,
      files_changed: ["a.ts", "b.ts"],
    });
    renderFacts({ state: "completed" });
    expect(await screen.findByTestId("situation-files-changed")).toHaveTextContent(
      "2 files changed",
    );
    getDelivery.mockRestore();
  });

  it("omits changed-files and commits facts when the delivery payload leaves them out", async () => {
    const getDelivery = vi.spyOn(api, "getDelivery").mockResolvedValue({
      pr_number: delivery.pr_number,
      repo: delivery.repo,
      pr_url: delivery.pr_url ?? null,
    });
    renderFacts({ state: "completed" });
    await screen.findByTestId("situation-delivery");
    expect(screen.queryByTestId("situation-files-changed")).not.toBeInTheDocument();
    expect(screen.queryByTestId("situation-commits")).not.toBeInTheDocument();
    getDelivery.mockRestore();
  });

  it("does not fetch delivery data outside the completed state", () => {
    const getDelivery = vi.spyOn(api, "getDelivery").mockResolvedValue(delivery);
    renderFacts({ state: "running" });
    expect(getDelivery).not.toHaveBeenCalled();
    getDelivery.mockRestore();
  });

  it("shows an error message when the delivery fetch fails", async () => {
    const getDelivery = vi.spyOn(api, "getDelivery").mockRejectedValue(new Error("boom"));
    renderFacts({ state: "completed" });
    expect(await screen.findByTestId("situation-delivery-error")).toHaveTextContent("boom");
    getDelivery.mockRestore();
  });
});
