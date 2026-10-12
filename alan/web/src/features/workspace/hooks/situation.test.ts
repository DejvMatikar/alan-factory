import { describe, expect, it } from "vitest";

import type { WorkspaceDetail } from "../../../api";
import { makeHandover, makeWorkspaceDetail } from "../../../test/workspaceFixtures";
import { situationOf } from "./situation";
import type { LiveWorkspaceState } from "./workflowLive";

const NO_LIVE: LiveWorkspaceState = {
  invocations: {},
  streamError: null,
  notices: [],
  stickyNotice: null,
};

function situation(overrides: Partial<WorkspaceDetail>) {
  return situationOf(makeWorkspaceDetail(overrides), NO_LIVE);
}

describe("situationOf — state precedence", () => {
  it("resolves paused regardless of other signals", () => {
    expect(
      situation({ status: "paused", pending_deploy_gate: { path: "awaiting_live", deadline: null, race_deadline: null, merged_at: null, waited_s: 0 } }).state,
    ).toBe("paused");
  });

  it("resolves completed", () => {
    expect(situation({ status: "completed" }).state).toBe("completed");
  });

  it("resolves awaiting_input to needs_you", () => {
    expect(situation({ status: "awaiting_input" }).state).toBe("needs_you");
  });

  it("resolves escalated, failed and partial to their own states", () => {
    expect(situation({ status: "escalated" }).state).toBe("escalated");
    expect(situation({ status: "failed" }).state).toBe("failed");
    expect(situation({ status: "partial" }).state).toBe("partial");
  });

  it("resolves active with a pending deploy gate to deploy_wait", () => {
    expect(
      situation({
        status: "active",
        pending_deploy_gate: {
          path: "awaiting_restart",
          deadline: null,
          race_deadline: 100,
          merged_at: null,
          waited_s: 30,
        },
      }).state,
    ).toBe("deploy_wait");
  });

  it("resolves plain active to running", () => {
    expect(situation({ status: "active" }).state).toBe("running");
  });

  it("resolves archived to archived", () => {
    expect(situation({ status: "archived" }).state).toBe("archived");
  });
});

describe("situationOf — headline", () => {
  it("gives escalated and partial a headline matching the status label", () => {
    expect(situation({ status: "escalated" }).headline).toBe("Escalated");
    expect(situation({ status: "partial" }).headline).toBe("Partial");
    expect(situation({ status: "failed" }).headline).toBe("Failed");
  });

  it("gives needs_you and deploy_wait distinct, non-status-derived headlines", () => {
    expect(situation({ status: "awaiting_input" }).headline).toBe("Needs you");
    expect(
      situation({
        status: "active",
        pending_deploy_gate: {
          path: "awaiting_live",
          deadline: null,
          race_deadline: null,
          merged_at: null,
          waited_s: 0,
        },
      }).headline,
    ).toBe("Waiting on deploy verification");
  });
});

describe("situationOf — glyph and tone", () => {
  it("gives escalated and failed the same glyphs the status badge uses", () => {
    expect(situation({ status: "escalated" }).glyph).toBe("↑");
    expect(situation({ status: "failed" }).glyph).toBe("✕");
  });

  it("gives partial, needs_you and running no glyph, since their headline text already carries the meaning", () => {
    expect(situation({ status: "partial" }).glyph).toBeNull();
    expect(situation({ status: "awaiting_input" }).glyph).toBeNull();
    expect(situation({ status: "active" }).glyph).toBeNull();
  });

  it("carries the same tone class the status badge uses for the underlying status", () => {
    expect(situation({ status: "escalated" }).toneClass).toContain("status-danger");
    expect(situation({ status: "active" }).toneClass).toContain("status-active");
  });
});

describe("situationOf — body", () => {
  it("reports elapsed time while running", () => {
    expect(situation({ status: "active", active_seconds: 125 }).body).toContain("Active for");
  });

  it("falls back to a working message when active_seconds is absent", () => {
    expect(situation({ status: "active", active_seconds: null }).body).toBe("Working now.");
  });

  it("explains why deploy verification is waiting, by path", () => {
    const gate = situation({
      status: "active",
      pending_deploy_gate: {
        path: "awaiting_live",
        deadline: null,
        race_deadline: null,
        merged_at: null,
        waited_s: 0,
      },
    });
    expect(gate.body).toContain("confirming the merge is live via local observation");
  });

  it("prefers the terminal outcome's own reason for escalated/failed/partial", () => {
    expect(
      situation({ status: "failed", terminal_outcome: { outcome: "failed", reason: "Build broke" } })
        .body,
    ).toBe("Build broke");
  });

  it("falls back to a generic sentence when no terminal outcome is recorded", () => {
    expect(situation({ status: "failed", terminal_outcome: null }).body).toBe("This run failed.");
  });
});

describe("situationOf — primary action", () => {
  it("offers Stop only while running with a live job", () => {
    expect(situation({ status: "active", has_running_job: true }).primaryAction).toBe("stop");
    expect(situation({ status: "active", has_running_job: false }).primaryAction).toBeNull();
  });

  it("offers Resume while paused", () => {
    expect(situation({ status: "paused" }).primaryAction).toBe("resume");
  });

  it("offers Re-queue only when needs_you has a resumable failed job", () => {
    expect(
      situation({ status: "awaiting_input", has_resumable_failed_job: true }).primaryAction,
    ).toBe("requeue");
    expect(
      situation({ status: "awaiting_input", has_resumable_failed_job: false }).primaryAction,
    ).toBeNull();
  });

  it("never offers a primary action for escalated, failed, partial, completed, or archived", () => {
    for (const status of ["escalated", "failed", "partial", "completed", "archived"] as const) {
      expect(situation({ status }).primaryAction).toBeNull();
    }
  });
});

describe("situationOf — decision", () => {
  it("surfaces the blocking question for needs_you and escalated", () => {
    const handover = makeHandover({
      open_questions: [
        {
          id: "q1",
          question: "Which backend?",
          proposed_answer_space: ["a", "b"],
          resolver_role: "human",
          blocking: true,
          prior_attempts: [],
          question_class: null,
          recommended: "a",
        },
      ],
    });
    expect(situation({ status: "awaiting_input", latest_handover: handover }).decision).not.toBeNull();
    expect(situation({ status: "escalated", latest_handover: handover }).decision).not.toBeNull();
  });

  it("is absent for a non-blocking question or a non-decision state", () => {
    const nonBlocking = makeHandover({
      open_questions: [
        {
          id: "q1",
          question: "Which backend?",
          proposed_answer_space: [],
          resolver_role: "human",
          blocking: false,
          prior_attempts: [],
          question_class: null,
          recommended: null,
        },
      ],
    });
    expect(
      situation({ status: "awaiting_input", latest_handover: nonBlocking }).decision,
    ).toBeNull();
    expect(situation({ status: "active", latest_handover: nonBlocking }).decision).toBeNull();
  });
});

describe("situationOf — park reason and blockers", () => {
  it("carries park_reason only for needs_you and escalated", () => {
    expect(situation({ status: "awaiting_input", park_reason: "crash" }).parkReason).toBe("crash");
    expect(situation({ status: "escalated", park_reason: "crash" }).parkReason).toBe("crash");
    expect(situation({ status: "failed", park_reason: "crash" }).parkReason).toBeNull();
    expect(situation({ status: "active", park_reason: "crash" }).parkReason).toBeNull();
  });

  it("carries blockers for needs_you, escalated, failed and partial but not running", () => {
    const blockers = [{ kind: "reason" as const, name: "n", detail: "d" }];
    for (const status of ["awaiting_input", "escalated", "failed", "partial"] as const) {
      expect(situation({ status, blockers }).blockers).toEqual(blockers);
    }
    expect(situation({ status: "active", blockers }).blockers).toEqual([]);
  });
});

describe("situationOf — passthrough facts", () => {
  it("carries manager_summary and unblock regardless of state", () => {
    const summary = { ok: true, text: "done", stale: false };
    const unblock = {
      shape: "s",
      attempts: 1,
      ceiling: 5,
      outcome: "not_yet",
      summary: "sum",
      issue_url: null,
      at: 0,
    };
    expect(situation({ status: "active", manager_summary: summary }).managerSummary).toEqual(
      summary,
    );
    expect(situation({ status: "completed", unblock }).unblock).toEqual(unblock);
  });

  it("threads live notices and the sticky notice through unchanged", () => {
    const live: LiveWorkspaceState = {
      invocations: {},
      streamError: null,
      notices: [{ id: "n-1", timestamp: "2026-01-01T00:00:00Z", text: "hi", severity: "info" }],
      stickyNotice: { id: "n-1", timestamp: "2026-01-01T00:00:00Z", text: "hi", severity: "info" },
    };
    const result = situationOf(makeWorkspaceDetail({ status: "active" }), live);
    expect(result.activityNotices).toBe(live.notices);
    expect(result.stickyNotice).toBe(live.stickyNotice);
  });
});
