import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { MutationBlock } from "../../lib/connectFlowErrors";
import MutationErrorNotice from "./MutationErrorNotice";

describe("MutationErrorNotice", () => {
  it("renders the no_token copy pointing at Settings, with role=alert", () => {
    render(<MutationErrorNotice block={{ kind: "no_token" }} testId="notice" />);
    expect(screen.getByTestId("notice")).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("notice-message")).toHaveTextContent("Settings");
    expect(screen.queryByTestId("notice-detail")).not.toBeInTheDocument();
  });

  it("renders the token_mismatch copy, distinct from no_token", () => {
    render(<MutationErrorNotice block={{ kind: "token_mismatch" }} testId="notice" />);
    expect(screen.getByTestId("notice-message")).toHaveTextContent("doesn't match");
  });

  it("renders the token_expired copy, distinct from no_token and naming the remedy", () => {
    render(<MutationErrorNotice block={{ kind: "token_expired" }} testId="notice" />);
    expect(screen.getByTestId("notice-message")).toHaveTextContent("expired");
    expect(screen.getByTestId("notice-message")).toHaveTextContent(/extend or replace/);
  });

  it("renders the not_configured copy", () => {
    render(<MutationErrorNotice block={{ kind: "not_configured" }} testId="notice" />);
    expect(screen.getByTestId("notice-message")).toHaveTextContent("ask an operator");
  });

  it("renders the missing_credential copy naming the Credentials section, plus the detail", () => {
    const block: MutationBlock = {
      kind: "missing_credential",
      detail: "422: no secret record for 'acme/widget:transport'",
    };
    render(<MutationErrorNotice block={block} testId="notice" />);
    expect(screen.getByTestId("notice-message")).toHaveTextContent("Credentials");
    expect(screen.getByTestId("notice-detail")).toHaveTextContent("acme/widget:transport");
  });

  it("renders already_registered, proposal_failed, validation, pool_busy, scaffold_failed, and other with their own detail", () => {
    const cases: Extract<MutationBlock, { detail: string }>[] = [
      { kind: "already_registered", detail: "409: already registered" },
      { kind: "proposal_failed", detail: "422: aborted" },
      { kind: "validation", detail: "422: value must not be empty" },
      { kind: "pool_busy", detail: "409: pool has active leases and cannot be resized" },
      { kind: "scaffold_failed", detail: "remote: workflow scope missing" },
      { kind: "other", detail: "boom" },
    ];
    for (const block of cases) {
      const { unmount } = render(<MutationErrorNotice block={block} testId="notice" />);
      expect(screen.getByTestId("notice-detail")).toHaveTextContent(block.detail);
      unmount();
    }
  });

  it("renders the scaffold_failed copy as a neutral headline, plus the forge's own detail line", () => {
    const block: MutationBlock = {
      kind: "scaffold_failed",
      detail: "remote: refusing to allow a Personal Access Token to create or update workflow",
    };
    render(<MutationErrorNotice block={block} testId="notice" />);
    expect(screen.getByTestId("notice-message")).toHaveTextContent(
      "scaffold pull request couldn't be opened",
    );
    expect(screen.getByTestId("notice-detail")).toHaveTextContent(
      "refusing to allow a Personal Access Token",
    );
  });

  it("renders no detail line at all for a kind with no detail field, headline only", () => {
    render(<MutationErrorNotice block={{ kind: "not_configured" }} testId="notice" />);
    expect(screen.queryByTestId("notice-detail")).not.toBeInTheDocument();
  });

  it("renders the pool_busy copy naming the pool and leases, distinct from already_registered", () => {
    const block: MutationBlock = {
      kind: "pool_busy",
      detail: "409: pool has active leases and cannot be resized",
    };
    render(<MutationErrorNotice block={block} testId="notice" />);
    expect(screen.getByTestId("notice-message")).toHaveTextContent("worker pool");
    expect(screen.getByTestId("notice-message")).toHaveTextContent("active leases");
  });
});
