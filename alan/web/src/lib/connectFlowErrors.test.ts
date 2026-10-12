import { describe, expect, it } from "vitest";

import { ApiError, ConfigProposalBlockedError } from "../api";
import { classifyMutationError } from "./connectFlowErrors";

describe("classifyMutationError", () => {
  it("classifies 401 as no_token regardless of stage", () => {
    expect(classifyMutationError(new ApiError(401, "control token required"), "register")).toEqual({
      kind: "no_token",
    });
  });

  it("classifies 403 as token_mismatch regardless of stage", () => {
    expect(classifyMutationError(new ApiError(403, "control token mismatch"), "propose")).toEqual({
      kind: "token_mismatch",
    });
  });

  it("classifies a 401 whose detail is 'control token expired' as token_expired, not no_token", () => {
    expect(
      classifyMutationError(new ApiError(401, "control token expired"), "control_token"),
    ).toEqual({ kind: "token_expired" });
  });

  it("classifies 500 as not_configured regardless of stage", () => {
    expect(
      classifyMutationError(new ApiError(500, "control token not configured"), "secret"),
    ).toEqual({ kind: "not_configured" });
  });

  it("classifies 409 during the register stage as already_registered, carrying the detail", () => {
    const block = classifyMutationError(new ApiError(409, "already registered"), "register");
    expect(block).toEqual({ kind: "already_registered", detail: "409: already registered" });
  });

  it("classifies 409 during the save stage as pool_busy, not already_registered", () => {
    const block = classifyMutationError(
      new ApiError(409, "pool has active leases and cannot be resized"),
      "save",
    );
    expect(block).toEqual({
      kind: "pool_busy",
      detail: "409: pool has active leases and cannot be resized",
    });
  });

  it("classifies 409 during propose/secret stages as other, since neither stage actually 409s", () => {
    expect(classifyMutationError(new ApiError(409, "x"), "propose")).toEqual({
      kind: "other",
      detail: "409: x",
    });
    expect(classifyMutationError(new ApiError(409, "x"), "secret")).toEqual({
      kind: "other",
      detail: "409: x",
    });
  });

  it("classifies 422 during the register stage as missing_credential", () => {
    const block = classifyMutationError(
      new ApiError(422, "no secret record for 'acme/widget:transport'"),
      "register",
    );
    expect(block).toEqual({
      kind: "missing_credential",
      detail: "422: no secret record for 'acme/widget:transport'",
    });
  });

  it("classifies 422 during the propose stage as proposal_failed", () => {
    const block = classifyMutationError(new ApiError(422, "aborted"), "propose");
    expect(block).toEqual({ kind: "proposal_failed", detail: "422: aborted" });
  });

  it("classifies 422 during the secret stage as validation", () => {
    const block = classifyMutationError(new ApiError(422, "value must not be empty"), "secret");
    expect(block).toEqual({ kind: "validation", detail: "422: value must not be empty" });
  });

  it("classifies 422 during the save stage as validation", () => {
    const block = classifyMutationError(
      new ApiError(422, "repo in body must match owner/repo in URL"),
      "save",
    );
    expect(block).toEqual({
      kind: "validation",
      detail: "422: repo in body must match owner/repo in URL",
    });
  });

  it("classifies a non-ApiError as other, carrying the error message", () => {
    expect(classifyMutationError(new Error("boom"), "register")).toEqual({
      kind: "other",
      detail: "boom",
    });
  });

  it("classifies a non-Error thrown value as other, stringified", () => {
    expect(classifyMutationError("raw string throw", "register")).toEqual({
      kind: "other",
      detail: "raw string throw",
    });
  });

  it("classifies an unmapped ApiError status as other", () => {
    const block = classifyMutationError(new ApiError(418, "teapot"), "register");
    expect(block).toEqual({ kind: "other", detail: "418: teapot" });
  });

  it("classifies 422 during the scaffold stage as scaffold_failed", () => {
    const block = classifyMutationError(
      new ApiError(422, "git push failed: remote: workflow scope missing"),
      "scaffold",
    );
    expect(block).toEqual({
      kind: "scaffold_failed",
      detail: "422: git push failed: remote: workflow scope missing",
    });
  });

  it("classifies 409 during the scaffold stage as other, since scaffold never actually 409s", () => {
    expect(classifyMutationError(new ApiError(409, "x"), "scaffold")).toEqual({
      kind: "other",
      detail: "409: x",
    });
  });

  it("classifies a ConfigProposalBlockedError as proposal_failed structurally, with no numeric status", () => {
    const finding = {
      check: "config_proposal",
      status: "fail" as const,
      message: "no forge configured",
      remedy: "seed the engine configuration from a stack preset manually",
      remedy_url: "",
    };
    const block = classifyMutationError(new ConfigProposalBlockedError(finding), "register");
    expect(block).toEqual({ kind: "proposal_failed", detail: "no forge configured" });
  });
});
