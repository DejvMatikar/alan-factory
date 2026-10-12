import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import { useHealthDigest } from "./useHealthDigest";

describe("useHealthDigest", () => {
  it("starts with recovery null, residue null, and digestOverdue unknown", () => {
    server.use(
      http.get("/api/health/digest", () =>
        HttpResponse.json({ recovery: null, residue: null, digest_overdue: false }),
      ),
    );
    const { result } = renderHook(() => useHealthDigest(true));
    expect(result.current.recovery).toBeNull();
    expect(result.current.residue).toBeNull();
    expect(result.current.digestOverdue).toBeNull();
    expect(result.current.error).toBe(false);
  });

  it("populates recovery, residue, and digestOverdue once the initial fetch resolves", async () => {
    server.use(
      http.get("/api/health/digest", () =>
        HttpResponse.json({
          recovery: { current_count: 3, previous_count: 1, direction: "up" },
          residue: [{ fingerprint: "abc123", title: "Unclassified spike" }],
          digest_overdue: true,
        }),
      ),
    );
    const { result } = renderHook(() => useHealthDigest(true));
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.recovery).toEqual({
      current_count: 3,
      previous_count: 1,
      direction: "up",
    });
    expect(result.current.residue).toEqual([{ fingerprint: "abc123", title: "Unclassified spike" }]);
    expect(result.current.digestOverdue).toBe(true);
    expect(result.current.error).toBe(false);
  });

  it("tolerates a network error, keeps recovery and residue null, and reports the error", async () => {
    server.use(http.get("/api/health/digest", () => HttpResponse.error()));
    const { result } = renderHook(() => useHealthDigest(true));
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.recovery).toBeNull();
    expect(result.current.residue).toBeNull();
    expect(result.current.error).toBe(true);
  });

  it("distinguishes an empty residue window from a never-measured one", async () => {
    server.use(
      http.get("/api/health/digest", () =>
        HttpResponse.json({ recovery: null, residue: [], digest_overdue: false }),
      ),
    );
    const { result } = renderHook(() => useHealthDigest(true));
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.residue).toEqual([]);
  });

  it("leaves digestOverdue unknown, not on-time, when the first fetch fails", async () => {
    server.use(http.get("/api/health/digest", () => HttpResponse.error()));
    const { result } = renderHook(() => useHealthDigest(true));
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.digestOverdue).toBeNull();
  });

  it("issues no fetch and stays unloaded when disabled", () => {
    let calls = 0;
    server.use(
      http.get("/api/health/digest", () => {
        calls += 1;
        return HttpResponse.json({ recovery: null, residue: [], digest_overdue: false });
      }),
    );
    const { result } = renderHook(() => useHealthDigest(false));
    expect(result.current.loaded).toBe(false);
    expect(calls).toBe(0);
  });

  it("fetches once re-enabled after starting disabled", async () => {
    let calls = 0;
    server.use(
      http.get("/api/health/digest", () => {
        calls += 1;
        return HttpResponse.json({ recovery: null, residue: [], digest_overdue: false });
      }),
    );
    const { result, rerender } = renderHook(({ enabled }) => useHealthDigest(enabled), {
      initialProps: { enabled: false },
    });
    expect(calls).toBe(0);
    rerender({ enabled: true });
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(calls).toBe(1);
  });
});
