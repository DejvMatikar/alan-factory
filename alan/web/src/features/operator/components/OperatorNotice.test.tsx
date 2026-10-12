import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { OperatorPhase } from "../operatorPhase";
import OperatorNotice, { UpdateWarning } from "./OperatorNotice";

function renderNotice(phase: OperatorPhase, onStart = vi.fn()) {
  render(<OperatorNotice phase={phase} onStart={onStart} />);
  return onStart;
}

describe("OperatorNotice", () => {
  it.each([
    ["loading", "Checking for a running session…"],
    ["connecting", "Connecting to the session…"],
    ["attached", "Attached to the running session."],
    ["reattaching", "Connection lost. Reattaching to the session…"],
    [
      "superseded",
      "This session was taken over by another browser window. Close this page and open it again to attach here.",
    ],
  ] as const)("says %s as a status message with no start button", (kind, text) => {
    renderNotice({ kind });
    expect(screen.getByRole("status")).toHaveTextContent(text);
    expect(screen.getByTestId(`operator-notice-${kind}`)).toBeInTheDocument();
    expect(screen.queryByTestId("operator-start")).not.toBeInTheDocument();
  });

  it.each([
    ["choose", "No operator session is running."],
    ["ended", "The session has ended."],
  ] as const)("offers to start a session once it is %s", async (kind, text) => {
    const onStart = renderNotice({ kind });
    expect(screen.getByRole("status")).toHaveTextContent(text);
    await userEvent.setup().click(screen.getByTestId("operator-start"));
    expect(onStart).toHaveBeenCalledTimes(1);
  });

  it.each([{ kind: "attached" }, { kind: "phone" }] as const)(
    "announces the %j notice through a native output element",
    (phase) => {
      renderNotice(phase);
      expect(screen.getByRole("status").tagName).toBe("OUTPUT");
    },
  );

  it("announces the update warning through a native output element", () => {
    render(<UpdateWarning warning="fetch failed" />);
    expect(screen.getByRole("status").tagName).toBe("OUTPUT");
  });

  it("asks for a larger screen at phone width instead of showing a terminal", () => {
    renderNotice({ kind: "phone" });
    expect(screen.getByTestId("operator-phone-notice")).toHaveTextContent(
      "The operator terminal needs a larger screen. Open this page on a tablet or desktop browser.",
    );
  });

  it.each([
    ["detached_mode_off", "Detached worker mode is off"],
    ["tmux_missing", "tmux is not installed"],
    ["claude_missing", "The claude CLI is not installed"],
    ["git_missing", "git is not installed"],
    ["checkout_clone_failed", "The operator checkout could not be cloned"],
    ["status_unreachable", "The service did not answer the status request"],
    ["ticket_refused", "The terminal connection was refused"],
  ])("names the %s cause and shows the server's detail", (reason, label) => {
    renderNotice({ kind: "unavailable", reason, detail: `detail for ${reason}` });
    expect(screen.getByRole("alert")).toHaveTextContent("Operator terminal unavailable");
    expect(screen.getByTestId("operator-unavailable-cause")).toHaveTextContent(label);
    expect(screen.getByTestId("operator-unavailable-detail")).toHaveTextContent(`detail for ${reason}`);
  });

  it("falls back to the raw reason when the server names one this build does not know", () => {
    renderNotice({ kind: "unavailable", reason: "future_reason", detail: "something new" });
    expect(screen.getByTestId("operator-unavailable-cause")).toHaveTextContent("future_reason");
  });

  it("shows no terminal-shaped element in any notice", () => {
    renderNotice({ kind: "unavailable", reason: "git_missing", detail: "no git" });
    expect(screen.queryByTestId("operator-terminal")).not.toBeInTheDocument();
  });
});

describe("UpdateWarning", () => {
  it("says the checkout update failed and that the session still started", () => {
    render(<UpdateWarning warning="fetch failed: network unreachable" />);
    expect(screen.getByTestId("operator-update-warning")).toHaveTextContent(
      "The operator checkout could not be brought up to date, so the session started with the checkout as it was: fetch failed: network unreachable",
    );
  });
});
