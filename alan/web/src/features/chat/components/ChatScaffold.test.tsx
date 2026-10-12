import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { RunSummary } from "../../../api";
import { SCROLL_BODY_FLOW_CLASS, SCROLL_COLUMN_FLOW_CLASS } from "../../../shell/stickyOffsets";
import ChatScaffold from "./ChatScaffold";

function wrap(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

function makeRunSummary(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    model: "opus",
    effort: "xhigh",
    system_prompt: "system prompt",
    total_cost_usd: 0.01,
    total_output_tokens: 10,
    total_input_tokens: 20,
    tool_counts: [],
    ...overrides,
  };
}

describe("ChatScaffold", () => {
  it("renders the title and tabs, with no in-chat back-to-workspaces link", () => {
    wrap(
      <ChatScaffold title="Build" tab="chat" onTab={() => undefined}>
        <div data-testid="child">child content</div>
      </ChatScaffold>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Build" })).toBeInTheDocument();
    expect(screen.queryByText(/Back to workspaces/)).not.toBeInTheDocument();
    expect(screen.getByTestId("tab-chat")).toBeInTheDocument();
    expect(screen.getByTestId("tab-summary")).toBeInTheDocument();
    expect(screen.getByTestId("child")).toBeInTheDocument();
  });

  it("renders no pricing summary when neither run has a summary", () => {
    wrap(
      <ChatScaffold title="Build" tab="chat" onTab={() => undefined}>
        <div />
      </ChatScaffold>,
    );
    expect(screen.queryByTestId(/run-summary-header/)).not.toBeInTheDocument();
  });

  it("renders only the conversation row when collisionRunSummary is absent", () => {
    wrap(
      <ChatScaffold title="Build" tab="chat" onTab={() => undefined} runSummary={makeRunSummary()}>
        <div />
      </ChatScaffold>,
    );
    expect(screen.getByTestId("run-summary-header-conversation")).toBeInTheDocument();
    expect(screen.queryByTestId("run-summary-header-collision")).not.toBeInTheDocument();
  });

  it("renders the conversation and collision runs as distinct pricing rows", () => {
    wrap(
      <ChatScaffold
        title="Build"
        tab="chat"
        onTab={() => undefined}
        runSummary={makeRunSummary({ model: "opus" })}
        collisionRunSummary={makeRunSummary({ model: "haiku", effort: null, tool_counts: [] })}
      >
        <div />
      </ChatScaffold>,
    );
    expect(screen.getByTestId("run-pricing")).toBeInTheDocument();
    expect(screen.getByTestId("run-summary-header-conversation")).toBeInTheDocument();
    expect(screen.getByTestId("run-summary-header-collision")).toBeInTheDocument();
    expect(screen.getByTestId("run-summary-label-conversation")).toHaveTextContent("Conversation");
    expect(screen.getByTestId("run-summary-label-collision")).toHaveTextContent("Collision check");
    expect(screen.getByTestId("run-summary-setup-conversation")).toHaveTextContent("opus · xhigh");
    expect(screen.getByTestId("run-summary-setup-collision")).toHaveTextContent("haiku");
  });

  it("calls onTab with the clicked tab id", () => {
    const onTab = vi.fn();
    wrap(
      <ChatScaffold title="Build" tab="chat" onTab={onTab}>
        <div />
      </ChatScaffold>,
    );
    fireEvent.click(screen.getByTestId("tab-summary"));
    expect(onTab).toHaveBeenCalledWith("summary");
    fireEvent.click(screen.getByTestId("tab-chat"));
    expect(onTab).toHaveBeenCalledWith("chat");
  });

  it("exposes the tabs as a tablist with aria-selected and roving tabIndex", () => {
    wrap(
      <ChatScaffold title="Build" tab="summary" onTab={() => undefined}>
        <div />
      </ChatScaffold>,
    );
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("tab-summary")).toHaveAttribute("tabIndex", "0");
    expect(screen.getByTestId("tab-chat")).toHaveAttribute("aria-selected", "false");
    expect(screen.getByTestId("tab-chat")).toHaveAttribute("tabIndex", "-1");
    expect(screen.getByTestId("tab-chat")).not.toHaveAttribute("aria-pressed");
    expect(screen.getByTestId("tab-summary")).not.toHaveAttribute("aria-pressed");
  });

  it("ArrowRight moves from Chat to Summary and focuses it", () => {
    const onTab = vi.fn();
    wrap(
      <ChatScaffold title="Build" tab="chat" onTab={onTab}>
        <div />
      </ChatScaffold>,
    );
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onTab).toHaveBeenCalledWith("summary");
  });

  it("ArrowLeft moves from Summary to Chat", () => {
    const onTab = vi.fn();
    wrap(
      <ChatScaffold title="Build" tab="summary" onTab={onTab}>
        <div />
      </ChatScaffold>,
    );
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    expect(onTab).toHaveBeenCalledWith("chat");
  });

  it("ArrowRight at the last tab clamps instead of wrapping", () => {
    const onTab = vi.fn();
    wrap(
      <ChatScaffold title="Build" tab="summary" onTab={onTab}>
        <div />
      </ChatScaffold>,
    );
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onTab).toHaveBeenCalledWith("summary");
  });

  it("a non-arrow key on the tablist does nothing", () => {
    const onTab = vi.fn();
    wrap(
      <ChatScaffold title="Build" tab="chat" onTab={onTab}>
        <div />
      </ChatScaffold>,
    );
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "Enter" });
    expect(onTab).not.toHaveBeenCalled();
  });

  it("arrow keys no-op when no rendered tab is currently active", () => {
    const onTab = vi.fn();
    wrap(
      <ChatScaffold title="Build" tab="questions" onTab={onTab}>
        <div />
      </ChatScaffold>,
    );
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onTab).not.toHaveBeenCalled();
  });

  it("does not cap its own height with an inline min-height — content grows with the whole page", () => {
    const { container } = wrap(
      <ChatScaffold title="Build" tab="chat" onTab={() => undefined}>
        <div />
      </ChatScaffold>,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.minHeight).toBe("");
  });

  it("gives the tabpanel no scroll-region classes on the chat tab, since the page itself scrolls instead", () => {
    wrap(
      <ChatScaffold title="Build" tab="chat" onTab={() => undefined}>
        <div />
      </ChatScaffold>,
    );
    const tabpanel = screen.getByRole("tabpanel");
    expect(tabpanel.className).not.toMatch(/max-h/);
    expect(tabpanel.className).not.toContain("overflow-y-auto");
    expect(tabpanel.className).not.toContain("min-h-0");
  });

  it("gives the tabpanel the same page-flow class on the summary tab as the chat tab", () => {
    wrap(
      <ChatScaffold title="Build" tab="summary" onTab={() => undefined}>
        <div />
      </ChatScaffold>,
    );
    const tabpanel = screen.getByRole("tabpanel");
    expect(tabpanel.className).not.toMatch(/max-h/);
    expect(tabpanel.className).not.toContain("overflow-y-auto");
    expect(tabpanel.className.trim()).toBe(`flex flex-col ${SCROLL_BODY_FLOW_CLASS}`.trim());
  });

  it("carries no tabIndex on the tabpanel, since it no longer scrolls internally", () => {
    wrap(
      <ChatScaffold title="Build" tab="chat" onTab={() => undefined}>
        <div />
      </ChatScaffold>,
    );
    expect(screen.getByRole("tabpanel")).not.toHaveAttribute("tabIndex");
  });

  it("makes the root a page-flow column, not a bounded scroll column, above the small-screen breakpoint", () => {
    const { container } = wrap(
      <ChatScaffold title="Build" tab="chat" onTab={() => undefined}>
        <div />
      </ChatScaffold>,
    );
    const root = container.firstElementChild as HTMLElement;
    for (const token of SCROLL_COLUMN_FLOW_CLASS.split(" ")) {
      expect(root.className).toContain(token);
    }
    expect(root.className).not.toMatch(/max-h/);
  });

  it("does not move focus to the heading when no focusHeadingKey is given", () => {
    wrap(
      <ChatScaffold title="Build" tab="chat" onTab={() => undefined}>
        <div />
      </ChatScaffold>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Build" })).not.toHaveFocus();
  });

  it("moves focus to the heading when a focusHeadingKey is given", () => {
    wrap(
      <ChatScaffold title="Build" tab="chat" onTab={() => undefined} focusHeadingKey="sid-1">
        <div />
      </ChatScaffold>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Build" })).toHaveFocus();
  });

  it("paints the focus ring only under focus-visible, never on every focus move", () => {
    wrap(
      <ChatScaffold title="Build" tab="chat" onTab={() => undefined}>
        <div />
      </ChatScaffold>,
    );
    const heading = screen.getByRole("heading", { level: 1, name: "Build" });
    expect(heading.className).toContain("focus-visible:outline-none");
    expect(heading.className).toContain("focus-visible:ring-2");
    expect(heading.className).toContain("focus-visible:ring-text-primary/60");
    expect(heading.className).not.toContain("focus:outline-none");
    expect(heading.className).not.toContain("focus:ring-2");
  });

  it("no longer pins the title, cost line, and tab row — the wrapper carries only layout classes", () => {
    wrap(
      <ChatScaffold
        title="Build"
        tab="chat"
        onTab={() => undefined}
        runSummary={makeRunSummary()}
        collisionRunSummary={makeRunSummary()}
      >
        <div />
      </ChatScaffold>,
    );
    const heading = screen.getByRole("heading", { level: 1, name: "Build" });
    const headingWrapper = heading.parentElement!.parentElement!;
    expect(headingWrapper.className).toBe("space-y-2");
    expect(headingWrapper.className).not.toContain("sm:sticky");
    expect(headingWrapper.className).not.toContain("sm:z-20");
    expect(headingWrapper.className).not.toContain("sm:top-28");
  });

  it("keeps tablist keyboard cycling unaffected by the sticky wrapper", () => {
    const onTab = vi.fn();
    wrap(
      <ChatScaffold title="Build" tab="chat" onTab={onTab}>
        <div />
      </ChatScaffold>,
    );
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onTab).toHaveBeenCalledWith("summary");
  });

  it("moves focus again when focusHeadingKey changes to a different session", () => {
    const { rerender } = wrap(
      <ChatScaffold title="Build" tab="chat" onTab={() => undefined} focusHeadingKey="sid-1">
        <div />
      </ChatScaffold>,
    );
    const heading = screen.getByRole("heading", { level: 1, name: "Build" });
    (document.activeElement as HTMLElement | null)?.blur();
    expect(heading).not.toHaveFocus();
    rerender(
      <MemoryRouter>
        <ChatScaffold title="Build" tab="chat" onTab={() => undefined} focusHeadingKey="sid-2">
          <div />
        </ChatScaffold>
      </MemoryRouter>,
    );
    expect(heading).toHaveFocus();
  });
});
