import { describe, expect, it } from "vitest";

import { NOTICES_CAP, noticeFromEvent } from "./workspaceNotices";

describe("noticeFromEvent", () => {
  it("returns null for router_enqueue (silent)", () => {
    expect(noticeFromEvent("router_enqueue", '{"task_id":"t1"}')).toBeNull();
  });

  it("returns null for router_done (silent)", () => {
    expect(noticeFromEvent("router_done", '{"task_id":"t1"}')).toBeNull();
  });

  it("returns null for empty data string", () => {
    expect(noticeFromEvent("job_failed", "")).toBeNull();
  });

  it("returns null for malformed JSON data", () => {
    expect(noticeFromEvent("job_failed", "not json")).toBeNull();
  });

  it("returns null for array data", () => {
    expect(noticeFromEvent("job_failed", "[]")).toBeNull();
  });

  it("job_failed: error severity with correct text", () => {
    const n = noticeFromEvent("job_failed", '{"job_id":"1"}');
    expect(n?.severity).toBe("error");
    expect(n?.text).toBe("Job failed");
  });

  it("job_retry_scheduled: warn severity", () => {
    const n = noticeFromEvent("job_retry_scheduled", '{"attempt":"1"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toBe("Job retrying after failure");
  });

  it("workspace_paused: warn severity", () => {
    const n = noticeFromEvent("workspace_paused", '{"task_id":"t1"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toBe("Workspace paused");
  });

  it("workspace_resumed: info severity", () => {
    const n = noticeFromEvent("workspace_resumed", '{"task_id":"t1"}');
    expect(n?.severity).toBe("info");
    expect(n?.text).toBe("Workspace resumed");
  });

  it("workspace_interrupted: warn severity", () => {
    const n = noticeFromEvent("workspace_interrupted", '{"task_id":"t1"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toBe("Workspace interrupted");
  });

  it("workspace_resuming: info severity", () => {
    const n = noticeFromEvent("workspace_resuming", '{"task_id":"t1"}');
    expect(n?.severity).toBe("info");
    expect(n?.text).toBe("Workspace resuming");
  });

  it("workflow_created: info severity", () => {
    const n = noticeFromEvent("workflow_created", '{"task_id":"t1"}');
    expect(n?.severity).toBe("info");
    expect(n?.text).toBe("Workflow started");
  });

  it("workspace_deleted: error severity", () => {
    const n = noticeFromEvent("workspace_deleted", '{"task_id":"t1"}');
    expect(n?.severity).toBe("error");
    expect(n?.text).toBe("Workspace deleted");
  });

  it("workspace_archived: info severity", () => {
    const n = noticeFromEvent("workspace_archived", '{"task_id":"t1"}');
    expect(n?.severity).toBe("info");
  });

  it("workspace_unarchived: info severity", () => {
    const n = noticeFromEvent("workspace_unarchived", '{"task_id":"t1"}');
    expect(n?.severity).toBe("info");
  });

  it("job_started: info severity", () => {
    const n = noticeFromEvent("job_started", '{"job_id":"1"}');
    expect(n?.severity).toBe("info");
    expect(n?.text).toBe("Job started");
  });

  it("job_completed: info severity", () => {
    const n = noticeFromEvent("job_completed", '{"job_id":"1"}');
    expect(n?.severity).toBe("info");
    expect(n?.text).toBe("Job completed");
  });

  it("job_paused: warn severity", () => {
    const n = noticeFromEvent("job_paused", '{"job_id":"1"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toBe("Job paused");
  });

  it("router_await_human: interpolates reason into text", () => {
    const n = noticeFromEvent(
      "router_await_human",
      '{"task_id":"t1","reason":"blocking question q1"}',
    );
    expect(n?.severity).toBe("warn");
    expect(n?.text).toBe("Waiting for your input — blocking question q1");
  });

  it("router_await_human: falls back to 'input required' when reason absent", () => {
    const n = noticeFromEvent("router_await_human", '{"task_id":"t1"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toBe("Waiting for your input — input required");
  });

  it("router_await_human: appends next_team when present", () => {
    const n = noticeFromEvent(
      "router_await_human",
      '{"task_id":"t1","reason":"needs answer","next_team":"human_review"}',
    );
    expect(n?.text).toBe("Waiting for your input — needs answer → human_review");
  });

  it("router_escalate: interpolates reason into text", () => {
    const n = noticeFromEvent(
      "router_escalate",
      '{"task_id":"t1","reason":"loop detected"}',
    );
    expect(n?.severity).toBe("error");
    expect(n?.text).toBe("Escalated — loop detected");
  });

  it("router_escalate: falls back when reason absent", () => {
    const n = noticeFromEvent(
      "router_escalate",
      '{"task_id":"t1","from_team":"impl","kind":"escalate"}',
    );
    expect(n?.severity).toBe("error");
    expect(n?.text).toBe("Escalated — manual intervention required");
  });

  it("router_escalate: appends next_team when present", () => {
    const n = noticeFromEvent(
      "router_escalate",
      '{"task_id":"t1","reason":"loop","next_team":"human_review"}',
    );
    expect(n?.text).toBe("Escalated — loop → human_review");
  });

  it("router_await_human: appends the human-readable block-class label", () => {
    const n = noticeFromEvent(
      "router_await_human",
      '{"task_id":"t1","reason":"stale review","block_class":"stale_review"}',
    );
    expect(n?.text).toBe("Waiting for your input — stale review [Stale Review]");
  });

  it("router_escalate: appends the human-readable block-class label", () => {
    const n = noticeFromEvent(
      "router_escalate",
      '{"task_id":"t1","reason":"gave up","block_class":"sonar_hotspot"}',
    );
    expect(n?.text).toBe("Escalated — gave up [SonarCloud Hotspot]");
  });

  it("router_await_human: omits the block-class label when empty", () => {
    const n = noticeFromEvent(
      "router_await_human",
      '{"task_id":"t1","reason":"needs answer","block_class":""}',
    );
    expect(n?.text).toBe("Waiting for your input — needs answer");
  });

  it("injection_overflow: interpolates dropped_count into text", () => {
    const n = noticeFromEvent("injection_overflow", '{"dropped_count":"2"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toBe("2 injected task(s) dropped");
  });

  it("injection_overflow: accepts dropped_count as a number", () => {
    const n = noticeFromEvent("injection_overflow", '{"dropped_count":3}');
    expect(n?.text).toBe("3 injected task(s) dropped");
  });

  it("injection_overflow: defaults count to 0 when dropped_count absent", () => {
    const n = noticeFromEvent("injection_overflow", '{"task_id":"t1"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toBe("0 injected task(s) dropped");
  });

  it("injection_overflow: defaults count to 0 when dropped_count is non-numeric string", () => {
    const n = noticeFromEvent("injection_overflow", '{"dropped_count":"abc"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toBe("0 injected task(s) dropped");
  });

  it("manager_summary_ready: falls back to humanized text", () => {
    const n = noticeFromEvent("manager_summary_ready", '{"task_id":"t1"}');
    expect(n?.severity).toBe("info");
    expect(n?.text).toBe("Manager summary ready");
  });

  it("dag_stall: interpolates reason into text", () => {
    const n = noticeFromEvent(
      "dag_stall",
      '{"reason":"no more runnable tasks","workspace_id":"t1"}',
    );
    expect(n?.severity).toBe("warn");
    expect(n?.text).toBe("Workflow stalled: no more runnable tasks");
  });

  it("dag_stall: falls back to 'unknown reason' when reason absent", () => {
    const n = noticeFromEvent("dag_stall", '{"workspace_id":"t1"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toBe("Workflow stalled: unknown reason");
  });

  it("frozen_chain_park: interpolates reason into text at warn severity", () => {
    const n = noticeFromEvent(
      "frozen_chain_park",
      '{"reason":"frozen chain: ancestor task \'a1\' exhausted the revisit cap with 2 descendant(s) permanently blocked","workspace_id":"t1","ancestor_id":"a1"}',
    );
    expect(n?.severity).toBe("warn");
    expect(n?.text).toBe(
      "Frozen dependency chain: frozen chain: ancestor task 'a1' exhausted the revisit cap with 2 descendant(s) permanently blocked",
    );
  });

  it("frozen_chain_park: falls back to 'unknown reason' when reason absent", () => {
    const n = noticeFromEvent("frozen_chain_park", '{"workspace_id":"t1"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toBe("Frozen dependency chain: unknown reason");
  });

  it("cannot_verify: warn severity with tooling-failure text and PR number", () => {
    const n = noticeFromEvent("cannot_verify", '{"task_id":"t1","pr_number":"42"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toContain("tooling failure");
    expect(n?.text).toContain("PR #42");
  });

  it("cannot_verify: interpolates numeric pr_number", () => {
    const n = noticeFromEvent("cannot_verify", '{"task_id":"t1","pr_number":42}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toContain("PR #42");
  });

  it("cannot_verify: omits PR number when pr_number absent", () => {
    const n = noticeFromEvent("cannot_verify", '{"task_id":"t1"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toContain("tooling failure");
    expect(n?.text).not.toContain("PR #");
  });

  it("cannot_verify: omits PR number when pr_number is non-numeric string", () => {
    const n = noticeFromEvent("cannot_verify", '{"task_id":"t1","pr_number":"bad"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).not.toContain("PR #");
  });

  it("review_post_failed: error severity naming the PR and the token", () => {
    const n = noticeFromEvent(
      "review_post_failed",
      '{"task_id":"t1","pr_number":"42"}',
    );
    expect(n?.severity).toBe("error");
    expect(n?.text).toContain("PR #42");
    expect(n?.text).toContain("ALAN_VERIFIER_GH_TOKEN");
  });

  it("review_post_failed: interpolates numeric pr_number", () => {
    const n = noticeFromEvent(
      "review_post_failed",
      '{"task_id":"t1","pr_number":42}',
    );
    expect(n?.severity).toBe("error");
    expect(n?.text).toContain("PR #42");
  });

  it("review_post_failed: omits PR number when pr_number absent", () => {
    const n = noticeFromEvent("review_post_failed", '{"task_id":"t1"}');
    expect(n?.severity).toBe("error");
    expect(n?.text).toContain("ALAN_VERIFIER_GH_TOKEN");
    expect(n?.text).not.toContain("PR #");
  });

  it("review_post_failed: omits PR number when pr_number is non-numeric string", () => {
    const n = noticeFromEvent(
      "review_post_failed",
      '{"task_id":"t1","pr_number":"bad"}',
    );
    expect(n?.severity).toBe("error");
    expect(n?.text).not.toContain("PR #");
  });

  it("pr_merge_rederived: info severity with restart text", () => {
    const n = noticeFromEvent("pr_merge_rederived", '{"task_id":"t1"}');
    expect(n?.severity).toBe("info");
    expect(n?.text).toBe("PR merge restarted");
  });

  it("deploy_verify_rederived: info severity with restart text", () => {
    const n = noticeFromEvent("deploy_verify_rederived", '{"task_id":"t1"}');
    expect(n?.severity).toBe("info");
    expect(n?.text).toBe("Deploy verify restarted");
  });

  it("verification_rederived: info severity with restart text", () => {
    const n = noticeFromEvent("verification_rederived", '{"task_id":"t1"}');
    expect(n?.severity).toBe("info");
    expect(n?.text).toBe("Verification restarted");
  });

  it("slot_work_preserved: info severity with commit count", () => {
    const n = noticeFromEvent("slot_work_preserved", '{"n_commits":"4"}');
    expect(n?.severity).toBe("info");
    expect(n?.text).toBe("4 commit(s) preserved from interrupted run");
  });

  it("slot_work_preserved: uses '?' when n_commits absent", () => {
    const n = noticeFromEvent("slot_work_preserved", '{"task_id":"t1"}');
    expect(n?.severity).toBe("info");
    expect(n?.text).toContain("?");
  });

  it("slot_work_rescued: warn severity with rescue ref", () => {
    const n = noticeFromEvent(
      "slot_work_rescued",
      '{"rescue_ref":"rescue/task1@inv_001"}',
    );
    expect(n?.severity).toBe("warn");
    expect(n?.text).toContain("rescue/task1@inv_001");
    expect(n?.text).toContain("restarting clean");
  });

  it("slot_work_rescued: falls back to 'rescue ref' when rescue_ref absent", () => {
    const n = noticeFromEvent("slot_work_rescued", '{"task_id":"t1"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toContain("rescue ref");
  });

  it("slot_resume_waiting: warn severity with slot index", () => {
    const n = noticeFromEvent("slot_resume_waiting", '{"slot_index":"3"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toContain("3");
    expect(n?.text).toContain("waiting for its pinned slot");
  });

  it("slot_resume_waiting: uses '?' when slot_index absent", () => {
    const n = noticeFromEvent("slot_resume_waiting", '{"task_id":"t1"}');
    expect(n?.severity).toBe("warn");
    expect(n?.text).toContain("?");
  });

  it("unknown event name: humanized info fallback", () => {
    const n = noticeFromEvent("some_custom_event", '{"x":"y"}');
    expect(n?.severity).toBe("info");
    expect(n?.text).toBe("Some custom event");
  });

  it("single-word unknown event: capitalizes first letter", () => {
    const n = noticeFromEvent("foobar", '{"x":"y"}');
    expect(n?.severity).toBe("info");
    expect(n?.text).toBe("Foobar");
  });
});

describe("NOTICES_CAP", () => {
  it("is a positive integer", () => {
    expect(NOTICES_CAP).toBeGreaterThan(0);
    expect(Number.isInteger(NOTICES_CAP)).toBe(true);
  });
});
