import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ConnectUnboundNotice, { readConnectUnboundState } from "./ConnectUnboundNotice";

describe("readConnectUnboundState", () => {
  it("returns null for null", () => {
    expect(readConnectUnboundState(null)).toBeNull();
  });

  it("returns null for undefined", () => {
    expect(readConnectUnboundState(undefined)).toBeNull();
  });

  it("returns null for a non-object primitive", () => {
    expect(readConnectUnboundState("preflight_blocked")).toBeNull();
  });

  it("returns null for a malformed object with no connectUnboundReason field", () => {
    expect(readConnectUnboundState({ somethingElse: true })).toBeNull();
  });

  it("returns null for an unknown reason string", () => {
    expect(readConnectUnboundState({ connectUnboundReason: "not-a-real-reason" })).toBeNull();
  });

  it("returns null for a non-string connectUnboundReason", () => {
    expect(readConnectUnboundState({ connectUnboundReason: 42 })).toBeNull();
  });

  it.each(["preflight_blocked", "preflight_unknown", "bind_rejected", "bind_conflict"] as const)(
    "returns %s when it is the connectUnboundReason",
    (reason) => {
      expect(readConnectUnboundState({ connectUnboundReason: reason })).toBe(reason);
    },
  );
});

describe("ConnectUnboundNotice", () => {
  it.each([
    ["preflight_blocked", /currently blocked/],
    ["preflight_unknown", /hasn't passed/],
    ["bind_rejected", /wasn't passing at the moment/],
    ["bind_conflict", /workspaces in flight/],
  ] as const)("renders distinct copy for %s", (reason, expected) => {
    render(<ConnectUnboundNotice reason={reason} />);
    expect(screen.getByTestId("connect-unbound-notice-message")).toHaveTextContent(expected);
  });
});
