import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement } from "react";
import { describe, expect, it } from "vitest";

import type { UnblockFleetActivity, UnblockScopedActivity } from "../api";
import { ServerEventsContext } from "../api/ServerEventsProvider";
import { server } from "../test/server";
import { useUnblockerActivity } from "./useUnblockerActivity";

const SUMMARY = {
  window_days: 30,
  events_total: 0,
  workspaces_touched: 0,
  waited_workspaces: 0,
  diagnosed_workspaces: 0,
  diagnosable_workspaces: 0,
  still_stuck_workspaces: 0,
  gave_up_workspaces: 0,
  aggregates: [],
};

const FLEET: UnblockFleetActivity = {
  ...SUMMARY,
  scope: "fleet",
  events_total: 5,
  ever_recorded: true,
  unattributed_events: 0,
};

function scoped(repo: string): UnblockScopedActivity {
  return { ...SUMMARY, scope: "repo", repo, fleet_ever_recorded: true, events: [], workspaces: [] };
}

function deferred() {
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { gate, release };
}

describe("useUnblockerActivity", () => {
  it("starts unloaded with no snapshot and no error", () => {
    server.use(http.get("/api/health/unblocker-activity", () => HttpResponse.json(FLEET)));
    const { result } = renderHook(() => useUnblockerActivity(null));
    expect(result.current).toEqual({ snapshot: null, loaded: false, error: false });
  });

  it("populates the fleet snapshot once the initial fetch resolves", async () => {
    server.use(http.get("/api/health/unblocker-activity", () => HttpResponse.json(FLEET)));
    const { result } = renderHook(() => useUnblockerActivity(null));
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.snapshot).toEqual(FLEET);
    expect(result.current.error).toBe(false);
  });

  it("requests the scoped payload for the given engine", async () => {
    const seen: (string | null)[] = [];
    server.use(
      http.get("/api/health/unblocker-activity", ({ request }) => {
        const engine = new URL(request.url).searchParams.get("engine");
        seen.push(engine);
        return HttpResponse.json(scoped(engine ?? ""));
      }),
    );
    const { result } = renderHook(() => useUnblockerActivity("acme/widget"));
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(seen).toEqual(["acme/widget"]);
    expect(result.current.snapshot).toEqual(scoped("acme/widget"));
  });

  it("reports an error and no snapshot when the fetch fails", async () => {
    server.use(http.get("/api/health/unblocker-activity", () => HttpResponse.error()));
    const { result } = renderHook(() => useUnblockerActivity(null));
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.snapshot).toBeNull();
    expect(result.current.error).toBe(true);
  });

  it("keeps the last good snapshot alongside the error when a refetch fails", async () => {
    let failing = false;
    server.use(
      http.get("/api/health/unblocker-activity", () =>
        failing ? HttpResponse.error() : HttpResponse.json(FLEET),
      ),
    );
    let health = 0;
    const { result, rerender } = renderHook(() => useUnblockerActivity(null), {
      wrapper: ({ children }) =>
        createElement(
          ServerEventsContext.Provider,
          { value: { counters: { workspace_list: 0, health, engines: 0 }, connected: true } },
          children,
        ),
    });
    await waitFor(() => {
      expect(result.current.snapshot).toEqual(FLEET);
    });
    failing = true;
    health = 1;
    rerender();
    await waitFor(() => {
      expect(result.current.error).toBe(true);
    });
    expect(result.current.snapshot).toEqual(FLEET);
    expect(result.current.loaded).toBe(true);
  });

  it("does not carry another scope's snapshot into an error for the new engine", async () => {
    server.use(
      http.get("/api/health/unblocker-activity", ({ request }) =>
        new URL(request.url).searchParams.get("engine") === null
          ? HttpResponse.json(FLEET)
          : HttpResponse.error(),
      ),
    );
    const { result, rerender } = renderHook(({ engine }) => useUnblockerActivity(engine), {
      initialProps: { engine: null as string | null },
    });
    await waitFor(() => {
      expect(result.current.snapshot).toEqual(FLEET);
    });
    rerender({ engine: "acme/widget" });
    await waitFor(() => {
      expect(result.current.error).toBe(true);
    });
    expect(result.current.snapshot).toBeNull();
  });

  it("refetches when the engine changes and drops the previous scope's snapshot immediately", async () => {
    const seen: (string | null)[] = [];
    server.use(
      http.get("/api/health/unblocker-activity", ({ request }) => {
        const engine = new URL(request.url).searchParams.get("engine");
        seen.push(engine);
        return HttpResponse.json(engine === null ? FLEET : scoped(engine));
      }),
    );
    const { result, rerender } = renderHook(({ engine }) => useUnblockerActivity(engine), {
      initialProps: { engine: null as string | null },
    });
    await waitFor(() => {
      expect(result.current.snapshot?.scope).toBe("fleet");
    });
    rerender({ engine: "acme/widget" });
    expect(result.current).toEqual({ snapshot: null, loaded: false, error: false });
    await waitFor(() => {
      expect(result.current.snapshot?.scope).toBe("repo");
    });
    expect(seen).toEqual([null, "acme/widget"]);
  });

  it("ignores a slow response from the previous scope that lands after the current one", async () => {
    const slow = deferred();
    server.use(
      http.get("/api/health/unblocker-activity", async ({ request }) => {
        const engine = new URL(request.url).searchParams.get("engine");
        if (engine === "acme/old") await slow.gate;
        return HttpResponse.json(scoped(engine ?? ""));
      }),
    );
    const { result, rerender } = renderHook(({ engine }) => useUnblockerActivity(engine), {
      initialProps: { engine: "acme/old" },
    });
    rerender({ engine: "acme/new" });
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    slow.release();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(result.current.snapshot).toEqual(scoped("acme/new"));
  });

  it("ignores a failure from the previous scope that lands after the current one", async () => {
    const slow = deferred();
    server.use(
      http.get("/api/health/unblocker-activity", async ({ request }) => {
        const engine = new URL(request.url).searchParams.get("engine");
        if (engine === "acme/old") {
          await slow.gate;
          return HttpResponse.error();
        }
        return HttpResponse.json(scoped(engine ?? ""));
      }),
    );
    const { result, rerender } = renderHook(({ engine }) => useUnblockerActivity(engine), {
      initialProps: { engine: "acme/old" },
    });
    rerender({ engine: "acme/new" });
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    slow.release();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(result.current.error).toBe(false);
    expect(result.current.snapshot).toEqual(scoped("acme/new"));
  });

  it("does not refetch on a rerender that keeps the same engine", async () => {
    let calls = 0;
    server.use(
      http.get("/api/health/unblocker-activity", () => {
        calls += 1;
        return HttpResponse.json(FLEET);
      }),
    );
    const { result, rerender } = renderHook(({ engine }) => useUnblockerActivity(engine), {
      initialProps: { engine: null as string | null },
    });
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    rerender({ engine: null });
    expect(calls).toBe(1);
  });
});
