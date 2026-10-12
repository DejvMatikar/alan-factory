import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import AutoDispatchStatusRow from "./AutoDispatchStatusRow";

describe("AutoDispatchStatusRow", () => {
  it("shows the current stage and a Stop control while checking", () => {
    render(
      <AutoDispatchStatusRow
        state={{ running: true, stage: "checking", stop: null }}
        onStop={vi.fn()}
      />,
    );
    expect(screen.getByTestId("auto-dispatch-status")).toHaveTextContent(/overlapping work/);
    expect(screen.getByTestId("auto-dispatch-stop")).toBeInTheDocument();
  });

  it("hides the Stop control while idle", () => {
    render(<AutoDispatchStatusRow state={{ running: false, stage: null, stop: null }} onStop={vi.fn()} />);
    expect(screen.queryByTestId("auto-dispatch-stop")).not.toBeInTheDocument();
  });

  it("hides the Stop control while dispatching, since the commit can't be aborted", () => {
    render(
      <AutoDispatchStatusRow
        state={{ running: true, stage: "dispatching", stop: null }}
        onStop={vi.fn()}
      />,
    );
    expect(screen.getByTestId("auto-dispatch-status")).toHaveTextContent(/Dispatching/);
    expect(screen.queryByTestId("auto-dispatch-stop")).not.toBeInTheDocument();
  });

  it("calls onStop when Stop is clicked", async () => {
    const user = userEvent.setup();
    const onStop = vi.fn();
    render(
      <AutoDispatchStatusRow state={{ running: true, stage: "proposing", stop: null }} onStop={onStop} />,
    );
    await user.click(screen.getByTestId("auto-dispatch-stop"));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("uses a custom stop test id when provided", () => {
    render(
      <AutoDispatchStatusRow
        state={{ running: true, stage: "proposing", stop: null }}
        onStop={vi.fn()}
        stopTestId="custom-stop"
      />,
    );
    expect(screen.getByTestId("custom-stop")).toBeInTheDocument();
  });
});
