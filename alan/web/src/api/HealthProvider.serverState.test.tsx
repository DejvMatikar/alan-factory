import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { useMemo } from "react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { HealthProvider, useHealthContext } from "./HealthProvider";
import type { HealthContextValue } from "./HealthProvider";
import { ServerEventsContext } from "./ServerEventsProvider";
import { server } from "../test/server";

const PAUSED_UNTIL = 4102444800;
const HEALTHY = { ok: true, queue_paused: false, paused_until: null, health_state: null };
const PAUSED_AND_BLOCKED = {
  ...HEALTHY,
  queue_paused: true,
  paused_until: PAUSED_UNTIL,
  health_state: { verdict: "degraded", blocked_count: 2, in_flight_count: 0, findings: [], computed_at: "" },
};

type Step = Record<string, unknown> | "error";

function serveHealth(steps: Step[]) {
  const tally = { calls: 0 };
  server.use(
    http.get("/api/health", () => {
      const step = steps[Math.min(tally.calls, steps.length - 1)]!;
      tally.calls += 1;
      return step === "error" ? HttpResponse.error() : HttpResponse.json(step);
    }),
  );
  return tally;
}

function Events({ healthCount, children }: Readonly<{ healthCount: number; children: ReactNode }>) {
  const value = useMemo(
    () => ({ counters: { workspace_list: 0, health: healthCount, engines: 0 }, connected: true }),
    [healthCount],
  );
  return <ServerEventsContext.Provider value={value}>{children}</ServerEventsContext.Provider>;
}

const seenRefetches: HealthContextValue["refetch"][] = [];

function Probe() {
  const context = useHealthContext();
  seenRefetches.push(context.refetch);
  return (
    <>
      <span data-testid="paused">{String(context.queuePaused)}</span>
      <span data-testid="paused-until">{String(context.pausedUntil)}</span>
      <span data-testid="blocked">{String(context.blockedCount)}</span>
      <button type="button" onClick={context.refetch}>
        refetch
      </button>
    </>
  );
}

function renderProvider(healthCount = 0) {
  const tree = (count: number) => (
    <Events healthCount={count}>
      <HealthProvider>
        <Probe />
      </HealthProvider>
    </Events>
  );
  const view = render(tree(healthCount));
  return { rerenderWith: (count: number) => { view.rerender(tree(count)); } };
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 25);
    });
  });
}

async function expectPaused(paused: boolean): Promise<void> {
  await waitFor(() => {
    expect(screen.getByTestId("paused")).toHaveTextContent(String(paused));
  });
}

describe("HealthProvider — failed read", () => {
  it("resets paused state and its deadline but retains every other value", async () => {
    serveHealth([PAUSED_AND_BLOCKED, "error"]);
    renderProvider();
    await expectPaused(true);
    expect(screen.getByTestId("paused-until")).toHaveTextContent(String(PAUSED_UNTIL));
    expect(screen.getByTestId("blocked")).toHaveTextContent("2");
    fireEvent.click(screen.getByRole("button", { name: "refetch" }));
    await expectPaused(false);
    expect(screen.getByTestId("paused-until")).toHaveTextContent("null");
    expect(screen.getByTestId("blocked")).toHaveTextContent("2");
  });

  it("restores paused state when the next read succeeds again", async () => {
    serveHealth([PAUSED_AND_BLOCKED, "error", PAUSED_AND_BLOCKED]);
    renderProvider();
    await expectPaused(true);
    fireEvent.click(screen.getByRole("button", { name: "refetch" }));
    await expectPaused(false);
    fireEvent.click(screen.getByRole("button", { name: "refetch" }));
    await expectPaused(true);
    expect(screen.getByTestId("paused-until")).toHaveTextContent(String(PAUSED_UNTIL));
  });

  it("publishes the not-paused defaults when the very first read fails", async () => {
    const tally = serveHealth(["error"]);
    renderProvider();
    await waitFor(() => {
      expect(tally.calls).toBe(1);
    });
    await settle();
    expect(screen.getByTestId("paused")).toHaveTextContent("false");
    expect(screen.getByTestId("paused-until")).toHaveTextContent("null");
    expect(screen.getByTestId("blocked")).toHaveTextContent("0");
  });

  it("stops polling the paused queue once a poll fails", async () => {
    vi.useFakeTimers();
    const tally = serveHealth([PAUSED_AND_BLOCKED, "error"]);
    renderProvider();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(tally.calls).toBe(2);
    expect(screen.getByTestId("paused")).toHaveTextContent("false");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    expect(tally.calls).toBe(2);
  });
});

describe("HealthProvider — health change events", () => {
  it("issues exactly one request on mount when the bus already reports a health change", async () => {
    const tally = serveHealth([HEALTHY]);
    renderProvider(1);
    await waitFor(() => {
      expect(tally.calls).toBe(1);
    });
    await settle();
    expect(tally.calls).toBe(1);
  });

  it("refetches when a health change event arrives while the tab is visible", async () => {
    const tally = serveHealth([HEALTHY]);
    const view = renderProvider(0);
    await waitFor(() => {
      expect(tally.calls).toBe(1);
    });
    view.rerenderWith(1);
    await waitFor(() => {
      expect(tally.calls).toBe(2);
    });
  });

  it("issues no request for a health change event while the tab is hidden, and one once it is visible", async () => {
    const tally = serveHealth([HEALTHY]);
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    const view = renderProvider(0);
    await waitFor(() => {
      expect(tally.calls).toBe(1);
    });
    hidden.mockReturnValue(true);
    view.rerenderWith(1);
    await settle();
    expect(tally.calls).toBe(1);
    hidden.mockReturnValue(false);
    view.rerenderWith(2);
    await waitFor(() => {
      expect(tally.calls).toBe(2);
    });
  });

  it("still fetches on an explicit refetch while the tab is hidden", async () => {
    const tally = serveHealth([HEALTHY]);
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    renderProvider(0);
    await waitFor(() => {
      expect(tally.calls).toBe(1);
    });
    hidden.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "refetch" }));
    await waitFor(() => {
      expect(tally.calls).toBe(2);
    });
  });

  it("keeps the same refetch function while the published value changes", async () => {
    serveHealth([HEALTHY, PAUSED_AND_BLOCKED]);
    seenRefetches.length = 0;
    renderProvider();
    await expectPaused(false);
    fireEvent.click(screen.getByRole("button", { name: "refetch" }));
    await expectPaused(true);
    expect(seenRefetches.length).toBeGreaterThan(1);
    expect(new Set(seenRefetches).size).toBe(1);
  });
});
