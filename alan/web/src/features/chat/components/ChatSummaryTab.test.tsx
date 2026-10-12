import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ComponentProps } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { CollisionInfo } from "../hooks/useCollisionCheck";
import ChatSummaryTab, { type SummaryMode } from "./ChatSummaryTab";

function wrap(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

function StatefulChatSummaryTab(props: Readonly<ComponentProps<typeof ChatSummaryTab>>) {
  const [mode, setMode] = useState<SummaryMode>("preview");
  return <ChatSummaryTab {...props} mode={mode} onModeChange={setMode} />;
}

const GREEN_COLLISION: CollisionInfo = {
  status: "green",
  sentence: "No overlapping work found.",
  match: null,
};

function stubRenderProposal(liveBrief: string) {
  return <div data-testid="stub-proposal">{liveBrief}</div>;
}

const COMMON = {
  onEdit: () => undefined,
  renderProposal: stubRenderProposal,
  collisionInfo: GREEN_COLLISION,
  onRerunCollision: () => undefined,
  mode: "edit" as const,
  onModeChange: () => undefined,
};

describe("ChatSummaryTab", () => {
  it("shows CollisionBox when brief is provided", () => {
    wrap(<ChatSummaryTab {...COMMON} brief={{ content: "" }} />);
    expect(screen.getByTestId("collision-box")).toBeInTheDocument();
  });

  it("does not show CollisionBox when brief is null", () => {
    wrap(<ChatSummaryTab {...COMMON} brief={null} />);
    expect(screen.queryByTestId("collision-box")).toBeNull();
  });

  it("renders the proposal panel returned by renderProposal", () => {
    wrap(<ChatSummaryTab {...COMMON} brief={{ content: "hello" }} />);
    expect(screen.getByTestId("stub-proposal")).toHaveTextContent("hello");
  });

  it("clicking Re-run calls onRerunCollision with the current live brief", () => {
    const onRerunCollision = vi.fn();
    wrap(
      <ChatSummaryTab {...COMMON} brief={{ content: "original" }} onRerunCollision={onRerunCollision} />,
    );
    fireEvent.click(screen.getByTestId("collision-rerun"));
    expect(onRerunCollision).toHaveBeenCalledWith("original");
  });

  it("does not render a pricing summary — that lives above the tabs in ChatScaffold", () => {
    wrap(<ChatSummaryTab {...COMMON} brief={{ content: "x" }} />);
    expect(screen.queryByTestId("run-pricing")).not.toBeInTheDocument();
    expect(screen.queryByTestId(/run-summary-header/)).not.toBeInTheDocument();
  });

  it("does not grow via flex-1 — whole-page scrolling owns overflow now", () => {
    const { container } = wrap(<ChatSummaryTab {...COMMON} brief={{ content: "x" }} />);
    expect(container.firstElementChild?.className).not.toMatch(/flex-1/);
  });

  it("renders the mode toggle reflecting edit mode with the 'Preview' label and no aria-pressed", () => {
    wrap(<ChatSummaryTab {...COMMON} brief={{ content: "x" }} mode="edit" />);
    const toggle = screen.getByTestId("summary-source-toggle");
    expect(toggle).not.toHaveAttribute("aria-pressed");
    expect(toggle).toHaveTextContent("Preview");
    expect(screen.getByTestId("summary-textarea")).toBeInTheDocument();
  });

  it("renders the mode toggle reflecting preview mode with the 'Edit' label and no aria-pressed", () => {
    wrap(<ChatSummaryTab {...COMMON} brief={{ content: "# Heading" }} mode="preview" />);
    const toggle = screen.getByTestId("summary-source-toggle");
    expect(toggle).not.toHaveAttribute("aria-pressed");
    expect(toggle).toHaveTextContent("Edit");
    expect(screen.getByTestId("summary-preview")).toBeInTheDocument();
    expect(screen.queryByTestId("summary-textarea")).not.toBeInTheDocument();
  });

  it("moves keyboard focus into the textarea when the toggle switches from preview into edit mode", async () => {
    const user = userEvent.setup();
    wrap(<StatefulChatSummaryTab {...COMMON} brief={{ content: "original" }} />);
    const toggle = screen.getByTestId("summary-source-toggle");
    expect(toggle).toHaveTextContent("Edit");
    await user.click(toggle);
    expect(screen.getByTestId("summary-textarea")).toHaveFocus();
  });

  it("switches from edit to preview mode on toggle click without flushing onEdit when there is no pending edit", () => {
    const onEdit = vi.fn();
    const onModeChange = vi.fn();
    wrap(
      <ChatSummaryTab
        {...COMMON}
        brief={{ content: "original" }}
        onEdit={onEdit}
        onModeChange={onModeChange}
        mode="edit"
      />,
    );
    fireEvent.click(screen.getByTestId("summary-source-toggle"));
    expect(onEdit).not.toHaveBeenCalled();
    expect(onModeChange).toHaveBeenCalledWith("preview");
  });

  it("flushes a pending edit via onEdit before switching to preview mode", async () => {
    const user = userEvent.setup();
    const onEdit = vi.fn();
    const onModeChange = vi.fn();
    wrap(
      <ChatSummaryTab
        {...COMMON}
        brief={{ content: "original" }}
        onEdit={onEdit}
        onModeChange={onModeChange}
        mode="edit"
      />,
    );
    const textarea = screen.getByTestId("summary-textarea");
    await user.clear(textarea);
    await user.type(textarea, "edited");

    fireEvent.click(screen.getByTestId("summary-source-toggle"));

    expect(onEdit).toHaveBeenCalledWith("edited");
    expect(onModeChange).toHaveBeenCalledWith("preview");
  });

  it("passes the live edited content to renderProposal, not the stale saved brief", async () => {
    const user = userEvent.setup();
    wrap(<ChatSummaryTab {...COMMON} brief={{ content: "original" }} />);
    const textarea = screen.getByTestId("summary-textarea");
    await user.clear(textarea);
    await user.type(textarea, "freshly edited");

    expect(screen.getByTestId("stub-proposal")).toHaveTextContent("freshly edited");
  });

  it("calls onLiveBriefChange on every keystroke, not just on blur", async () => {
    const user = userEvent.setup();
    const onLiveBriefChange = vi.fn();
    wrap(
      <ChatSummaryTab {...COMMON} brief={{ content: "original" }} onLiveBriefChange={onLiveBriefChange} />,
    );
    const textarea = screen.getByTestId("summary-textarea");
    await user.type(textarea, "!");
    expect(onLiveBriefChange).toHaveBeenCalledWith("original!");
  });

  it("does not require onLiveBriefChange to be provided", async () => {
    const user = userEvent.setup();
    wrap(<ChatSummaryTab {...COMMON} brief={{ content: "original" }} />);
    const textarea = screen.getByTestId("summary-textarea");
    await user.type(textarea, "!");
    expect(textarea).toHaveValue("original!");
  });

  it("switches from preview back to edit mode on toggle click", () => {
    const onModeChange = vi.fn();
    wrap(
      <ChatSummaryTab
        {...COMMON}
        brief={{ content: "original" }}
        onModeChange={onModeChange}
        mode="preview"
      />,
    );
    fireEvent.click(screen.getByTestId("summary-source-toggle"));
    expect(onModeChange).toHaveBeenCalledWith("edit");
  });
});
