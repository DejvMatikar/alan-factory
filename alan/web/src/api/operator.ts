import { createApiClient } from "./client";
import type { components } from "./schema";
import { unwrap } from "./unwrap";

export type OperatorScope = components["schemas"]["Scope"];
export type OperatorStatus = components["schemas"]["OperatorStatusOut"];
export type OperatorSession = components["schemas"]["OperatorSessionOut"];
export type OperatorStartBody = components["schemas"]["OperatorStartBody"];

export interface TerminalSize {
  readonly cols: number;
  readonly rows: number;
}

const TERMINAL_PATH = "/api/operator/terminal";

const client = createApiClient({
  baseUrl: globalThis.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function getOperatorStatus(): Promise<OperatorStatus> {
  return unwrap(await client.GET("/api/operator/status"));
}

export async function startOperatorSession(body: OperatorStartBody): Promise<OperatorSession> {
  return unwrap(await client.POST("/api/operator/session", { body }));
}

export async function endOperatorSession(): Promise<void> {
  unwrap(await client.DELETE("/api/operator/session"));
}

export async function fetchAttachTicket(): Promise<string> {
  return unwrap(await client.POST("/api/operator/attach-ticket")).ticket;
}

export function operatorSocketUrl(ticket: string, size: TerminalSize | null): string {
  const url = new URL(TERMINAL_PATH, globalThis.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("ticket", ticket);
  if (size !== null) {
    url.searchParams.set("cols", String(size.cols));
    url.searchParams.set("rows", String(size.rows));
  }
  return url.toString();
}
