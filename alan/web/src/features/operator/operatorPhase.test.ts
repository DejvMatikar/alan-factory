import { describe, expect, it } from "vitest";

import type { OperatorStatus } from "../../api/operator";
import type { SocketState } from "./hooks/useOperatorSocket";
import {
  derivePhase,
  showsControls,
  showsTerminal,
  type OperatorPhase,
  type PhaseInputs,
} from "./operatorPhase";

const SESSION: NonNullable<OperatorStatus["session"]> = {
  scope: "fleet",
  target: null,
  started_at: "2026-09-29T10:00:00Z",
  update_warning: null,
  exited: false,
};

const LIVE: OperatorStatus = { session: SESSION, unavailable: null };
const NONE: OperatorStatus = { session: null, unavailable: null };
const BLOCKED: OperatorStatus = {
  session: null,
  unavailable: { reason: "tmux_missing", detail: "tmux is not installed." },
};

function inputs(overrides: Partial<PhaseInputs>): PhaseInputs {
  return {
    wide: true,
    status: LIVE,
    statusFailed: false,
    socket: "idle",
    endedByUser: false,
    ...overrides,
  };
}

describe("derivePhase — while a session is live", () => {
  const cases: readonly [SocketState, OperatorPhase][] = [
    ["idle", { kind: "connecting" }],
    ["connecting", { kind: "connecting" }],
    ["gone", { kind: "connecting" }],
    ["attached", { kind: "attached" }],
    ["reattaching", { kind: "reattaching" }],
    ["ended", { kind: "ended" }],
    ["superseded", { kind: "superseded" }],
    [
      "refused",
      {
        kind: "unavailable",
        reason: "ticket_refused",
        detail: "The service refused the connection ticket twice in a row. Reload the page to try again.",
      },
    ],
  ];

  it.each(cases)("maps the %s socket to %j", (socket, expected) => {
    expect(derivePhase(inputs({ socket }))).toEqual(expected);
  });
});

describe("derivePhase — without a live session", () => {
  it("asks for a larger screen at phone width before anything else", () => {
    expect(derivePhase(inputs({ wide: false, status: BLOCKED }))).toEqual({ kind: "phone" });
  });

  it("waits while the status has not loaded", () => {
    expect(derivePhase(inputs({ status: undefined }))).toEqual({ kind: "loading" });
  });

  it("names an unreachable service when the status request failed", () => {
    expect(derivePhase(inputs({ status: undefined, statusFailed: true }))).toEqual({
      kind: "unavailable",
      reason: "status_unreachable",
      detail: "The service did not answer the request for the operator terminal's status.",
    });
  });

  it("offers the scope picker when nothing is running and nothing is missing", () => {
    expect(derivePhase(inputs({ status: NONE }))).toEqual({ kind: "choose" });
  });

  it("reports the server's exact unavailable reason", () => {
    expect(derivePhase(inputs({ status: BLOCKED }))).toEqual({
      kind: "unavailable",
      reason: "tmux_missing",
      detail: "tmux is not installed.",
    });
  });

  it("reports ended once the user ended the session, ahead of the unavailable reason", () => {
    expect(derivePhase(inputs({ status: BLOCKED, endedByUser: true }))).toEqual({ kind: "ended" });
  });

  it("keeps reporting ended when the socket saw the session end and the status has caught up", () => {
    expect(derivePhase(inputs({ status: NONE, socket: "ended" }))).toEqual({ kind: "ended" });
  });

  it("prefers a live session over a stale ended-by-user flag", () => {
    expect(derivePhase(inputs({ socket: "attached", endedByUser: true }))).toEqual({
      kind: "attached",
    });
  });
});

describe("showsTerminal", () => {
  it.each(["connecting", "attached", "reattaching"] as const)("shows the terminal while %s", (kind) => {
    expect(showsTerminal({ kind })).toBe(true);
  });

  it.each(["phone", "loading", "choose", "ended", "superseded"] as const)(
    "hides the terminal while %s",
    (kind) => {
      expect(showsTerminal({ kind })).toBe(false);
    },
  );

  it("hides the terminal while unavailable", () => {
    expect(showsTerminal({ kind: "unavailable", reason: "git_missing", detail: "no git" })).toBe(false);
  });
});

describe("showsControls", () => {
  it.each(["connecting", "attached", "reattaching", "superseded"] as const)(
    "shows the session controls while %s",
    (kind) => {
      expect(showsControls({ kind })).toBe(true);
    },
  );

  it("shows the session controls while unavailable", () => {
    expect(showsControls({ kind: "unavailable", reason: "ticket_refused", detail: "refused" })).toBe(true);
  });

  it.each(["phone", "loading", "choose", "ended"] as const)(
    "hides the session controls while %s",
    (kind) => {
      expect(showsControls({ kind })).toBe(false);
    },
  );
});
