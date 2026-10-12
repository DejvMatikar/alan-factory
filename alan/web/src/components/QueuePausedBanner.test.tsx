import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HealthContext, HealthProvider } from "../api/HealthProvider";
import type { HealthContextValue } from "../api/HealthProvider";
import { formatDate } from "../lib/format";
import { server } from "../test/server";
import QueuePausedBanner from "./QueuePausedBanner";

const HEALTHY = { ok: true as const, queue_paused: false, paused_until: null, health_state: null };
const HOLD_EPOCH = 4102444800;
const REGAIN_AT = "2026-10-01T00:00:00+00:00";
const SPEND_CAP_STOP = {
  cause: "Anthropic refused work because a spend limit was reached.",
  fix: "Raise the spend limit, then press Resume.",
  regain_at: REGAIN_AT,
};

function stoppedHealth(stop: Record<string, unknown> = SPEND_CAP_STOP, pausedUntil: number | null = HOLD_EPOCH) {
  return { ok: true, queue_paused: true, paused_until: pausedUntil, health_state: null, spend_cap_stop: stop };
}

function serveHealth(body: Record<string, unknown>) {
  server.use(http.get("/api/health", () => HttpResponse.json(body)));
}

function contextValue(overrides: Partial<HealthContextValue> = {}): HealthContextValue {
  return {
    queuePaused: true,
    pausedUntil: HOLD_EPOCH,
    spendCapStop: SPEND_CAP_STOP,
    healthState: null,
    blockedCount: 0,
    needsUserCount: 0,
    lastComputedAt: null,
    finisherRate: null,
    qualityMetrics: null,
    sweeper: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

function renderWithProvider(ui: ReactElement) {
  return render(<HealthProvider>{ui}</HealthProvider>);
}

async function flushAsync(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("QueuePausedBanner — real timers", () => {
  it("renders nothing when queue is not paused", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(HEALTHY)));
    renderWithProvider(<QueuePausedBanner />);
    await waitFor(() => {
      expect(screen.queryByTestId("queue-paused-banner")).not.toBeInTheDocument();
    });
  });

  it("renders nothing when queue_paused=true but paused_until=null", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ok: true, queue_paused: true, paused_until: null, health_state: null }),
      ),
    );
    renderWithProvider(<QueuePausedBanner />);
    await waitFor(() => {
      expect(screen.queryByTestId("queue-paused-banner")).not.toBeInTheDocument();
    });
  });

  it("renders the banner when queue_paused is true", async () => {
    const pausedUntil = Date.now() / 1000 + 3600;
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ok: true, queue_paused: true, paused_until: pausedUntil, health_state: null }),
      ),
    );
    renderWithProvider(<QueuePausedBanner />);
    const banner = await screen.findByTestId("queue-paused-banner");
    expect(banner).toHaveAttribute("role", "status");
    expect(banner).toHaveAttribute("aria-live", "polite");
  });

  it("shows resume time and countdown in the banner text", async () => {
    const pausedUntil = Date.now() / 1000 + 3661;
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ok: true, queue_paused: true, paused_until: pausedUntil, health_state: null }),
      ),
    );
    renderWithProvider(<QueuePausedBanner />);
    const banner = await screen.findByTestId("queue-paused-banner");
    expect(banner.textContent).toMatch(/resumes at \d{1,2}:\d{2}/);
    expect(banner.textContent).toMatch(/in \d+:\d{2}/);
  });

  it("tolerates /api/health network error — no banner, no crash", async () => {
    server.use(http.get("/api/health", () => HttpResponse.error()));
    renderWithProvider(<QueuePausedBanner />);
    await waitFor(() => {
      expect(screen.queryByTestId("queue-paused-banner")).not.toBeInTheDocument();
    });
  });

  it("tolerates /api/health 500 response — no banner, no crash", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ error: "internal server error" }, { status: 500 }),
      ),
    );
    renderWithProvider(<QueuePausedBanner />);
    await waitFor(() => {
      expect(screen.queryByTestId("queue-paused-banner")).not.toBeInTheDocument();
    });
  });
});

describe("QueuePausedBanner — fake timers", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("updates countdown every second via the separate timer", async () => {
    const pausedUntil = Date.now() / 1000 + 3661;
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ok: true, queue_paused: true, paused_until: pausedUntil, health_state: null }),
      ),
    );
    renderWithProvider(<QueuePausedBanner />);
    await act(flushAsync);
    const before = screen.getByTestId("queue-paused-banner").textContent ?? "";
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    const after = screen.getByTestId("queue-paused-banner").textContent ?? "";
    expect(after).not.toBe(before);
    expect(after).toMatch(/in \d+:\d{2}/);
  });

  it("dismisses when polling returns queue_paused=false", async () => {
    const pausedUntil = Date.now() / 1000 + 3600;
    let paused = true;
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({
          ok: true,
          queue_paused: paused,
          paused_until: paused ? pausedUntil : null,
          health_state: null,
        }),
      ),
    );
    renderWithProvider(<QueuePausedBanner />);
    await act(flushAsync);
    expect(screen.getByTestId("queue-paused-banner")).toBeInTheDocument();
    paused = false;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    await act(flushAsync);
    expect(screen.queryByTestId("queue-paused-banner")).not.toBeInTheDocument();
  });

  it("cleans up both timers on unmount", async () => {
    const pausedUntil = Date.now() / 1000 + 3600;
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ok: true, queue_paused: true, paused_until: pausedUntil, health_state: null }),
      ),
    );
    const { unmount } = renderWithProvider(<QueuePausedBanner />);
    await act(flushAsync);
    unmount();
    expect(() => vi.advanceTimersByTime(10000)).not.toThrow();
  });
});

describe("QueuePausedBanner — resume control", () => {
  it("hides the resume button when the queue is not paused", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(HEALTHY)));
    renderWithProvider(<QueuePausedBanner />);
    await waitFor(() => {
      expect(screen.queryByTestId("queue-resume-button")).not.toBeInTheDocument();
    });
  });

  it("shows the resume button inside the paused banner", async () => {
    const pausedUntil = Date.now() / 1000 + 3600;
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ok: true, queue_paused: true, paused_until: pausedUntil, health_state: null }),
      ),
    );
    renderWithProvider(<QueuePausedBanner />);
    expect(await screen.findByTestId("queue-resume-button")).toBeInTheDocument();
  });

  it("the announcement region is a sibling of, never nested inside, the banner", async () => {
    const pausedUntil = Date.now() / 1000 + 3600;
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ok: true, queue_paused: true, paused_until: pausedUntil, health_state: null }),
      ),
    );
    renderWithProvider(<QueuePausedBanner />);
    const banner = await screen.findByTestId("queue-paused-banner");
    const announcement = screen.getByTestId("queue-resume-announcement");
    expect(banner.contains(announcement)).toBe(false);
  });

  it("does not render a confirmation dialog when the resume button is clicked", async () => {
    const pausedUntil = Date.now() / 1000 + 3600;
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ok: true, queue_paused: true, paused_until: pausedUntil, health_state: null }),
      ),
    );
    renderWithProvider(<QueuePausedBanner />);
    const button = await screen.findByTestId("queue-resume-button");
    fireEvent.click(button);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("a successful resume announces the resumed text and refetches health", async () => {
    const pausedUntil = Date.now() / 1000 + 3600;
    let healthCalls = 0;
    server.use(
      http.get("/api/health", () => {
        healthCalls += 1;
        return HttpResponse.json({
          ok: true,
          queue_paused: true,
          paused_until: pausedUntil,
          health_state: null,
        });
      }),
    );
    renderWithProvider(<QueuePausedBanner />);
    const button = await screen.findByTestId("queue-resume-button");
    const callsBeforeClick = healthCalls;
    fireEvent.click(button);
    await waitFor(() => {
      expect(screen.getByTestId("queue-resume-announcement").textContent).toBe("Queue resumed.");
    });
    await waitFor(() => {
      expect(healthCalls).toBeGreaterThan(callsBeforeClick);
    });
  });

  it("a failed resume announces a failure and re-enables the button", async () => {
    const pausedUntil = Date.now() / 1000 + 3600;
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ok: true, queue_paused: true, paused_until: pausedUntil, health_state: null }),
      ),
      http.post("/api/queue/resume", () =>
        HttpResponse.json({ detail: "control token mismatch" }, { status: 403 }),
      ),
    );
    renderWithProvider(<QueuePausedBanner />);
    const button = await screen.findByTestId("queue-resume-button");
    fireEvent.click(button);
    await waitFor(() => {
      expect(screen.getByTestId("queue-resume-announcement").textContent).toBe(
        "Couldn't resume the queue. Try again.",
      );
    });
    expect(screen.getByTestId("queue-resume-button")).not.toBeDisabled();
  });
});

describe("QueuePausedBanner — announcement precedence on re-trip", () => {
  function renderWithContext(value: HealthContextValue, ui: ReactElement) {
    return render(<HealthContext.Provider value={value}>{ui}</HealthContext.Provider>);
  }

  it("clears a stale announcement once the queue becomes paused again", async () => {
    const pausedUntil1 = Date.now() / 1000 + 3600;
    const baseValue = contextValue({ pausedUntil: pausedUntil1, spendCapStop: null });
    server.use(
      http.post("/api/queue/resume", () =>
        HttpResponse.json({ ok: true, queue_paused: false, paused_until: null }),
      ),
    );
    const { rerender } = renderWithContext(baseValue, <QueuePausedBanner />);
    const button = await screen.findByTestId("queue-resume-button");
    fireEvent.click(button);
    await waitFor(() => {
      expect(screen.getByTestId("queue-resume-announcement").textContent).toBe("Queue resumed.");
    });

    rerender(
      <HealthContext.Provider value={{ ...baseValue, queuePaused: false, pausedUntil: null }}>
        <QueuePausedBanner />
      </HealthContext.Provider>,
    );
    expect(screen.queryByTestId("queue-paused-banner")).not.toBeInTheDocument();
    expect(screen.getByTestId("queue-resume-announcement").textContent).toBe("Queue resumed.");

    const pausedUntil2 = Date.now() / 1000 + 1800;
    rerender(
      <HealthContext.Provider
        value={{ ...baseValue, queuePaused: true, pausedUntil: pausedUntil2 }}
      >
        <QueuePausedBanner />
      </HealthContext.Provider>,
    );
    expect(screen.getByTestId("queue-paused-banner")).toBeInTheDocument();
    expect(screen.getByTestId("queue-resume-announcement").textContent).toBe("");
  });
});

describe("QueuePausedBanner — spend-cap stop variant", () => {
  it("shows the cause, the fix and the regain date as text in a status region", async () => {
    serveHealth(stoppedHealth());
    renderWithProvider(<QueuePausedBanner />);
    const banner = await screen.findByTestId("queue-stopped-banner");
    expect(banner).toHaveAttribute("role", "status");
    expect(banner).toHaveAttribute("aria-live", "polite");
    expect(banner).toHaveTextContent("Queue stopped");
    expect(banner).toHaveTextContent(SPEND_CAP_STOP.cause);
    expect(banner).toHaveTextContent(SPEND_CAP_STOP.fix);
    expect(banner).toHaveTextContent(`Access returns ${formatDate(REGAIN_AT)}.`);
  });

  it("replaces the ordinary banner and shows no countdown", async () => {
    serveHealth(stoppedHealth());
    renderWithProvider(<QueuePausedBanner />);
    const banner = await screen.findByTestId("queue-stopped-banner");
    expect(screen.queryByTestId("queue-paused-banner")).not.toBeInTheDocument();
    expect(banner.textContent).not.toMatch(/auto-resumes|in \d+:\d{2}/);
  });

  it.each([
    ["null", { cause: SPEND_CAP_STOP.cause, fix: SPEND_CAP_STOP.fix, regain_at: null }],
    ["absent", { cause: SPEND_CAP_STOP.cause, fix: SPEND_CAP_STOP.fix }],
    ["unparseable", { cause: SPEND_CAP_STOP.cause, fix: SPEND_CAP_STOP.fix, regain_at: "not-a-date" }],
  ])("omits the regain date when it is %s", async (_label, stop) => {
    serveHealth(stoppedHealth(stop));
    renderWithProvider(<QueuePausedBanner />);
    const banner = await screen.findByTestId("queue-stopped-banner");
    expect(banner).toHaveTextContent(SPEND_CAP_STOP.fix);
    expect(banner.textContent).not.toContain("Access returns");
    expect(banner.textContent).not.toMatch(/NaN|Invalid/);
  });

  it.each([
    ["the year-2100 hold", HOLD_EPOCH],
    ["a hold a minute away", Date.now() / 1000 + 60],
    ["no deadline", null],
  ])("is chosen from the reported stop, not from %s", async (_label, pausedUntil) => {
    serveHealth(stoppedHealth(SPEND_CAP_STOP, pausedUntil));
    renderWithProvider(<QueuePausedBanner />);
    expect(await screen.findByTestId("queue-stopped-banner")).toBeInTheDocument();
    expect(screen.queryByTestId("queue-paused-banner")).not.toBeInTheDocument();
  });

  it("reuses the resume control and keeps the announcement region outside the banner", async () => {
    serveHealth(stoppedHealth());
    renderWithProvider(<QueuePausedBanner />);
    const banner = await screen.findByTestId("queue-stopped-banner");
    expect(banner).toContainElement(screen.getByTestId("queue-resume-button"));
    expect(banner.contains(screen.getByTestId("queue-resume-announcement"))).toBe(false);
  });

  it("one resume announces it and leaves the stop variant", async () => {
    let resumed = false;
    server.use(
      http.get("/api/health", () => HttpResponse.json(resumed ? HEALTHY : stoppedHealth())),
      http.post("/api/queue/resume", () => {
        resumed = true;
        return HttpResponse.json({ ok: true, queue_paused: false, paused_until: null });
      }),
    );
    renderWithProvider(<QueuePausedBanner />);
    fireEvent.click(await screen.findByTestId("queue-resume-button"));
    await waitFor(() => {
      expect(screen.queryByTestId("queue-stopped-banner")).not.toBeInTheDocument();
    });
    expect(screen.queryByTestId("queue-paused-banner")).not.toBeInTheDocument();
    expect(screen.getByTestId("queue-resume-announcement").textContent).toBe("Queue resumed.");
  });

  it("a failed resume announces a failure and keeps the stop variant", async () => {
    serveHealth(stoppedHealth());
    server.use(
      http.post("/api/queue/resume", () =>
        HttpResponse.json({ detail: "control token mismatch" }, { status: 403 }),
      ),
    );
    renderWithProvider(<QueuePausedBanner />);
    fireEvent.click(await screen.findByTestId("queue-resume-button"));
    await waitFor(() => {
      expect(screen.getByTestId("queue-resume-announcement").textContent).toBe(
        "Couldn't resume the queue. Try again.",
      );
    });
    expect(screen.getByTestId("queue-stopped-banner")).toBeInTheDocument();
    expect(screen.getByTestId("queue-resume-button")).not.toBeDisabled();
  });

  it("falls back to the ordinary banner with a valid date when the stored reason is unreadable", async () => {
    serveHealth({ ...HEALTHY, queue_paused: true, paused_until: HOLD_EPOCH });
    renderWithProvider(<QueuePausedBanner />);
    const banner = await screen.findByTestId("queue-paused-banner");
    expect(screen.queryByTestId("queue-stopped-banner")).not.toBeInTheDocument();
    expect(banner.textContent).toMatch(/auto-resumes at \d{1,2}:\d{2}/);
    expect(banner.textContent).not.toMatch(/NaN|Invalid/);
  });

  it("switches between the two variants as the reported stop appears and clears", () => {
    const stopped = contextValue();
    const ordinary = contextValue({ spendCapStop: null, pausedUntil: Date.now() / 1000 + 3600 });
    const { rerender } = render(
      <HealthContext.Provider value={stopped}>
        <QueuePausedBanner />
      </HealthContext.Provider>,
    );
    expect(screen.getByTestId("queue-stopped-banner")).toBeInTheDocument();
    rerender(
      <HealthContext.Provider value={ordinary}>
        <QueuePausedBanner />
      </HealthContext.Provider>,
    );
    expect(screen.queryByTestId("queue-stopped-banner")).not.toBeInTheDocument();
    expect(screen.getByTestId("queue-paused-banner").textContent).toMatch(/in \d+:\d{2}/);
    rerender(
      <HealthContext.Provider value={stopped}>
        <QueuePausedBanner />
      </HealthContext.Provider>,
    );
    expect(screen.getByTestId("queue-stopped-banner")).toBeInTheDocument();
    expect(screen.queryByTestId("queue-paused-banner")).not.toBeInTheDocument();
  });
});

describe("QueuePausedBanner — stop variant timers", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("schedules no timer while the queue is stopped", async () => {
    render(
      <HealthContext.Provider value={contextValue()}>
        <QueuePausedBanner />
      </HealthContext.Provider>,
    );
    const before = screen.getByTestId("queue-stopped-banner").textContent;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(vi.getTimerCount()).toBe(0);
    expect(screen.getByTestId("queue-stopped-banner").textContent).toBe(before);
  });

  it("still schedules the clock timers for an ordinary pause", () => {
    render(
      <HealthContext.Provider value={contextValue({ spendCapStop: null })}>
        <QueuePausedBanner />
      </HealthContext.Provider>,
    );
    expect(vi.getTimerCount()).toBeGreaterThan(0);
  });
});
