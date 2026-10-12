import { act, render, renderHook, screen } from "@testing-library/react";
import type React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type ChangeKind, type ServerEventsHandlers } from "../api";
import {
  ServerEventsContext,
  ServerEventsProvider,
  useChangeRefetch,
  useServerEvents,
  type ServerEventsValue,
} from "./ServerEventsProvider";

function Probe() {
  const { counters, connected } = useServerEvents();
  return (
    <div>
      <span data-testid="c-list">{counters.workspace_list}</span>
      <span data-testid="c-health">{counters.health}</span>
      <span data-testid="c-engines">{counters.engines}</span>
      <span data-testid="connected">{String(connected)}</span>
    </div>
  );
}

function setHidden(hidden: boolean): void {
  Object.defineProperty(document, "hidden", { value: hidden, configurable: true });
}

describe("ServerEventsProvider", () => {
  let handlers: ServerEventsHandlers | null = null;
  let unsubscribeCalls = 0;
  let subscribeCalls = 0;

  beforeEach(() => {
    handlers = null;
    unsubscribeCalls = 0;
    subscribeCalls = 0;
    vi.spyOn(api, "subscribeEvents").mockImplementation((h: ServerEventsHandlers) => {
      handlers = h;
      subscribeCalls += 1;
      return () => {
        unsubscribeCalls += 1;
      };
    });
    vi.spyOn(crypto, "getRandomValues").mockImplementation(((arr: Uint32Array) => {
      arr.fill(0);
      return arr;
    }) as typeof crypto.getRandomValues);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    setHidden(false);
  });

  function renderProvider() {
    return render(
      <ServerEventsProvider>
        <Probe />
      </ServerEventsProvider>,
    );
  }

  it("connects on mount and bumps every counter once", () => {
    renderProvider();
    expect(subscribeCalls).toBe(1);
    expect(screen.getByTestId("connected")).toHaveTextContent("true");
    expect(screen.getByTestId("c-list")).toHaveTextContent("1");
    expect(screen.getByTestId("c-health")).toHaveTextContent("1");
    expect(screen.getByTestId("c-engines")).toHaveTextContent("1");
  });

  it("bumps only the changed kinds", () => {
    renderProvider();
    act(() => {
      handlers?.onChanged(["health"]);
    });
    expect(screen.getByTestId("c-health")).toHaveTextContent("2");
    expect(screen.getByTestId("c-list")).toHaveTextContent("1");
    expect(screen.getByTestId("c-engines")).toHaveTextContent("1");
  });

  it("bumps only the engines counter for an engines-only change", () => {
    renderProvider();
    act(() => {
      handlers?.onChanged(["engines"]);
    });
    expect(screen.getByTestId("c-engines")).toHaveTextContent("2");
    expect(screen.getByTestId("c-list")).toHaveTextContent("1");
    expect(screen.getByTestId("c-health")).toHaveTextContent("1");
  });

  it("reconnects with backoff after a disconnect", () => {
    vi.useFakeTimers();
    try {
      renderProvider();
      act(() => {
        handlers?.onDisconnect();
      });
      expect(screen.getByTestId("connected")).toHaveTextContent("false");
      expect(subscribeCalls).toBe(1);
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(subscribeCalls).toBe(2);
      expect(screen.getByTestId("connected")).toHaveTextContent("true");
      expect(screen.getByTestId("c-list")).toHaveTextContent("2");
    } finally {
      vi.useRealTimers();
    }
  });

  it("grows the backoff on consecutive disconnects and resets on a frame", () => {
    vi.useFakeTimers();
    try {
      renderProvider();
      act(() => {
        handlers?.onDisconnect();
      });
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(subscribeCalls).toBe(2);
      act(() => {
        handlers?.onDisconnect();
      });
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(subscribeCalls).toBe(2);
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(subscribeCalls).toBe(3);
      act(() => {
        handlers?.onChanged(["health"]);
      });
      act(() => {
        handlers?.onDisconnect();
      });
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(subscribeCalls).toBe(4);
    } finally {
      vi.useRealTimers();
    }
  });

  it("tears down when hidden and reconnects when visible again", () => {
    renderProvider();
    act(() => {
      setHidden(true);
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(unsubscribeCalls).toBe(1);
    expect(screen.getByTestId("connected")).toHaveTextContent("false");
    act(() => {
      setHidden(false);
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(subscribeCalls).toBe(2);
    expect(screen.getByTestId("connected")).toHaveTextContent("true");
    expect(screen.getByTestId("c-list")).toHaveTextContent("2");
  });

  it("does not connect while hidden", () => {
    setHidden(true);
    renderProvider();
    expect(subscribeCalls).toBe(0);
    expect(screen.getByTestId("connected")).toHaveTextContent("false");
  });

  it("unsubscribes on unmount", () => {
    const view = renderProvider();
    view.unmount();
    expect(unsubscribeCalls).toBe(1);
  });

  it("provides a safe default outside the provider", () => {
    const { result } = renderHook(() => useServerEvents());
    expect(result.current.connected).toBe(false);
    expect(result.current.counters).toEqual({
      workspace_list: 0,
      health: 0,
      engines: 0,
    });
  });

  it("an incoming inbox kind (retired server-side event, no longer a client ChangeKind) bumps nothing the app reads", () => {
    renderProvider();
    act(() => {
      handlers?.onChanged(["inbox"] as unknown as ChangeKind[]);
    });
    expect(screen.getByTestId("c-list")).toHaveTextContent("1");
    expect(screen.getByTestId("c-health")).toHaveTextContent("1");
    expect(screen.getByTestId("c-engines")).toHaveTextContent("1");
  });
});

describe("useChangeRefetch", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    setHidden(false);
  });

  it("does nothing when disabled", () => {
    vi.useFakeTimers();
    try {
      const refetch = vi.fn();
      renderHook(() => {
        useChangeRefetch("health", refetch, false);
      });
      vi.advanceTimersByTime(60000);
      expect(refetch).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("skips the fallback tick while the tab is hidden", () => {
    vi.useFakeTimers();
    try {
      const refetch = vi.fn();
      renderHook(() => {
        useChangeRefetch("health", refetch);
      });
      expect(refetch).toHaveBeenCalledTimes(1);
      setHidden(true);
      vi.advanceTimersByTime(15000);
      expect(refetch).toHaveBeenCalledTimes(1);
      setHidden(false);
      vi.advanceTimersByTime(15000);
      expect(refetch).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops the fallback poll while connected", () => {
    vi.useFakeTimers();
    try {
      const refetch = vi.fn();
      vi.spyOn(api, "subscribeEvents").mockImplementation(() => () => undefined);
      render(
        <ServerEventsProvider>
          <FallbackProbe refetch={refetch} />
        </ServerEventsProvider>,
      );
      const initial = refetch.mock.calls.length;
      vi.advanceTimersByTime(60000);
      expect(refetch.mock.calls.length).toBe(initial);
    } finally {
      vi.useRealTimers();
    }
  });
});

function FallbackProbe({ refetch }: Readonly<{ refetch: () => void }>) {
  useChangeRefetch("workspace_list", refetch);
  return null;
}

describe("useChangeRefetch — visibility and re-enable", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    setHidden(false);
  });

  function contextWrapper(value: ServerEventsValue) {
    return function Wrapper({ children }: Readonly<{ children: React.ReactNode }>) {
      return (
        <ServerEventsContext.Provider value={value}>
          {children}
        </ServerEventsContext.Provider>
      );
    };
  }

  function eventsValue(count: number): ServerEventsValue {
    return {
      counters: { workspace_list: 0, health: count, engines: 0 },
      connected: true,
    };
  }

  it("skips the mount refetch while hidden, fetches once visible via counter bump", () => {
    setHidden(true);
    const refetch = vi.fn();
    const view = render(
      <ServerEventsContext.Provider value={eventsValue(0)}>
        <RefetchConsumer refetch={refetch} />
      </ServerEventsContext.Provider>,
    );
    expect(refetch).not.toHaveBeenCalled();
    setHidden(false);
    view.rerender(
      <ServerEventsContext.Provider value={eventsValue(1)}>
        <RefetchConsumer refetch={refetch} />
      </ServerEventsContext.Provider>,
    );
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("refetches again when re-enabled with an unchanged counter", () => {
    const refetch = vi.fn();
    const { rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) => {
        useChangeRefetch("health", refetch, enabled);
      },
      { initialProps: { enabled: true }, wrapper: contextWrapper(eventsValue(3)) },
    );
    expect(refetch).toHaveBeenCalledTimes(1);
    rerender({ enabled: false });
    rerender({ enabled: true });
    expect(refetch).toHaveBeenCalledTimes(2);
  });
});

function RefetchConsumer({ refetch }: Readonly<{ refetch: () => void }>) {
  useChangeRefetch("health", refetch);
  return null;
}
