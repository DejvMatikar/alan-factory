import { describe, expect, it, vi } from "vitest";

import { queryClient } from "../api/queryClient";
import {
  createRequestTally,
  hasAsyncLeak,
  installIsolationResets,
  reportAsyncLeak,
  type RequestLifecycleEmitter,
} from "./isolation";
import { server } from "./server";

type Listener = (payload: { requestId: string; request: Request }) => void;

function makeFakeEmitter(): RequestLifecycleEmitter & {
  emit: (event: string, payload: { requestId: string; request: Request }) => void;
} {
  const listeners = new Map<string, Set<Listener>>();
  return {
    on(event: string, listener: Listener) {
      const set = listeners.get(event) ?? new Set<Listener>();
      set.add(listener);
      listeners.set(event, set);
      return undefined;
    },
    removeListener(event: string, listener: Listener) {
      listeners.get(event)?.delete(listener);
      return undefined;
    },
    emit(event, payload) {
      for (const listener of listeners.get(event) ?? []) listener(payload);
    },
  };
}

describe("createRequestTally", () => {
  it("flags a request that starts but has no matching end as a leak", () => {
    const emitter = makeFakeEmitter();
    const tally = createRequestTally(emitter);
    tally.attach();

    emitter.emit("request:start", {
      requestId: "r1",
      request: new Request("https://isolation-pin.test/leaked"),
    });

    const snapshot = tally.snapshot();
    expect(hasAsyncLeak(snapshot)).toBe(true);
    expect(snapshot.inFlight).toEqual([
      { requestId: "r1", method: "GET", pathname: "/leaked" },
    ]);

    tally.detach();
  });

  it("does not flag a request whose end event already arrived", () => {
    const emitter = makeFakeEmitter();
    const tally = createRequestTally(emitter);
    tally.attach();

    const request = new Request("https://isolation-pin.test/ok");
    emitter.emit("request:start", { requestId: "r2", request });
    emitter.emit("request:end", { requestId: "r2", request });

    const snapshot = tally.snapshot();
    expect(hasAsyncLeak(snapshot)).toBe(false);
    expect(snapshot.inFlight).toEqual([]);

    tally.detach();
  });

  it("counts request:unhandled events", () => {
    const emitter = makeFakeEmitter();
    const tally = createRequestTally(emitter);
    tally.attach();

    emitter.emit("request:unhandled", {
      requestId: "r3",
      request: new Request("https://isolation-pin.test/missing"),
    });

    const snapshot = tally.snapshot();
    expect(snapshot.unhandledPaths).toEqual(["/missing"]);
    expect(hasAsyncLeak(snapshot)).toBe(true);

    tally.detach();
  });

  it("reset clears prior in-flight and unhandled state", () => {
    const emitter = makeFakeEmitter();
    const tally = createRequestTally(emitter);
    tally.attach();

    emitter.emit("request:start", {
      requestId: "r4",
      request: new Request("https://isolation-pin.test/leaked"),
    });
    tally.reset();

    expect(tally.snapshot()).toEqual({ inFlight: [], unhandledPaths: [] });

    tally.detach();
  });

  it("detach stops tracking further events", () => {
    const emitter = makeFakeEmitter();
    const tally = createRequestTally(emitter);
    tally.attach();
    tally.detach();

    emitter.emit("request:start", {
      requestId: "r5",
      request: new Request("https://isolation-pin.test/ignored"),
    });

    expect(tally.snapshot().inFlight).toEqual([]);
  });
});

describe("reportAsyncLeak", () => {
  it("warns once with the in-flight and unhandled details", () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    reportAsyncLeak(
      {
        inFlight: [{ requestId: "r1", method: "GET", pathname: "/leaked" }],
        unhandledPaths: ["/missing"],
      },
      "some test",
    );

    expect(warnSpy).toHaveBeenCalledTimes(1);
    const message = String(warnSpy.mock.calls[0]?.[0]);
    expect(message).toContain("some test");
    expect(message).toContain("GET /leaked");
    expect(message).toContain("/missing");
  });
});

describe("installIsolationResets", () => {
  it("seeds the query cache", () => {
    queryClient.setQueryData(["isolation-unit"], "seeded");
    expect(queryClient.getQueryData(["isolation-unit"])).toBe("seeded");
  });

  it("starts the next test with an empty query cache", () => {
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
  });

  it("attaches no further listeners when installed a second time", () => {
    const on = vi.spyOn(server.events, "on");
    installIsolationResets();
    expect(on).not.toHaveBeenCalled();
  });
});
