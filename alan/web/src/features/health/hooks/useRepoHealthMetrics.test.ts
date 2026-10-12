import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import {
  METRICS_COMPUTED_AT,
  healthWindow,
  metricWindows,
  repoHealthResponse,
  repoRow,
  scopeWindows,
} from "../../../test/healthWindows";
import { server } from "../../../test/server";
import { useRepoHealthMetrics } from "./useRepoHealthMetrics";

describe("useRepoHealthMetrics", () => {
  it("starts with an empty row list, no fleet windows and null timestamps", () => {
    server.use(http.get("/api/health/repos", () => HttpResponse.json(repoHealthResponse())));
    const { result } = renderHook(() => useRepoHealthMetrics());
    expect(result.current.rows).toEqual([]);
    expect(result.current.fleet).toBeNull();
    expect(result.current.computedAt).toBeNull();
    expect(result.current.metricsComputedAt).toBeNull();
    expect(result.current.unattributed).toBe(0);
    expect(result.current.loaded).toBe(false);
    expect(result.current.error).toBe(false);
  });

  it("populates rows, fleet windows, timestamps and the unattributed count once the fetch resolves", async () => {
    const fleet = scopeWindows({
      outcome: metricWindows(healthWindow(0.8, 30), healthWindow(0.7, 90), "better"),
    });
    server.use(
      http.get("/api/health/repos", () =>
        HttpResponse.json(
          repoHealthResponse({
            computed_at: "2026-06-01T00:00:00Z",
            unattributed: 4,
            fleet,
            rows: [repoRow("acme/widget", { outstanding_findings: { value: 1, sample: 1, low_data: false } })],
          }),
        ),
      ),
    );
    const { result } = renderHook(() => useRepoHealthMetrics());
    await waitFor(() => {
      expect(result.current.rows).toHaveLength(1);
    });
    expect(result.current.rows[0]?.repo).toBe("acme/widget");
    expect(result.current.fleet).toEqual(fleet);
    expect(result.current.computedAt).toBe("2026-06-01T00:00:00Z");
    expect(result.current.metricsComputedAt).toBe(METRICS_COMPUTED_AT);
    expect(result.current.unattributed).toBe(4);
    expect(result.current.loaded).toBe(true);
    expect(result.current.error).toBe(false);
  });

  it("tolerates a network error, keeps the empty state, and reports the error", async () => {
    server.use(http.get("/api/health/repos", () => HttpResponse.error()));
    const { result } = renderHook(() => useRepoHealthMetrics());
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.rows).toEqual([]);
    expect(result.current.fleet).toBeNull();
    expect(result.current.computedAt).toBeNull();
    expect(result.current.metricsComputedAt).toBeNull();
    expect(result.current.error).toBe(true);
  });
});
