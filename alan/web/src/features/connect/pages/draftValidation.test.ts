import { describe, expect, it, vi } from "vitest";

import type { EnginePreflightFinding } from "../../../api";
import { settleDraftValidation } from "./draftValidation";

const PASS_FINDING: EnginePreflightFinding = {
  check: "flow_graph_and_posture_valid",
  status: "pass",
  message: "ok",
  remedy: "",
  remedy_url: "",
};

describe("settleDraftValidation", () => {
  it("sets a ready state with the resolved finding when not cancelled", async () => {
    const setState = vi.fn();
    await settleDraftValidation(Promise.resolve(PASS_FINDING), () => false, setState);
    expect(setState).toHaveBeenCalledWith({ status: "ready", finding: PASS_FINDING });
  });

  it("sets an error state with the message when the promise rejects and not cancelled", async () => {
    const setState = vi.fn();
    await settleDraftValidation(Promise.reject(new Error("boom")), () => false, setState);
    expect(setState).toHaveBeenCalledWith({ status: "error", message: "boom" });
  });

  it("never calls setState once cancelled, on either resolution path", async () => {
    const setStateResolved = vi.fn();
    await settleDraftValidation(Promise.resolve(PASS_FINDING), () => true, setStateResolved);
    expect(setStateResolved).not.toHaveBeenCalled();

    const setStateRejected = vi.fn();
    await settleDraftValidation(Promise.reject(new Error("boom")), () => true, setStateRejected);
    expect(setStateRejected).not.toHaveBeenCalled();
  });
});
