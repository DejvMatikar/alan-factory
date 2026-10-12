import { act, renderHook } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, StrictMode, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { deferredFetch } from "../../../test/docReadHarness";
import { FakeWebSocket } from "../../../test/fakeWebSocket";
import { server } from "../../../test/server";
import { useOperatorSocket, type OutputChunk } from "./useOperatorSocket";

const TICKET_PATH = "/api/operator/attach-ticket";

type TicketStep = "ok" | "gone" | "fail";

function serveTickets(steps: TicketStep[] = ["ok"]) {
  const tally = { calls: 0 };
  server.use(
    http.post(TICKET_PATH, () => {
      const step = steps[Math.min(tally.calls, steps.length - 1)]!;
      tally.calls += 1;
      if (step === "gone") {
        return HttpResponse.json({ detail: "no operator session" }, { status: 404 });
      }
      if (step === "fail") return HttpResponse.json({ detail: "boom" }, { status: 500 });
      return HttpResponse.json({ ticket: `tk-${String(tally.calls)}`, expires_in_s: 30 });
    }),
  );
  return tally;
}

function serveGatedTicket(status: number) {
  const gate = deferredFetch();
  const tally = { calls: 0, release: gate.release };
  server.use(
    http.post(TICKET_PATH, async () => {
      tally.calls += 1;
      await gate.settled;
      return status === 200
        ? HttpResponse.json({ ticket: "gated", expires_in_s: 30 })
        : HttpResponse.json({ detail: "boom" }, { status });
    }),
  );
  return tally;
}

async function settle(): Promise<void> {
  for (let i = 0; i < 10; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
  await settle();
}

function ticketOf(socket: FakeWebSocket): string | null {
  return new URL(socket.url).searchParams.get("ticket");
}

function renderSocket(sessionKey: string | null = "s1", onGone = vi.fn()) {
  const rendered = renderHook(
    ({ key }: { key: string | null }) => useOperatorSocket(key, onGone),
    { initialProps: { key: sessionKey } },
  );
  return { ...rendered, onGone };
}

async function attach(index = 0): Promise<FakeWebSocket> {
  await settle();
  const socket = FakeWebSocket.instances[index]!;
  act(() => {
    socket.serverOpen();
  });
  return socket;
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

describe("useOperatorSocket — attaching", () => {
  it("fetches a ticket, opens the socket with it and reports attached once it opens", async () => {
    serveTickets();
    const { result } = renderSocket();
    expect(result.current.state).toBe("connecting");
    await settle();
    expect(FakeWebSocket.instances).toHaveLength(1);
    const socket = FakeWebSocket.latest();
    expect(ticketOf(socket)).toBe("tk-1");
    expect(socket.binaryType).toBe("arraybuffer");
    expect(result.current.state).toBe("connecting");
    act(() => {
      socket.serverOpen();
    });
    expect(result.current.state).toBe("attached");
  });

  it("stays idle and requests nothing while there is no session key", async () => {
    const tally = serveTickets();
    const { result } = renderSocket(null);
    await advance(10_000);
    expect(result.current.state).toBe("idle");
    expect(tally.calls).toBe(0);
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("opens a single socket under StrictMode's double subscribe, ignoring the first ticket", async () => {
    const tally = serveTickets();
    const wrapper = ({ children }: { children: ReactNode }) => createElement(StrictMode, null, children);
    renderHook(() => useOperatorSocket("s1", vi.fn()), { wrapper });
    await settle();
    expect(tally.calls).toBe(2);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("restarts on a new session key: closes the old socket and attaches to the new session", async () => {
    serveTickets();
    const { result, rerender } = renderSocket("s1");
    const first = await attach();
    expect(result.current.state).toBe("attached");
    rerender({ key: "s2" });
    expect(first.closeCalls).toBe(1);
    expect(result.current.state).toBe("connecting");
    await settle();
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(ticketOf(FakeWebSocket.latest())).toBe("tk-2");
  });
});

describe("useOperatorSocket — close codes", () => {
  it("maps 4001 to superseded and never reconnects", async () => {
    const tally = serveTickets();
    const { result } = renderSocket();
    const socket = await attach();
    act(() => {
      socket.serverClose(4001);
    });
    expect(result.current.state).toBe("superseded");
    await advance(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(tally.calls).toBe(1);
  });

  it("maps 4002 to ended, refetches the status once, and never reconnects", async () => {
    const tally = serveTickets();
    const { result, onGone } = renderSocket();
    const socket = await attach();
    act(() => {
      socket.serverClose(4002);
    });
    expect(result.current.state).toBe("ended");
    expect(onGone).toHaveBeenCalledTimes(1);
    await advance(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(tally.calls).toBe(1);
  });

  it("maps 4404 to a status refetch and never reconnects", async () => {
    const tally = serveTickets();
    const { result, onGone } = renderSocket();
    const socket = await attach();
    act(() => {
      socket.serverClose(4404);
    });
    expect(result.current.state).toBe("gone");
    expect(onGone).toHaveBeenCalledTimes(1);
    await advance(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(tally.calls).toBe(1);
  });

  it("retries at once with a fresh ticket on 4401 and attaches when the retry is accepted", async () => {
    const tally = serveTickets();
    const { result } = renderSocket();
    const first = await attach();
    act(() => {
      first.serverClose(4401);
    });
    expect(result.current.state).toBe("connecting");
    await settle();
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(ticketOf(FakeWebSocket.latest())).toBe("tk-2");
    expect(tally.calls).toBe(2);
    act(() => {
      FakeWebSocket.latest().serverOpen();
    });
    expect(result.current.state).toBe("attached");
  });

  it("reports refused, and stops asking for tickets, when the fresh-ticket retry is rejected too", async () => {
    const tally = serveTickets();
    const { result } = renderSocket();
    await settle();
    act(() => {
      FakeWebSocket.latest().serverClose(4401);
    });
    await settle();
    act(() => {
      FakeWebSocket.latest().serverClose(4401);
    });
    await advance(60_000);
    expect(result.current.state).toBe("refused");
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(tally.calls).toBe(2);
  });

  it("forgives an earlier 4401 once a retry attaches", async () => {
    serveTickets();
    const { result } = renderSocket();
    await settle();
    act(() => {
      FakeWebSocket.latest().serverClose(4401);
    });
    await settle();
    act(() => {
      FakeWebSocket.latest().serverOpen();
    });
    act(() => {
      FakeWebSocket.latest().serverClose(4401);
    });
    await settle();
    expect(result.current.state).toBe("connecting");
    expect(FakeWebSocket.instances).toHaveLength(3);
  });
});

describe("useOperatorSocket — a handshake that never opens", () => {
  it("reattaches once, then reports refused and stops asking for tickets when a second socket closes 1006 without opening", async () => {
    const tally = serveTickets();
    const { result } = renderSocket();
    await settle();
    act(() => {
      FakeWebSocket.latest().serverClose(1006);
    });
    expect(result.current.state).toBe("reattaching");
    await advance(500);
    expect(FakeWebSocket.instances).toHaveLength(2);
    act(() => {
      FakeWebSocket.latest().serverClose(1006);
    });
    expect(result.current.state).toBe("refused");
    await advance(60_000);
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(tally.calls).toBe(2);
  });

  it("counts an unopened 1006 together with a 4401 toward refusing", async () => {
    serveTickets();
    const { result } = renderSocket();
    await settle();
    act(() => {
      FakeWebSocket.latest().serverClose(4401);
    });
    await settle();
    act(() => {
      FakeWebSocket.latest().serverClose(1006);
    });
    expect(result.current.state).toBe("refused");
  });

  it("forgives an unopened 1006 once a later handshake opens", async () => {
    serveTickets();
    const { result } = renderSocket();
    await settle();
    act(() => {
      FakeWebSocket.latest().serverClose(1006);
    });
    await advance(500);
    act(() => {
      FakeWebSocket.latest().serverOpen();
    });
    act(() => {
      FakeWebSocket.latest().serverClose(1006);
    });
    await advance(500);
    act(() => {
      FakeWebSocket.latest().serverClose(1006);
    });
    expect(result.current.state).toBe("reattaching");
    await advance(1000);
    expect(FakeWebSocket.instances).toHaveLength(4);
  });
});

describe("useOperatorSocket — reattaching", () => {
  it("maps 1006 to reattaching and reattaches with a fresh ticket after the first delay", async () => {
    const tally = serveTickets();
    const { result } = renderSocket();
    const first = await attach();
    act(() => {
      first.serverClose(1006);
    });
    expect(result.current.state).toBe("reattaching");
    await advance(499);
    expect(FakeWebSocket.instances).toHaveLength(1);
    await advance(1);
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(ticketOf(FakeWebSocket.latest())).toBe("tk-2");
    expect(tally.calls).toBe(2);
    act(() => {
      FakeWebSocket.latest().serverOpen();
    });
    expect(result.current.state).toBe("attached");
  });

  it("backs off through 500, 1000, 2000, 4000 and 8000 ms and holds at 8000 while tickets keep failing", async () => {
    const tally = serveTickets(["fail"]);
    renderSocket();
    await settle();
    for (const delay of [500, 1000, 2000, 4000, 8000, 8000]) {
      const before = tally.calls;
      await advance(delay - 1);
      expect(tally.calls).toBe(before);
      await advance(1);
      expect(tally.calls).toBe(before + 1);
    }
  });

  it("restarts the backoff once a reattach opens", async () => {
    serveTickets(["fail", "fail", "ok"]);
    renderSocket();
    await settle();
    await advance(500);
    await advance(1000);
    act(() => {
      FakeWebSocket.latest().serverOpen();
    });
    act(() => {
      FakeWebSocket.latest().serverClose(1006);
    });
    await advance(500);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it.each([1000, 1001, 1005, 1011])("treats close code %i as a lost connection to reattach", async (code) => {
    serveTickets();
    const { result } = renderSocket();
    const socket = await attach();
    act(() => {
      socket.serverClose(code);
    });
    expect(result.current.state).toBe("reattaching");
  });

  it("reports gone and refetches the status when the ticket request says no session is live", async () => {
    serveTickets(["gone"]);
    const { result, onGone } = renderSocket();
    await settle();
    expect(result.current.state).toBe("gone");
    expect(onGone).toHaveBeenCalledTimes(1);
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("reattaches after a delay when the ticket request fails for any other reason", async () => {
    const tally = serveTickets(["fail", "ok"]);
    const { result } = renderSocket();
    await settle();
    expect(result.current.state).toBe("reattaching");
    expect(FakeWebSocket.instances).toHaveLength(0);
    await advance(500);
    expect(tally.calls).toBe(2);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("reattaches after a delay when the ticket request cannot reach the service", async () => {
    let calls = 0;
    server.use(
      http.post(TICKET_PATH, () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.error()
          : HttpResponse.json({ ticket: "tk-net", expires_in_s: 30 });
      }),
    );
    const { result } = renderSocket();
    await settle();
    expect(result.current.state).toBe("reattaching");
    await advance(500);
    expect(ticketOf(FakeWebSocket.latest())).toBe("tk-net");
  });
});

describe("useOperatorSocket — teardown", () => {
  it("closes the socket and detaches its handlers on unmount, ignoring later close events", async () => {
    serveTickets();
    const { unmount, onGone } = renderSocket();
    const socket = await attach();
    unmount();
    expect(socket.closeCalls).toBe(1);
    expect(socket.onclose).toBeNull();
    expect(socket.onopen).toBeNull();
    expect(socket.onmessage).toBeNull();
    expect(socket.onerror).toBeNull();
    await advance(60_000);
    expect(onGone).not.toHaveBeenCalled();
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("cancels a pending reattach on unmount", async () => {
    const tally = serveTickets();
    const { unmount } = renderSocket();
    const socket = await attach();
    act(() => {
      socket.serverClose(1006);
    });
    unmount();
    await advance(60_000);
    expect(tally.calls).toBe(1);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("opens no socket when the component unmounts before the ticket arrives", async () => {
    const tally = serveGatedTicket(200);
    const { unmount } = renderSocket();
    await settle();
    unmount();
    tally.release();
    await settle();
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("schedules nothing when a ticket failure arrives after unmount", async () => {
    const tally = serveGatedTicket(500);
    const { unmount } = renderSocket();
    await settle();
    unmount();
    tally.release();
    await advance(60_000);
    expect(tally.calls).toBe(1);
    expect(FakeWebSocket.instances).toHaveLength(0);
  });
});

describe("useOperatorSocket — output", () => {
  it("delivers binary frames as bytes and text frames as strings to the bound sink", async () => {
    serveTickets();
    const { result } = renderSocket();
    const socket = await attach();
    const received: OutputChunk[] = [];
    act(() => {
      result.current.port.bindOutput((chunk) => received.push(chunk));
    });
    socket.serverSend(new Uint8Array([104, 105]).buffer);
    socket.serverSend("plain");
    expect(received.map((chunk) => (typeof chunk === "string" ? chunk : Array.from(chunk)))).toEqual([
      [104, 105],
      "plain",
    ]);
  });

  it("holds output that arrives before a sink is bound and flushes it in order on bind", async () => {
    serveTickets();
    const { result } = renderSocket();
    const socket = await attach();
    socket.serverSend("one");
    socket.serverSend("two");
    const received: OutputChunk[] = [];
    result.current.port.bindOutput((chunk) => received.push(chunk));
    socket.serverSend("three");
    expect(received).toEqual(["one", "two", "three"]);
  });

  it("keeps only the newest 1024 chunks when no sink is ever bound", async () => {
    serveTickets();
    const { result } = renderSocket();
    const socket = await attach();
    for (let i = 0; i < 1030; i += 1) socket.serverSend(`chunk-${String(i)}`);
    const received: OutputChunk[] = [];
    result.current.port.bindOutput((chunk) => received.push(chunk));
    expect(received).toHaveLength(1024);
    expect(received[0]).toBe("chunk-6");
    expect(received.at(-1)).toBe("chunk-1029");
  });

  it("stops delivering after the sink is unbound", async () => {
    serveTickets();
    const { result } = renderSocket();
    const socket = await attach();
    const received: OutputChunk[] = [];
    const unbind = result.current.port.bindOutput((chunk) => received.push(chunk));
    socket.serverSend("kept");
    unbind();
    socket.serverSend("dropped");
    expect(received).toEqual(["kept"]);
  });

  it("keeps a newer sink bound when an older sink's unbind runs late", async () => {
    serveTickets();
    const { result } = renderSocket();
    const socket = await attach();
    const older: OutputChunk[] = [];
    const newer: OutputChunk[] = [];
    const unbindOlder = result.current.port.bindOutput((chunk) => older.push(chunk));
    result.current.port.bindOutput((chunk) => newer.push(chunk));
    unbindOlder();
    socket.serverSend("still delivered");
    expect(older).toEqual([]);
    expect(newer).toEqual(["still delivered"]);
  });
});

describe("useOperatorSocket — input and size", () => {
  it("sends keystrokes as UTF-8 binary frames and binary input as raw bytes once attached", async () => {
    serveTickets();
    const { result } = renderSocket();
    const socket = await attach();
    result.current.port.send("ls\r");
    result.current.port.send("é");
    result.current.port.sendBinary("\u0000ÿ");
    expect(socket.sentBytes()).toEqual([[108, 115, 13], [195, 169], [0, 255]]);
    expect(socket.sentText()).toEqual([]);
  });

  it("sends a resize as a JSON text frame once attached", async () => {
    serveTickets();
    const { result } = renderSocket();
    const socket = await attach();
    result.current.port.resize(100, 30);
    expect(socket.sentText()).toEqual(['{"type":"resize","cols":100,"rows":30}']);
  });

  it("drops input and resizes while not attached", async () => {
    serveTickets();
    const { result } = renderSocket();
    await settle();
    const socket = FakeWebSocket.latest();
    result.current.port.send("x");
    result.current.port.sendBinary("x");
    result.current.port.resize(100, 30);
    expect(socket.sent).toEqual([]);
    act(() => {
      socket.serverOpen();
    });
    act(() => {
      socket.serverClose(1006);
    });
    result.current.port.send("y");
    expect(socket.sentBytes()).toEqual([]);
    expect(socket.sentText()).toEqual(['{"type":"resize","cols":100,"rows":30}']);
  });

  it("sends the size recorded while connecting as soon as the socket opens", async () => {
    serveTickets();
    const { result } = renderSocket();
    await settle();
    const socket = FakeWebSocket.latest();
    act(() => {
      result.current.port.resize(132, 43);
    });
    expect(socket.sent).toEqual([]);
    act(() => {
      socket.serverOpen();
    });
    expect(socket.sentText()).toEqual(['{"type":"resize","cols":132,"rows":43}']);
  });

  it("sends nothing on open when no size was ever recorded", async () => {
    serveTickets();
    renderSocket();
    const socket = await attach();
    expect(socket.sent).toEqual([]);
  });

  it("puts a size recorded before connecting into the first socket's URL", async () => {
    serveTickets();
    const { result } = renderSocket();
    act(() => {
      result.current.port.resize(120, 40);
    });
    await settle();
    const url = new URL(FakeWebSocket.latest().url);
    expect(url.searchParams.get("cols")).toBe("120");
    expect(url.searchParams.get("rows")).toBe("40");
  });

  it("reattaches at the most recent size", async () => {
    serveTickets();
    const { result } = renderSocket();
    const socket = await attach();
    act(() => {
      result.current.port.resize(90, 20);
    });
    act(() => {
      socket.serverClose(1006);
    });
    await advance(500);
    const url = new URL(FakeWebSocket.latest().url);
    expect(url.searchParams.get("cols")).toBe("90");
    expect(url.searchParams.get("rows")).toBe("20");
  });
});
