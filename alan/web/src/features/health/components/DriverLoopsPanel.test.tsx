import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import type { DriverLoopDecision, DriverLoopStatus } from "../../../api";
import { clearControlToken, writeControlToken } from "../../../controlToken";
import { server } from "../../../test/server";
import DriverLoopsPanel, { loopStateText, loopStateTone } from "./DriverLoopsPanel";

function loop(overrides: Partial<DriverLoopStatus> = {}): DriverLoopStatus {
  return {
    name: "alan-issue-driver",
    enabled: true,
    dedicated_clone: true,
    paused: false,
    decision: { action: "idle", reason: null, detail: null, next_wake_at: 2_000_000_000 },
    last_outcome: null,
    final_message: "",
    denial_count: 0,
    run_now_pending: false,
    ...overrides,
  };
}

function mockDriverLoops(supervised: boolean, loops: DriverLoopStatus[]): void {
  server.use(
    http.get("/api/driver-loops", () => HttpResponse.json({ supervised, loops })),
  );
}

describe("DriverLoopsPanel", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders the title", () => {
    mockDriverLoops(true, []);
    render(<DriverLoopsPanel />);
    expect(screen.getByRole("heading", { name: "Driver loops" })).toBeInTheDocument();
  });

  it("shows a loading state before the fetch resolves", () => {
    server.use(http.get("/api/driver-loops", () => new Promise(() => undefined)));
    render(<DriverLoopsPanel />);
    expect(screen.getByTestId("driver-loops-loading")).toBeInTheDocument();
  });

  it("shows an error state when the fetch fails", async () => {
    server.use(http.get("/api/driver-loops", () => HttpResponse.error()));
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loops-error")).toBeInTheDocument();
    });
  });

  it("shows an empty state when supervised with zero loops configured", async () => {
    mockDriverLoops(true, []);
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loops-empty")).toHaveTextContent(
        "No driver loops configured.",
      );
    });
  });

  it("explains the empty state as intentional when supervised with zero loops configured", async () => {
    mockDriverLoops(true, []);
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loops-empty")).toHaveTextContent(
        "The drivers run interactively today; enabling a headless loop waits on the open write-gap gate.",
      );
    });
  });

  it("shows the not-supervised copy, not the supervised explanatory note, when not supervised with zero loops", async () => {
    mockDriverLoops(false, []);
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loops-empty")).toHaveTextContent(
        "Not supervised on this instance — driver loops run only in production.",
      );
    });
    expect(screen.getByTestId("driver-loops-empty")).not.toHaveTextContent(
      "enabling a headless loop waits",
    );
  });

  it("renders no pause, resume, or run-now controls in the supervised empty state", async () => {
    mockDriverLoops(true, []);
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loops-empty")).toBeInTheDocument();
    });
    expect(screen.queryByTestId(/^driver-loop-pause-resume-/)).toBeNull();
    expect(screen.queryByTestId(/^driver-loop-run-now-/)).toBeNull();
  });

  it("renders the supervised empty state with no alert role, no aria-hidden, and no alert/warning class", async () => {
    mockDriverLoops(true, []);
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loops-empty")).toBeInTheDocument();
    });
    const empty = screen.getByTestId("driver-loops-empty");
    expect(empty).not.toHaveAttribute("role", "alert");
    expect(empty).not.toHaveAttribute("aria-hidden");
    expect(empty.className).not.toMatch(/alert|warning|danger|destroy/);
  });

  it("shows a not-supervised notice and no controls when unsupervised", async () => {
    mockDriverLoops(false, [loop()]);
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loops-not-supervised")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("driver-loop-pause-resume-alan-issue-driver")).toBeNull();
    expect(screen.queryByTestId("driver-loop-run-now-alan-issue-driver")).toBeNull();
  });

  it("renders state, next wake, last outcome, final message, and denial count per loop", async () => {
    mockDriverLoops(true, [
      loop({
        decision: { action: "hold", reason: "interactive_lock", detail: null, next_wake_at: null },
        last_outcome: "ran_with_denials",
        final_message: "denied write to /etc",
        denial_count: 2,
      }),
    ]);
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loop-row-alan-issue-driver")).toBeInTheDocument();
    });
    expect(screen.getByTestId("aggregate-chip-driver-loop-state-alan-issue-driver")).toHaveTextContent(
      "Held — interactive session",
    );
    expect(screen.getByTestId("driver-loop-next-wake-alan-issue-driver")).toHaveTextContent("—");
    expect(screen.getByTestId("driver-loop-last-outcome-alan-issue-driver")).toHaveTextContent(
      "Ran with denials",
    );
    expect(screen.getByTestId("driver-loop-final-message-alan-issue-driver")).toHaveTextContent(
      "denied write to /etc",
    );
    expect(screen.getByTestId("driver-loop-denials-alan-issue-driver")).toHaveTextContent("2");
  });

  it("offers Run now only for an enabled loop", async () => {
    mockDriverLoops(true, [
      loop({ name: "a", enabled: true }),
      loop({ name: "b", enabled: false }),
    ]);
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loop-run-now-a")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("driver-loop-run-now-b")).toBeNull();
  });

  it("keeps controls focusable but aria-disabled without a control token, and enables them once one is present", async () => {
    mockDriverLoops(true, [loop()]);
    const { rerender } = render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loop-pause-resume-alan-issue-driver")).toBeInTheDocument();
    });
    const pauseButton = screen.getByTestId("driver-loop-pause-resume-alan-issue-driver");
    expect(pauseButton).toHaveAttribute("aria-disabled", "true");
    expect(pauseButton).not.toHaveAttribute("disabled");

    writeControlToken("secret-token");
    mockDriverLoops(true, [loop()]);
    rerender(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loop-pause-resume-alan-issue-driver")).toHaveAttribute(
        "aria-disabled",
        "false",
      );
    });
    clearControlToken();
  });

  it("pausing a loop calls the API and announces the outcome in the status region", async () => {
    writeControlToken("secret-token");
    mockDriverLoops(true, [loop({ paused: false })]);
    server.use(
      http.post("/api/driver-loops/alan-issue-driver/pause", () =>
        HttpResponse.json({ name: "alan-issue-driver", paused: true }),
      ),
    );
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loop-pause-resume-alan-issue-driver")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("driver-loop-pause-resume-alan-issue-driver"));
    await waitFor(() => {
      expect(screen.getByTestId("driver-loops-status-region")).toHaveTextContent(
        "alan-issue-driver paused.",
      );
    });
    clearControlToken();
  });

  it("resuming a paused loop calls the API and announces the outcome", async () => {
    writeControlToken("secret-token");
    mockDriverLoops(true, [loop({ paused: true })]);
    server.use(
      http.post("/api/driver-loops/alan-issue-driver/resume", () =>
        HttpResponse.json({ name: "alan-issue-driver", paused: false }),
      ),
    );
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loop-pause-resume-alan-issue-driver")).toHaveTextContent(
        "Resume",
      );
    });
    await userEvent.click(screen.getByTestId("driver-loop-pause-resume-alan-issue-driver"));
    await waitFor(() => {
      expect(screen.getByTestId("driver-loops-status-region")).toHaveTextContent(
        "alan-issue-driver resumed.",
      );
    });
    clearControlToken();
  });

  it("running a loop now calls the API and announces the outcome", async () => {
    writeControlToken("secret-token");
    mockDriverLoops(true, [loop({ decision: { action: "idle", reason: null, detail: null, next_wake_at: null } })]);
    server.use(
      http.post("/api/driver-loops/alan-issue-driver/run-now", () =>
        HttpResponse.json({ ...loop(), run_now_pending: true }),
      ),
    );
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loop-run-now-alan-issue-driver")).toHaveAttribute(
        "aria-disabled",
        "false",
      );
    });
    await userEvent.click(screen.getByTestId("driver-loop-run-now-alan-issue-driver"));
    await waitFor(() => {
      expect(screen.getByTestId("driver-loops-status-region")).toHaveTextContent(
        "alan-issue-driver run requested.",
      );
    });
    clearControlToken();
  });

  it("announces a failed mutation with its error detail in the status region", async () => {
    writeControlToken("secret-token");
    mockDriverLoops(true, [loop({ paused: false })]);
    server.use(
      http.post("/api/driver-loops/alan-issue-driver/pause", () =>
        HttpResponse.json({ detail: "not_supervised" }, { status: 409 }),
      ),
    );
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loop-pause-resume-alan-issue-driver")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("driver-loop-pause-resume-alan-issue-driver"));
    await waitFor(() => {
      expect(screen.getByTestId("driver-loops-status-region")).toHaveTextContent(
        "alan-issue-driver failed to pause",
      );
    });
    clearControlToken();
  });

  it("run-now is aria-disabled while the loop is held", async () => {
    writeControlToken("secret-token");
    mockDriverLoops(true, [
      loop({
        decision: { action: "hold", reason: "running", detail: null, next_wake_at: null },
      }),
    ]);
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loop-run-now-alan-issue-driver")).toHaveAttribute(
        "aria-disabled",
        "true",
      );
    });
    clearControlToken();
  });

  it("run-now is aria-disabled while a run-now request is still pending on the server", async () => {
    writeControlToken("secret-token");
    mockDriverLoops(true, [
      loop({
        decision: { action: "idle", reason: null, detail: null, next_wake_at: null },
        run_now_pending: true,
      }),
    ]);
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("driver-loop-run-now-alan-issue-driver")).toHaveAttribute(
        "aria-disabled",
        "true",
      );
    });
    clearControlToken();
  });

  it("renders 'Due now' for a spawn decision", async () => {
    mockDriverLoops(true, [
      loop({ decision: { action: "spawn", reason: null, detail: null, next_wake_at: null } }),
    ]);
    render(<DriverLoopsPanel />);
    await waitFor(() => {
      expect(
        screen.getByTestId("aggregate-chip-driver-loop-state-alan-issue-driver"),
      ).toHaveTextContent("Due now");
    });
  });
});

describe("loopStateTone", () => {
  function decision(overrides: Partial<DriverLoopDecision> = {}): DriverLoopDecision {
    return { action: "hold", reason: "paused", detail: null, next_wake_at: null, ...overrides };
  }

  it("is transitional for a spawn decision", () => {
    expect(loopStateTone(decision({ action: "spawn" }))).toBe("transitional");
  });

  it("is green for an idle decision", () => {
    expect(loopStateTone(decision({ action: "idle" }))).toBe("green");
  });

  it("is unknown for a hold decision with no reason", () => {
    expect(loopStateTone(decision({ action: "hold", reason: null }))).toBe("unknown");
  });

  it("is unknown for a hold reason with no dedicated tone, such as paused", () => {
    expect(loopStateTone(decision({ action: "hold", reason: "paused" }))).toBe("unknown");
  });

  it("is red for interactive_lock", () => {
    expect(loopStateTone(decision({ action: "hold", reason: "interactive_lock" }))).toBe("red");
  });
});

describe("loopStateText", () => {
  function decision(overrides: Partial<DriverLoopDecision> = {}): DriverLoopDecision {
    return { action: "hold", reason: "paused", detail: null, next_wake_at: null, ...overrides };
  }

  it("is 'Due now' for a spawn decision", () => {
    expect(loopStateText(decision({ action: "spawn" }))).toBe("Due now");
  });

  it("is 'Idle' for an idle decision", () => {
    expect(loopStateText(decision({ action: "idle" }))).toBe("Idle");
  });

  it("is 'Held' for a hold decision with no reason", () => {
    expect(loopStateText(decision({ action: "hold", reason: null }))).toBe("Held");
  });

  it("labels every hold reason", () => {
    expect(loopStateText(decision({ action: "hold", reason: "not_supervised" }))).toBe(
      "Not supervised here",
    );
  });
});
