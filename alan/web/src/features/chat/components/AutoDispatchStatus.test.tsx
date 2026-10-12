import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import AutoDispatchStatus from "./AutoDispatchStatus";

describe("AutoDispatchStatus", () => {
  it("shows the current stage label while running", () => {
    render(<AutoDispatchStatus state={{ running: true, stage: "dispatching", stop: null }} />);
    expect(screen.getByTestId("auto-dispatch-status")).toHaveTextContent("Dispatching…");
  });

  it("shows the stop message once stopped", () => {
    render(<AutoDispatchStatus state={{ running: false, stage: null, stop: "cancelled" }} />);
    expect(screen.getByTestId("auto-dispatch-status")).toHaveTextContent(/cancelled/);
  });

  it("renders empty text while idle", () => {
    render(<AutoDispatchStatus state={{ running: false, stage: null, stop: null }} />);
    expect(screen.getByTestId("auto-dispatch-status")).toHaveTextContent("");
  });

  it("carries aria-live polite for assistive announcement", () => {
    render(<AutoDispatchStatus state={{ running: false, stage: null, stop: null }} />);
    expect(screen.getByTestId("auto-dispatch-status")).toHaveAttribute("aria-live", "polite");
  });
});
