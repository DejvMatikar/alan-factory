import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { useHealthQuery } from "../api/queries";
import { queryClient } from "../api/queryClient";
import { server } from "./server";

const HEALTHY = { ok: true, queue_paused: false, paused_until: null, health_state: null };

describe("query cache isolation pin: seeded entry residue", () => {
  it("test 1 leaves a cached entry behind", () => {
    queryClient.setQueryData(["isolation-pin"], "residue");
    expect(queryClient.getQueryData(["isolation-pin"])).toBe("residue");
  });

  it("test 2 asserts the cached entry was cleared", () => {
    expect(queryClient.getQueryData(["isolation-pin"])).toBeUndefined();
  });
});

describe("query cache isolation pin: health read request count", () => {
  let calls = 0;

  function serveHealth(): void {
    calls = 0;
    server.use(
      http.get("/api/health", () => {
        calls += 1;
        return HttpResponse.json(HEALTHY);
      }),
    );
  }

  async function mountAndAwaitRead(): Promise<void> {
    const { result } = renderHook(() => {
      const { isSuccess } = useHealthQuery();
      return isSuccess;
    });
    await waitFor(() => {
      expect(result.current).toBe(true);
    });
  }

  it("test 1 loads the health read from the network", async () => {
    serveHealth();
    await mountAndAwaitRead();
    expect(calls).toBe(1);
  });

  it("test 2 loads the health read from the network again despite refetchOnMount being off", async () => {
    serveHealth();
    await mountAndAwaitRead();
    expect(calls).toBe(1);
  });
});
