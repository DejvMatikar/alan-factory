import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  formatActiveDuration,
  formatAutoArchiveCountdown,
  formatCostUsd,
  formatDate,
  formatRelativeTime,
  formatWarmupDuration,
} from "./format";

describe("formatDate", () => {
  it("returns a locale string for a valid ISO timestamp", () => {
    const result = formatDate("2026-05-12T08:00:00Z");
    expect(typeof result).toBe("string");
    expect(result.length).toBeGreaterThan(0);
  });
});

describe("formatWarmupDuration", () => {
  it("formats sub-minute durations with one decimal and a trailing s", () => {
    expect(formatWarmupDuration(12.44)).toBe("12.4s");
  });

  it("formats zero seconds", () => {
    expect(formatWarmupDuration(0)).toBe("0.0s");
  });

  it("formats a duration just under a minute", () => {
    expect(formatWarmupDuration(59.9)).toBe("59.9s");
  });

  it("formats minute-scale durations as Nm SSs", () => {
    expect(formatWarmupDuration(63.4)).toBe("1m 03s");
  });

  it("formats exactly one minute", () => {
    expect(formatWarmupDuration(60)).toBe("1m 00s");
  });

  it("formats multi-minute durations", () => {
    expect(formatWarmupDuration(725)).toBe("12m 05s");
  });

  it("carries a rounded-up remainder into the next minute instead of showing 60s", () => {
    expect(formatWarmupDuration(119.6)).toBe("2m 00s");
  });

  it("rounds up to the next minute rather than showing 60.0s", () => {
    expect(formatWarmupDuration(59.95)).toBe("1m 00s");
  });
});

describe("formatActiveDuration", () => {
  it("returns an explicit empty-state dash for null", () => {
    expect(formatActiveDuration(null)).toBe("—");
  });

  it("returns an explicit empty-state dash for undefined", () => {
    expect(formatActiveDuration(undefined)).toBe("—");
  });

  it("shows 0m for a true zero duration", () => {
    expect(formatActiveDuration(0)).toBe("0m");
  });

  it("shows <1m for a nonzero duration that rounds down to zero minutes", () => {
    expect(formatActiveDuration(20)).toBe("<1m");
  });

  it("formats a sub-hour duration as minutes only", () => {
    expect(formatActiveDuration(25 * 60)).toBe("25m");
  });

  it("formats an hour-scale duration as Nh Mm", () => {
    expect(formatActiveDuration(2 * 3600 + 10 * 60)).toBe("2h 10m");
  });

  it("formats an exact-hour duration with a zero minutes remainder", () => {
    expect(formatActiveDuration(3 * 3600)).toBe("3h 0m");
  });

  it("rounds to the nearest minute rather than truncating", () => {
    expect(formatActiveDuration(90)).toBe("2m");
  });
});

describe("formatCostUsd", () => {
  it("formats a whole-dollar amount with two decimals", () => {
    expect(formatCostUsd(1.5)).toBe("$1.50");
  });

  it("formats a sub-cent amount by rounding to two decimals", () => {
    expect(formatCostUsd(0.0015)).toBe("$0.00");
  });

  it("formats zero", () => {
    expect(formatCostUsd(0)).toBe("$0.00");
  });

  it("rounds a three-decimal amount to the nearer cent", () => {
    expect(formatCostUsd(2.756)).toBe("$2.76");
  });
});

describe("formatRelativeTime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-02T12:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns Unknown for an invalid ISO string", () => {
    expect(formatRelativeTime("not-a-date")).toBe("Unknown");
  });

  it("returns Just now for a future timestamp", () => {
    expect(formatRelativeTime("2026-06-02T13:00:00Z")).toBe("Just now");
  });

  it("returns Just now when less than 60 seconds ago", () => {
    expect(formatRelativeTime("2026-06-02T11:59:30Z")).toBe("Just now");
  });

  it("returns 1 minute ago for exactly 60 seconds ago", () => {
    expect(formatRelativeTime("2026-06-02T11:59:00Z")).toBe("1 minute ago");
  });

  it("returns N minutes ago for 2-59 minutes ago", () => {
    expect(formatRelativeTime("2026-06-02T11:55:00Z")).toBe("5 minutes ago");
  });

  it("returns 1 hour ago for exactly 60 minutes ago", () => {
    expect(formatRelativeTime("2026-06-02T11:00:00Z")).toBe("1 hour ago");
  });

  it("returns N hours ago for 2-23 hours ago", () => {
    expect(formatRelativeTime("2026-06-02T09:00:00Z")).toBe("3 hours ago");
  });

  it("returns 1 day ago for exactly 24 hours ago", () => {
    expect(formatRelativeTime("2026-06-01T12:00:00Z")).toBe("1 day ago");
  });

  it("returns N days ago for multiple days", () => {
    expect(formatRelativeTime("2026-05-30T12:00:00Z")).toBe("3 days ago");
  });
});

describe("formatAutoArchiveCountdown", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-02T12:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns null when autoArchiveDays is null", () => {
    expect(formatAutoArchiveCountdown("2026-06-02T12:00:00Z", null)).toBeNull();
  });

  it("returns null when autoArchiveDays is undefined", () => {
    expect(formatAutoArchiveCountdown("2026-06-02T12:00:00Z", undefined)).toBeNull();
  });

  it("returns null when updatedAt is unparseable", () => {
    expect(formatAutoArchiveCountdown("not-a-date", 14)).toBeNull();
  });

  it("returns 'auto-archives in N days' for multiple full days remaining", () => {
    expect(formatAutoArchiveCountdown("2026-05-21T12:00:00Z", 14)).toBe(
      "auto-archives in 2 days",
    );
  });

  it("uses singular 'day' at exactly one day remaining", () => {
    expect(formatAutoArchiveCountdown("2026-06-02T12:00:00Z", 1)).toBe(
      "auto-archives in 1 day",
    );
  });

  it("returns 'auto-archives soon' for just under one day remaining", () => {
    expect(formatAutoArchiveCountdown("2026-06-02T11:59:59.999Z", 1)).toBe(
      "auto-archives soon",
    );
  });

  it("returns 'auto-archives soon' once the deadline has already passed", () => {
    expect(formatAutoArchiveCountdown("2026-05-01T12:00:00Z", 14)).toBe(
      "auto-archives soon",
    );
  });
});
