import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import { useDriverLoops } from "./useDriverLoops";

describe("useDriverLoops", () => {
  it("starts with an empty loop list and supervised false", () => {
    server.use(
      http.get("/api/driver-loops", () => HttpResponse.json({ supervised: false, loops: [] })),
    );
    const { result } = renderHook(() => useDriverLoops());
    expect(result.current.loops).toEqual([]);
    expect(result.current.supervised).toBe(false);
    expect(result.current.error).toBe(false);
  });

  it("populates loops and supervised once the initial fetch resolves", async () => {
    server.use(
      http.get("/api/driver-loops", () =>
        HttpResponse.json({
          supervised: true,
          loops: [
            {
              name: "alan-issue-driver",
              enabled: false,
              dedicated_clone: true,
              paused: false,
              decision: { action: "hold", reason: "disabled", detail: null, next_wake_at: null },
              last_outcome: null,
              final_message: "",
              denial_count: 0,
              run_now_pending: false,
            },
          ],
        }),
      ),
    );
    const { result } = renderHook(() => useDriverLoops());
    await waitFor(() => {
      expect(result.current.loops).toHaveLength(1);
    });
    expect(result.current.loops[0]?.name).toBe("alan-issue-driver");
    expect(result.current.supervised).toBe(true);
    expect(result.current.error).toBe(false);
  });

  it("tolerates a network error, keeps the empty loop list, and reports the error", async () => {
    server.use(http.get("/api/driver-loops", () => HttpResponse.error()));
    const { result } = renderHook(() => useDriverLoops());
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.loops).toEqual([]);
    expect(result.current.supervised).toBe(false);
    expect(result.current.error).toBe(true);
  });
});
