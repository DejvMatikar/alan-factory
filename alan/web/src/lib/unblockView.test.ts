import { describe, expect, it } from "vitest";

import type { UnblockRecord } from "../api";
import {
  authorityHolderLabel,
  isHostOwnedOutcome,
  isHumanOwnedPark,
  isSilentTooLong,
  SILENCE_THRESHOLD_MINUTES,
  SILENCE_THRESHOLD_MS,
  unblockView,
} from "./unblockView";

const NOW = 1_800_000_000_000;

function record(overrides: Partial<UnblockRecord> = {}): UnblockRecord {
  return {
    shape: "stale_review",
    attempts: 1,
    ceiling: 5,
    outcome: "not_yet",
    summary: "waiting · stale_review · re-probing · 2026-09-19T12:00Z",
    issue_url: null,
    at: NOW / 1000,
    action: null,
    reason: null,
    authority_holder: null,
    ...overrides,
  };
}

describe("SILENCE_THRESHOLD_MS", () => {
  it("is 90 minutes, 4.5x the host's 1200s max re-probe interval", () => {
    expect(SILENCE_THRESHOLD_MINUTES).toBe(90);
    expect(SILENCE_THRESHOLD_MS).toBe(90 * 60 * 1000);
    expect(SILENCE_THRESHOLD_MS).toBe(4.5 * 1200 * 1000);
  });
});

describe("isHostOwnedOutcome — fail-safe allowlist", () => {
  it("allows only acted and not_yet", () => {
    expect(isHostOwnedOutcome("acted")).toBe(true);
    expect(isHostOwnedOutcome("not_yet")).toBe(true);
  });

  it("rejects needs_human, any failed: variant, and any unrecognised string", () => {
    expect(isHostOwnedOutcome("needs_human")).toBe(false);
    expect(isHostOwnedOutcome("failed: boom")).toBe(false);
    expect(isHostOwnedOutcome("")).toBe(false);
    expect(isHostOwnedOutcome("mystery_future_outcome")).toBe(false);
  });
});

describe("isSilentTooLong", () => {
  it("is false exactly at the threshold", () => {
    const rec = record({ at: NOW / 1000 });
    expect(isSilentTooLong(rec, NOW + SILENCE_THRESHOLD_MS)).toBe(false);
  });

  it("is true one millisecond past the threshold", () => {
    const rec = record({ at: NOW / 1000 });
    expect(isSilentTooLong(rec, NOW + SILENCE_THRESHOLD_MS + 1)).toBe(true);
  });
});

describe("unblockView — host-owned outcomes within the silence window", () => {
  it("acted, recent: host-owned, displayState acted, not silent", () => {
    const view = unblockView(record({ outcome: "acted" }), NOW);
    expect(view).toEqual({ owner: "host", displayState: "acted", silent: false });
  });

  it("not_yet, recent: host-owned, displayState waiting, not silent", () => {
    const view = unblockView(record({ outcome: "not_yet" }), NOW);
    expect(view).toEqual({ owner: "host", displayState: "waiting", silent: false });
  });
});

describe("unblockView — human-owned outcomes", () => {
  it("needs_human: human-owned, not silent (a genuine report)", () => {
    const view = unblockView(record({ outcome: "needs_human" }), NOW);
    expect(view).toEqual({ owner: "human", displayState: "needs_human", silent: false });
  });

  it("failed: <exception>: human-owned, not silent", () => {
    const view = unblockView(record({ outcome: "failed: boom" }), NOW);
    expect(view).toEqual({ owner: "human", displayState: "needs_human", silent: false });
  });

  it("an unrecognised outcome string defaults to human-owned, not silent", () => {
    const view = unblockView(record({ outcome: "some_future_outcome" }), NOW);
    expect(view).toEqual({ owner: "human", displayState: "needs_human", silent: false });
  });
});

describe("unblockView — prolonged silence escalates a host-owned park to human", () => {
  it("acted past the silence threshold becomes human-owned and silent", () => {
    const rec = record({ outcome: "acted", at: NOW / 1000 });
    const view = unblockView(rec, NOW + SILENCE_THRESHOLD_MS + 1);
    expect(view).toEqual({ owner: "human", displayState: "needs_human", silent: true });
  });

  it("not_yet past the silence threshold becomes human-owned and silent", () => {
    const rec = record({ outcome: "not_yet", at: NOW / 1000 });
    const view = unblockView(rec, NOW + SILENCE_THRESHOLD_MS + 1);
    expect(view).toEqual({ owner: "human", displayState: "needs_human", silent: true });
  });
});

describe("isHumanOwnedPark", () => {
  it("is true for a missing record (no ledger activity at all)", () => {
    expect(isHumanOwnedPark(null, NOW)).toBe(true);
    expect(isHumanOwnedPark(undefined, NOW)).toBe(true);
  });

  it("is false for a fresh host-owned record", () => {
    expect(isHumanOwnedPark(record({ outcome: "acted" }), NOW)).toBe(false);
  });

  it("is true for a needs_human record", () => {
    expect(isHumanOwnedPark(record({ outcome: "needs_human" }), NOW)).toBe(true);
  });

  it("is true for a host-owned record gone silent too long", () => {
    const rec = record({ outcome: "not_yet", at: NOW / 1000 });
    expect(isHumanOwnedPark(rec, NOW + SILENCE_THRESHOLD_MS + 1)).toBe(true);
  });
});

describe("authorityHolderLabel", () => {
  it("names the known authority holders in plain language", () => {
    expect(authorityHolderLabel("human_operator")).toBe("you, the human operator");
    expect(authorityHolderLabel("fleet_grant_missing")).toContain("fleet configuration");
    expect(authorityHolderLabel("nobody")).toContain("no one yet");
  });

  it("falls back to the raw string for an unrecognised holder", () => {
    expect(authorityHolderLabel("some_new_holder")).toBe("some_new_holder");
  });

  it("falls back to 'a human' when absent", () => {
    expect(authorityHolderLabel(null)).toBe("a human");
    expect(authorityHolderLabel(undefined)).toBe("a human");
  });
});
