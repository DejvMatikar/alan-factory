import { useCallback, useState, useSyncExternalStore } from "react";

import { ApiError } from "../../../api";
import { fetchAttachTicket, operatorSocketUrl, type TerminalSize } from "../../../api/operator";
import {
  CLOSE_BAD_TICKET,
  CLOSE_ENDED,
  CLOSE_NO_SESSION,
  CLOSE_SUPERSEDED,
  encodeResize,
} from "../operatorProtocol";

export type SocketState =
  | "idle"
  | "connecting"
  | "attached"
  | "reattaching"
  | "ended"
  | "superseded"
  | "gone"
  | "refused";

export type OutputChunk = string | Uint8Array;
export type OutputSink = (chunk: OutputChunk) => void;

export interface OperatorPort {
  readonly send: (data: string) => void;
  readonly sendBinary: (data: string) => void;
  readonly resize: (cols: number, rows: number) => void;
  readonly bindOutput: (sink: OutputSink) => () => void;
}

export interface OperatorSocket {
  readonly state: SocketState;
  readonly port: OperatorPort;
}

interface Connection {
  readonly port: OperatorPort;
  readonly getState: () => SocketState;
  readonly subscribe: (active: boolean, onGone: () => void, listener: () => void) => () => void;
}

const REATTACH_DELAYS_MS = [500, 1000, 2000, 4000, 8000] as const;
const MAX_PENDING_CHUNKS = 1024;
const NOT_NOTIFIED = (): void => undefined;
const encoder = new TextEncoder();

function createConnection(): Connection {
  let state: SocketState = "idle";
  let notify = NOT_NOTIFIED;
  let onGone = NOT_NOTIFIED;
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let epoch = 0;
  let attempts = 0;
  let refusals = 0;
  let size: TerminalSize | null = null;
  let sink: OutputSink | null = null;
  const pending: OutputChunk[] = [];

  function setState(next: SocketState): void {
    state = next;
    notify();
  }

  function clearTimer(): void {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  }

  function closeSocket(): void {
    if (socket === null) return;
    socket.onopen = null;
    socket.onmessage = null;
    socket.onclose = null;
    socket.onerror = null;
    socket.close();
    socket = null;
  }

  function deliver(data: string | ArrayBuffer): void {
    const chunk = typeof data === "string" ? data : new Uint8Array(data);
    if (sink !== null) {
      sink(chunk);
      return;
    }
    pending.push(chunk);
    if (pending.length > MAX_PENDING_CHUNKS) pending.shift();
  }

  function markGone(): void {
    setState("gone");
    onGone();
  }

  function scheduleReattach(): void {
    setState("reattaching");
    const delay = REATTACH_DELAYS_MS[Math.min(attempts, REATTACH_DELAYS_MS.length - 1)]!;
    attempts += 1;
    timer = setTimeout(() => {
      timer = null;
      void openSession(epoch);
    }, delay);
  }

  function retryNow(): void {
    setState("connecting");
    void openSession(epoch);
  }

  function retryOrRefuse(retry: () => void): void {
    refusals += 1;
    if (refusals > 1) {
      setState("refused");
      return;
    }
    retry();
  }

  function handleClose(code: number, opened: boolean): void {
    socket = null;
    switch (code) {
      case CLOSE_SUPERSEDED:
        setState("superseded");
        return;
      case CLOSE_ENDED:
        setState("ended");
        onGone();
        return;
      case CLOSE_NO_SESSION:
        markGone();
        return;
      case CLOSE_BAD_TICKET:
        retryOrRefuse(retryNow);
        return;
      default:
        if (opened) scheduleReattach();
        else retryOrRefuse(scheduleReattach);
    }
  }

  function handleOpen(): void {
    attempts = 0;
    refusals = 0;
    setState("attached");
    if (size !== null) liveSocket()?.send(encodeResize(size.cols, size.rows));
  }

  function connect(ticket: string): void {
    const ws = new WebSocket(operatorSocketUrl(ticket, size));
    let opened = false;
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      opened = true;
      handleOpen();
    };
    ws.onmessage = (event: MessageEvent<string | ArrayBuffer>) => {
      deliver(event.data);
    };
    ws.onclose = (event: CloseEvent) => {
      handleClose(event.code, opened);
    };
    socket = ws;
  }

  function handleTicketFailure(error: unknown): void {
    if (error instanceof ApiError && error.status === 404) markGone();
    else scheduleReattach();
  }

  async function openSession(runEpoch: number): Promise<void> {
    let ticket: string;
    try {
      ticket = await fetchAttachTicket();
    } catch (error) {
      if (runEpoch === epoch) handleTicketFailure(error);
      return;
    }
    if (runEpoch === epoch) connect(ticket);
  }

  function begin(): void {
    epoch += 1;
    attempts = 0;
    refusals = 0;
    setState("connecting");
    void openSession(epoch);
  }

  function halt(): void {
    epoch += 1;
    clearTimer();
    closeSocket();
  }

  function liveSocket(): WebSocket | null {
    return state === "attached" ? socket : null;
  }

  const port: OperatorPort = {
    send: (data) => {
      liveSocket()?.send(encoder.encode(data));
    },
    sendBinary: (data) => {
      liveSocket()?.send(Uint8Array.from(data, (char) => char.codePointAt(0)!));
    },
    resize: (cols, rows) => {
      size = { cols, rows };
      liveSocket()?.send(encodeResize(cols, rows));
    },
    bindOutput: (next) => {
      sink = next;
      for (const chunk of pending.splice(0)) next(chunk);
      return () => {
        if (sink === next) sink = null;
      };
    },
  };

  return {
    port,
    getState: () => state,
    subscribe: (active, gone, listener) => {
      if (!active) return NOT_NOTIFIED;
      notify = listener;
      onGone = gone;
      begin();
      return () => {
        halt();
        notify = NOT_NOTIFIED;
        onGone = NOT_NOTIFIED;
      };
    },
  };
}

export function useOperatorSocket(
  sessionKey: string | null,
  onSessionGone: () => void,
): OperatorSocket {
  const [connection] = useState(createConnection);
  const subscribe = useCallback(
    (listener: () => void) => connection.subscribe(sessionKey !== null, onSessionGone, listener),
    [connection, sessionKey, onSessionGone],
  );
  const state = useSyncExternalStore(subscribe, connection.getState);
  return { state, port: connection.port };
}
