import type { OperatorStatus } from "../../api/operator";
import type { SocketState } from "./hooks/useOperatorSocket";
import {
  STATUS_UNREACHABLE,
  STATUS_UNREACHABLE_DETAIL,
  TICKET_REFUSED,
  TICKET_REFUSED_DETAIL,
} from "./operatorCopy";

export type OperatorPhase =
  | { readonly kind: "phone" }
  | { readonly kind: "loading" }
  | { readonly kind: "choose" }
  | { readonly kind: "connecting" }
  | { readonly kind: "attached" }
  | { readonly kind: "reattaching" }
  | { readonly kind: "ended" }
  | { readonly kind: "superseded" }
  | { readonly kind: "unavailable"; readonly reason: string; readonly detail: string };

export interface PhaseInputs {
  readonly wide: boolean;
  readonly status: OperatorStatus | undefined;
  readonly statusFailed: boolean;
  readonly socket: SocketState;
  readonly endedByUser: boolean;
}

const TERMINAL_PHASES: ReadonlySet<OperatorPhase["kind"]> = new Set([
  "connecting",
  "attached",
  "reattaching",
]);

const CONTROLS_ONLY_PHASES: ReadonlySet<OperatorPhase["kind"]> = new Set([
  "superseded",
  "unavailable",
]);

export function showsTerminal(phase: OperatorPhase): boolean {
  return TERMINAL_PHASES.has(phase.kind);
}

export function showsControls(phase: OperatorPhase): boolean {
  return showsTerminal(phase) || CONTROLS_ONLY_PHASES.has(phase.kind);
}

function unavailable(reason: string, detail: string): OperatorPhase {
  return { kind: "unavailable", reason, detail };
}

const LOADING: OperatorPhase = { kind: "loading" };

function statusUnreachable(): OperatorPhase {
  return unavailable(STATUS_UNREACHABLE, STATUS_UNREACHABLE_DETAIL);
}

function withLiveSession(socket: SocketState): OperatorPhase {
  switch (socket) {
    case "attached":
      return { kind: "attached" };
    case "reattaching":
      return { kind: "reattaching" };
    case "ended":
      return { kind: "ended" };
    case "superseded":
      return { kind: "superseded" };
    case "refused":
      return unavailable(TICKET_REFUSED, TICKET_REFUSED_DETAIL);
    default:
      return { kind: "connecting" };
  }
}

function withoutSession(status: OperatorStatus, socket: SocketState, endedByUser: boolean): OperatorPhase {
  if (endedByUser || socket === "ended") return { kind: "ended" };
  if (status.unavailable !== null) {
    return unavailable(status.unavailable.reason, status.unavailable.detail);
  }
  return { kind: "choose" };
}

export function derivePhase(input: PhaseInputs): OperatorPhase {
  if (!input.wide) return { kind: "phone" };
  if (input.status === undefined) return input.statusFailed ? statusUnreachable() : LOADING;
  if (input.status.session !== null) return withLiveSession(input.socket);
  return withoutSession(input.status, input.socket, input.endedByUser);
}
