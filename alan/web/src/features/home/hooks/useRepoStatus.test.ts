import { act, render, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type RepoStatus } from "../../../api";
import { ServerEventsContext, type ServerEventsValue } from "../../../api/ServerEventsProvider";
import { useRepoStatus } from "./useRepoStatus";

function eventsValue(engines: number): ServerEventsValue {
  return {
    counters: { workspace_list: 0, health: 0, engines },
    connected: true,
  };
}

const SAMPLE_STATUS: RepoStatus = {
  slug: "acme/widget",
  is_default: false,
  main_ci: {
    provenance: { status: "unknown", detail: "no credential available for this repo's forge" },
    green_count: null,
    red_count: null,
    counted_over: null,
  },
  open_prs: { provenance: { status: "unknown", detail: "" }, total: null, authored_by_alan: null },
  cost: { provenance: { status: "unknown", detail: "" }, total_cost_usd: null, unknown_count: 0 },
  delivery_time: { provenance: { status: "unknown", detail: "" }, by_size: [] },
  last_deploy: { provenance: { status: "unknown", detail: "" }, outcome: null, pr_number: null, at: null },
  health: { provenance: { status: "unknown", detail: "" }, finding_count: null, verdict: null },
};

function connectedWrapper({ children }: Readonly<{ children: ReactNode }>) {
  return createElement(ServerEventsContext.Provider, { value: eventsValue(0) }, children);
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("useRepoStatus", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches api.getRepoStatus for the given engine key and resolves to ready", async () => {
    const spy = vi.spyOn(api, "getRepoStatus").mockResolvedValue(SAMPLE_STATUS);
    const { result } = renderHook(() => useRepoStatus("acme/widget"));
    await waitFor(() => {
      expect(result.current.state).toEqual({
        status: "ready",
        data: SAMPLE_STATUS,
        refreshing: false,
        refreshError: null,
      });
    });
    expect(spy).toHaveBeenCalledWith("acme/widget");
  });

  it("issues exactly one request on first render", async () => {
    const spy = vi.spyOn(api, "getRepoStatus").mockResolvedValue(SAMPLE_STATUS);
    const { result } = renderHook(() => useRepoStatus("acme/widget"), { wrapper: connectedWrapper });
    await waitFor(() => {
      expect(result.current.state.status).toBe("ready");
    });
    await settle();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("re-fetches once when the shared engines server-event bucket bumps", async () => {
    const spy = vi.spyOn(api, "getRepoStatus").mockResolvedValue(SAMPLE_STATUS);
    function Probe() {
      useRepoStatus("acme/widget");
      return null;
    }
    const view = render(
      createElement(ServerEventsContext.Provider, { value: eventsValue(0) }, createElement(Probe)),
    );
    await waitFor(() => {
      expect(spy).toHaveBeenCalledTimes(1);
    });
    view.rerender(
      createElement(ServerEventsContext.Provider, { value: eventsValue(1) }, createElement(Probe)),
    );
    await waitFor(() => {
      expect(spy).toHaveBeenCalledTimes(2);
    });
    await settle();
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("re-fetches every sixty seconds while the bus is connected", async () => {
    vi.useFakeTimers();
    const spy = vi.spyOn(api, "getRepoStatus").mockResolvedValue(SAMPLE_STATUS);
    renderHook(() => useRepoStatus("acme/widget"), { wrapper: connectedWrapper });
    await advance(59_999);
    expect(spy).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(spy).toHaveBeenCalledTimes(2);
    await advance(60_000);
    expect(spy).toHaveBeenCalledTimes(3);
  });
});
