import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { CollisionInfo } from "../hooks/useCollisionCheck";
import CollisionBox from "./CollisionBox";

const LOADING: CollisionInfo = { status: "loading", sentence: "", match: null };
const GREEN: CollisionInfo = {
  status: "green",
  sentence: "No overlapping work found.",
  match: null,
};
const YELLOW_NO_MATCH: CollisionInfo = {
  status: "yellow",
  sentence: "Similar work exists.",
  match: null,
};
const RED_WS: CollisionInfo = {
  status: "red",
  sentence: "Live overlapping work found.",
  match: {
    label: "My workspace",
    kind: "workspace",
    deep_link: "/workspaces/abc-123",
  },
};
const YELLOW_PR: CollisionInfo = {
  status: "yellow",
  sentence: "Similar PR found.",
  match: {
    label: "PR #42",
    kind: "pr",
    deep_link: "https://github.com/example/repo/pull/42",
  },
};

function wrap(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe("CollisionBox", () => {
  it("renders loading placeholder without Re-run", () => {
    wrap(<CollisionBox info={LOADING} onRerun={() => undefined} />);
    expect(screen.getByTestId("collision-box")).toHaveTextContent(
      "Checking for overlapping work",
    );
    expect(screen.queryByTestId("collision-rerun")).toBeNull();
    expect(screen.queryByTestId("collision-sentence")).toBeNull();
  });

  it("renders green state with sentence and Re-run, no match link", () => {
    wrap(<CollisionBox info={GREEN} onRerun={() => undefined} />);
    expect(screen.getByTestId("collision-sentence")).toHaveTextContent(
      "No overlapping work found.",
    );
    expect(screen.getByTestId("collision-rerun")).toBeInTheDocument();
    expect(screen.queryByTestId("collision-match-link")).toBeNull();
    expect(screen.getByTestId("collision-box").className).toContain("green");
  });

  it("renders yellow state with amber styling", () => {
    wrap(<CollisionBox info={YELLOW_NO_MATCH} onRerun={() => undefined} />);
    expect(screen.getByTestId("collision-box").className).toContain("amber");
    expect(screen.queryByTestId("collision-match-link")).toBeNull();
  });

  it("renders red state with workspace Link (internal)", () => {
    wrap(<CollisionBox info={RED_WS} onRerun={() => undefined} />);
    expect(screen.getByTestId("collision-box").className).toContain("red");
    const matchContainer = screen.getByTestId("collision-match-link");
    const link = matchContainer.querySelector("a");
    expect(link).toHaveTextContent("My workspace");
    expect(link).toHaveAttribute("href", "/workspaces/abc-123");
    expect(link).not.toHaveAttribute("target");
  });

  it("renders yellow state with PR anchor (external, target=_blank)", () => {
    wrap(<CollisionBox info={YELLOW_PR} onRerun={() => undefined} />);
    expect(screen.getByTestId("collision-box").className).toContain("amber");
    const matchContainer = screen.getByTestId("collision-match-link");
    const link = matchContainer.querySelector("a");
    expect(link).toHaveTextContent("PR #42");
    expect(link).toHaveAttribute("href", "https://github.com/example/repo/pull/42");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer noopener");
  });

  it("calls onRerun when Re-run button is clicked", () => {
    const onRerun = vi.fn();
    wrap(<CollisionBox info={GREEN} onRerun={onRerun} />);
    fireEvent.click(screen.getByTestId("collision-rerun"));
    expect(onRerun).toHaveBeenCalledTimes(1);
  });
});
