import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { createElement } from "react";

import { HealthProvider, useHealthContext } from "./HealthProvider";
import { server } from "../test/server";

const HEALTHY = {
  ok: true as const,
  queue_paused: false,
  paused_until: null,
  health_state: null,
};

const PAUSED = {
  ok: true as const,
  queue_paused: true,
  paused_until: Date.now() / 1000 + 3600,
  health_state: null,
};

const DEGRADED_HEALTH_STATE = {
  verdict: "degraded" as const,
  blocked_count: 2,
  in_flight_count: 0,
  findings: [
    {
      finding_id: "task-abc",
      kind: "build_task",
      severity: "critical" as const,
      description: "Build task task-abc has failing CI",
      workspace_id: "task-abc",
      pr_number: 42,
      blocker_flags: ["pr_checks_failing"],
      action: "dispatch_remediation" as const,
    },
    {
      finding_id: "task-def",
      kind: "build_task",
      severity: "medium" as const,
      description: "Build task task-def is awaiting human input",
      workspace_id: "task-def",
      pr_number: null,
      blocker_flags: ["ws_stuck"],
      action: "deep_link" as const,
    },
  ],
  computed_at: new Date().toISOString(),
};

function wrapper({ children }: Readonly<{ children: ReactNode }>) {
  return createElement(HealthProvider, null, children);
}

async function flushAsync(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("HealthProvider — context values", () => {
  it("provides default not-paused state initially", () => {
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    expect(result.current.queuePaused).toBe(false);
    expect(result.current.pausedUntil).toBeNull();
    expect(result.current.spendCapStop).toBeNull();
    expect(result.current.dispatchReadiness).toEqual([]);
    expect(result.current.healthState).toBeNull();
    expect(result.current.blockedCount).toBe(0);
    expect(result.current.needsUserCount).toBe(0);
    expect(result.current.lastComputedAt).toBeNull();
    expect(result.current.sweeper).toBeNull();
    expect(result.current.finisherRate).toBeNull();
    expect(result.current.qualityMetrics).toBeNull();
    expect(typeof result.current.refetch).toBe("function");
  });

  it("reflects health response after fetch", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(HEALTHY)));
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.queuePaused).toBe(false);
    });
    expect(result.current.healthState).toBeNull();
  });

  it("reflects paused state", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(PAUSED)));
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.queuePaused).toBe(true);
    });
    expect(result.current.pausedUntil).not.toBeNull();
  });

  it("computes blockedCount from health_state", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...HEALTHY, health_state: DEGRADED_HEALTH_STATE }),
      ),
    );
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.blockedCount).toBe(2);
    });
  });

  it("computes needsUserCount as blocked_count minus in_flight_count", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...HEALTHY, health_state: DEGRADED_HEALTH_STATE }),
      ),
    );
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.needsUserCount).toBe(2);
    });
  });

  it("tolerates network error — state unchanged, no crash", async () => {
    server.use(http.get("/api/health", () => HttpResponse.error()));
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.queuePaused).toBe(false);
    });
  });

  it("populates lastComputedAt from last_computed_at response", async () => {
    const ts = "2026-06-02T10:00:00.000Z";
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...HEALTHY, last_computed_at: ts }),
      ),
    );
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.lastComputedAt).toBe(ts);
    });
  });

  it("lastComputedAt defaults to null when absent from response", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(HEALTHY)));
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.queuePaused).toBe(false);
    });
    expect(result.current.lastComputedAt).toBeNull();
  });

  it("populates sweeper from sweeper response", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({
          ...HEALTHY,
          sweeper: {
            timestamp_utc: "2026-07-11T09:00:00+00:00",
            surface_count: 4,
            blocked_count: 1,
            next_eligible_utc: "2026-07-11T09:30:00+00:00",
            recent_actions: [
              { action: "sweep_complete", timestamp_utc: "2026-07-11T09:00:00+00:00" },
            ],
          },
        }),
      ),
    );
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.sweeper).not.toBeNull();
    });
    expect(result.current.sweeper?.timestamp_utc).toBe("2026-07-11T09:00:00+00:00");
    expect(result.current.sweeper?.surface_count).toBe(4);
    expect(result.current.sweeper?.blocked_count).toBe(1);
    expect(result.current.sweeper?.next_eligible_utc).toBe("2026-07-11T09:30:00+00:00");
    expect(result.current.sweeper?.recent_actions).toEqual([
      { action: "sweep_complete", timestamp_utc: "2026-07-11T09:00:00+00:00" },
    ]);
  });

  it("populates finisherRate from finisher_rate response", async () => {
    const finisherRate = {
      rate: 0.75,
      counts: {
        dispatched: 20,
        not_crashed: 18,
        not_needed_human: 16,
        converged: 14,
        merged_pr: 16,
        dod_evidenced: 15,
        autonomous: 12,
      },
      leak_reasons: { crash_reasons: {}, convergence_kinds: {} },
      low_data: false,
      window_days: 30,
      sample_size: 20,
      floor_alert: false,
      autonomous_rate: 0.6,
    };
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...HEALTHY, finisher_rate: finisherRate })),
    );
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.finisherRate).not.toBeNull();
    });
    expect(result.current.finisherRate).toEqual(finisherRate);
  });

  it("finisherRate defaults to null when absent from response", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(HEALTHY)));
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.queuePaused).toBe(false);
    });
    expect(result.current.finisherRate).toBeNull();
  });

  it("populates qualityMetrics from quality_metrics response", async () => {
    const qualityMetrics = {
      build_success_rate: 0.75,
      in_flight_build_count: 4,
      first_pass_approval_rate: 0.8,
      mean_fix_cycles_to_merge: 1.2,
      sample_size: 10,
      computed_at: "2026-06-01T10:00:00Z",
      format_error_rate: 0.2,
      format_error_sample: 5,
      format_error_low_data: false,
      recovery_rate: 0.6,
      recovery_sample: 5,
      recovery_low_data: false,
    };
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...HEALTHY, quality_metrics: qualityMetrics }),
      ),
    );
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.qualityMetrics).not.toBeNull();
    });
    expect(result.current.qualityMetrics).toEqual(qualityMetrics);
  });

  it("populates qualityMetrics with the not-measured-yet shape on an empty format-correction sample", async () => {
    const qualityMetrics = {
      build_success_rate: null,
      in_flight_build_count: 0,
      first_pass_approval_rate: null,
      mean_fix_cycles_to_merge: null,
      sample_size: null,
      computed_at: null,
      format_error_rate: null,
      format_error_sample: 0,
      format_error_low_data: true,
      recovery_rate: null,
      recovery_sample: 0,
      recovery_low_data: true,
    };
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...HEALTHY, quality_metrics: qualityMetrics }),
      ),
    );
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.qualityMetrics).not.toBeNull();
    });
    expect(result.current.qualityMetrics).toEqual(qualityMetrics);
  });

  it("exposes a stable refetch function", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(HEALTHY)));
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => expect(result.current.queuePaused).toBe(false));
    expect(typeof result.current.refetch).toBe("function");
  });

  it("default context refetch is a callable no-op", () => {
    const { result } = renderHook(() => useHealthContext());
    expect(() => { result.current.refetch(); }).not.toThrow();
  });

  it("renders children", () => {
    render(
      <HealthProvider>
        <span data-testid="child">hello</span>
      </HealthProvider>,
    );
    expect(screen.getByTestId("child")).toBeInTheDocument();
  });
});

describe("HealthProvider — spend-cap stop", () => {
  const STOP = {
    cause: "Anthropic refused work because a spend limit was reached.",
    fix: "Raise the spend limit, then press Resume.",
    regain_at: "2026-10-01T00:00:00+00:00",
  };
  const STOPPED = { ...PAUSED, paused_until: 4102444800, spend_cap_stop: STOP };

  it("publishes spend_cap_stop from the health response", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(STOPPED)));
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.spendCapStop).toEqual(STOP);
    });
    expect(result.current.queuePaused).toBe(true);
    expect(result.current.pausedUntil).toBe(4102444800);
  });

  it.each([
    ["absent", HEALTHY],
    ["null", { ...HEALTHY, spend_cap_stop: null }],
  ])("publishes null when spend_cap_stop is %s", async (_label, body) => {
    server.use(http.get("/api/health", () => HttpResponse.json(body)));
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.queuePaused).toBe(false);
    });
    expect(result.current.spendCapStop).toBeNull();
  });

  it("clears spendCapStop together with queuePaused when a later read fails", async () => {
    let calls = 0;
    server.use(
      http.get("/api/health", () => {
        calls += 1;
        return calls === 1 ? HttpResponse.json(STOPPED) : HttpResponse.error();
      }),
    );
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.spendCapStop).toEqual(STOP);
    });
    act(() => {
      result.current.refetch();
    });
    await waitFor(() => {
      expect(result.current.spendCapStop).toBeNull();
    });
    expect(result.current.queuePaused).toBe(false);
    expect(result.current.pausedUntil).toBeNull();
  });
});

describe("HealthProvider — dispatch readiness", () => {
  const ISSUES = [
    {
      engine: "acme/widget",
      requirement: "reviewer_credential",
      state: "not_ready" as const,
      message: "no reviewer credential is stored",
      fix: "store a reviewer credential for acme/widget",
    },
    {
      engine: "acme/widget",
      requirement: "code_quality",
      state: "unknown" as const,
      message: "could not be evaluated (OSError)",
      fix: "./setup.sh",
    },
  ];

  it("publishes dispatch_readiness from the health response", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...HEALTHY, dispatch_readiness: ISSUES })),
    );
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.dispatchReadiness).toEqual(ISSUES);
    });
  });

  it.each([
    ["absent", HEALTHY],
    ["empty", { ...HEALTHY, dispatch_readiness: [] }],
  ])("publishes an empty list when dispatch_readiness is %s", async (_label, body) => {
    server.use(http.get("/api/health", () => HttpResponse.json(body)));
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.queuePaused).toBe(false);
    });
    expect(result.current.dispatchReadiness).toEqual([]);
  });

  it("clears dispatchReadiness when a later read fails", async () => {
    let calls = 0;
    server.use(
      http.get("/api/health", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ ...HEALTHY, dispatch_readiness: ISSUES })
          : HttpResponse.error();
      }),
    );
    const { result } = renderHook(() => useHealthContext(), { wrapper });
    await waitFor(() => {
      expect(result.current.dispatchReadiness).toEqual(ISSUES);
    });
    act(() => {
      result.current.refetch();
    });
    await waitFor(() => {
      expect(result.current.dispatchReadiness).toEqual([]);
    });
  });
});

describe("HealthProvider — polling cadence", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("polls every 5s when not paused", async () => {
    let calls = 0;
    server.use(
      http.get("/api/health", () => {
        calls++;
        return HttpResponse.json(HEALTHY);
      }),
    );
    const { unmount } = renderHook(() => useHealthContext(), { wrapper });
    await act(flushAsync);
    calls = 0;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    await act(flushAsync);
    expect(calls).toBe(1);
    unmount();
  });

  it("polls every 1s when paused", async () => {
    let calls = 0;
    server.use(
      http.get("/api/health", () => {
        calls++;
        return HttpResponse.json(PAUSED);
      }),
    );
    const { result, unmount } = renderHook(() => useHealthContext(), { wrapper });
    await act(flushAsync);
    expect(result.current.queuePaused).toBe(true);
    calls = 0;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    await act(flushAsync);
    expect(calls).toBe(1);
    unmount();
  });

  it("cleans up on unmount", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(HEALTHY)));
    const { unmount } = renderHook(() => useHealthContext(), { wrapper });
    await act(flushAsync);
    unmount();
    expect(() => vi.advanceTimersByTime(10000)).not.toThrow();
  });
});
