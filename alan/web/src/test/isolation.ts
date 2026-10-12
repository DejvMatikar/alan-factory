import { afterAll, afterEach, beforeAll, beforeEach, expect, vi } from "vitest";

import { queryClient } from "../api/queryClient";
import { server } from "./server";

export function installMswLifecycle(): void {
  beforeAll(() => {
    server.listen({ onUnhandledRequest: "error" });
  });
  afterEach(() => {
    server.resetHandlers();
  });
  afterAll(() => {
    server.close();
  });
}

export interface InFlightRequest {
  requestId: string;
  method: string;
  pathname: string;
}

export interface RequestTallySnapshot {
  inFlight: InFlightRequest[];
  unhandledPaths: string[];
}

interface RequestLifecyclePayload {
  requestId: string;
  request: Request;
}

export interface RequestLifecycleEmitter {
  on(event: "request:start", listener: (payload: RequestLifecyclePayload) => void): unknown;
  on(event: "request:end", listener: (payload: RequestLifecyclePayload) => void): unknown;
  on(event: "request:unhandled", listener: (payload: RequestLifecyclePayload) => void): unknown;
  removeListener(event: "request:start", listener: (payload: RequestLifecyclePayload) => void): unknown;
  removeListener(event: "request:end", listener: (payload: RequestLifecyclePayload) => void): unknown;
  removeListener(event: "request:unhandled", listener: (payload: RequestLifecyclePayload) => void): unknown;
}

export interface RequestTally {
  attach(): void;
  detach(): void;
  reset(): void;
  snapshot(): RequestTallySnapshot;
}

export function createRequestTally(emitter: RequestLifecycleEmitter): RequestTally {
  const inFlight = new Map<string, InFlightRequest>();
  const unhandledPaths: string[] = [];

  const onStart = ({ requestId, request }: RequestLifecyclePayload): void => {
    inFlight.set(requestId, {
      requestId,
      method: request.method,
      pathname: new URL(request.url).pathname,
    });
  };
  const onEnd = ({ requestId }: RequestLifecyclePayload): void => {
    inFlight.delete(requestId);
  };
  const onUnhandled = ({ request }: RequestLifecyclePayload): void => {
    unhandledPaths.push(new URL(request.url).pathname);
  };

  return {
    attach() {
      emitter.on("request:start", onStart);
      emitter.on("request:end", onEnd);
      emitter.on("request:unhandled", onUnhandled);
    },
    detach() {
      emitter.removeListener("request:start", onStart);
      emitter.removeListener("request:end", onEnd);
      emitter.removeListener("request:unhandled", onUnhandled);
    },
    reset() {
      inFlight.clear();
      unhandledPaths.length = 0;
    },
    snapshot() {
      return { inFlight: [...inFlight.values()], unhandledPaths: [...unhandledPaths] };
    },
  };
}

export function hasAsyncLeak(snapshot: RequestTallySnapshot): boolean {
  return snapshot.inFlight.length > 0 || snapshot.unhandledPaths.length > 0;
}

export function reportAsyncLeak(snapshot: RequestTallySnapshot, testName: string): void {
  const details: string[] = [];
  if (snapshot.inFlight.length > 0) {
    const requests = snapshot.inFlight.map((r) => `${r.method} ${r.pathname}`).join(", ");
    details.push(`in-flight request(s) crossed the test boundary: ${requests}`);
  }
  if (snapshot.unhandledPaths.length > 0) {
    details.push(`unhandled endpoint(s): ${snapshot.unhandledPaths.join(", ")}`);
  }
  console.warn(`[async-leak] "${testName}": ${details.join("; ")}`);
}

let isolationResetsInstalled = false;

export function installIsolationResets(): void {
  if (isolationResetsInstalled) return;
  isolationResetsInstalled = true;
  const tally = createRequestTally(server.events);
  tally.attach();
  beforeEach(() => {
    tally.reset();
  });
  afterEach(() => {
    vi.useRealTimers();
    queryClient.clear();
    const snapshot = tally.snapshot();
    if (hasAsyncLeak(snapshot)) {
      reportAsyncLeak(snapshot, expect.getState().currentTestName ?? "unknown test");
    }
  });
  afterAll(() => {
    tally.detach();
  });
}
