import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ArtifactRef, WorkspaceDetail } from "../../../api";
import { makeHandover, makeWorkspaceDetail } from "../../../test/workspaceFixtures";
import Deliverables from "./Deliverables";

const BASE_WS: WorkspaceDetail = makeWorkspaceDetail({ engine_key: "DejvMatikar/alan" });

describe("Deliverables", () => {
  it("renders nothing when there is no latest handover", () => {
    const { container } = render(<Deliverables ws={BASE_WS} taskId="t1" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the latest handover has no artifacts", () => {
    const ws = { ...BASE_WS, latest_handover: makeHandover() };
    const { container } = render(<Deliverables ws={ws} taskId="t1" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the Deliverables heading and artifact links when artifacts exist", () => {
    const artifacts: ArtifactRef[] = [
      { path: "invocations/inv_001/report.md", description: "Full research report" },
    ];
    const ws = {
      ...BASE_WS,
      latest_handover: makeHandover({ artifacts_produced: artifacts }),
    };
    render(<Deliverables ws={ws} taskId="t1" />);
    expect(screen.getByRole("heading", { name: "Deliverables" })).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Artifacts Produced" }),
    ).not.toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Full research report" });
    expect(link).toHaveAttribute(
      "href",
      "/api/workspaces/t1/artifacts?path=invocations%2Finv_001%2Freport.md",
    );
  });

  it("passes pr_number through to the PR link", () => {
    const ws = {
      ...BASE_WS,
      latest_handover: makeHandover({
        artifacts_produced: [
          { path: "invocations/inv_001/report.md", description: "Full research report" },
        ],
        pr_number: 42,
      }),
    };
    render(<Deliverables ws={ws} taskId="t1" />);
    expect(screen.getByTestId("pr-link")).toHaveAttribute(
      "href",
      "https://github.com/DejvMatikar/alan/pull/42",
    );
  });

  it("uses the workspace's own engine_key for a non-default repo", () => {
    const ws = {
      ...BASE_WS,
      engine_key: "acme-corp/sprocket",
      latest_handover: makeHandover({
        artifacts_produced: [
          { path: "invocations/inv_001/report.md", description: "Full research report" },
        ],
        pr_number: 7,
      }),
    };
    render(<Deliverables ws={ws} taskId="t1" />);
    expect(screen.getByTestId("pr-link")).toHaveAttribute(
      "href",
      "https://github.com/acme-corp/sprocket/pull/7",
    );
  });

  it("renders a degraded, non-clickable PR reference when the workspace has no engine_key", () => {
    const ws = {
      ...BASE_WS,
      engine_key: null,
      latest_handover: makeHandover({
        artifacts_produced: [
          { path: "invocations/inv_001/report.md", description: "Full research report" },
        ],
        pr_number: 42,
      }),
    };
    render(<Deliverables ws={ws} taskId="t1" />);
    const el = screen.getByTestId("pr-link");
    expect(el.tagName).toBe("SPAN");
    expect(el).toHaveTextContent("PR #42");
    expect(el).toHaveTextContent("repository unresolved");
  });
});
