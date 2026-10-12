import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import UnderlineTab from "./UnderlineTab";
import { handleTablistKeyDown } from "./tablistKeys";

describe("UnderlineTab", () => {
  it("marks the selected tab with the underline, aria-selected and a roving tab stop", () => {
    render(<UnderlineTab id="chat" label="Chat" active={true} onClick={vi.fn()} />);
    const tab = screen.getByRole("tab", { name: "Chat" });
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(tab).toHaveAttribute("tabIndex", "0");
    expect(tab.className).toContain("border-b-2 border-text-primary");
    expect(tab.className).toContain("text-text-primary");
  });

  it("renders an unselected tab muted, without the underline, and out of the tab order", () => {
    render(<UnderlineTab id="chat" label="Chat" active={false} onClick={vi.fn()} />);
    const tab = screen.getByRole("tab", { name: "Chat" });
    expect(tab).toHaveAttribute("aria-selected", "false");
    expect(tab).toHaveAttribute("tabIndex", "-1");
    expect(tab.className).not.toContain("border-b-2");
    expect(tab.className).toContain("text-text-muted");
  });

  it("derives its id and test id from the tab id and never claims aria-pressed", () => {
    render(<UnderlineTab id="summary" label="Summary" active={false} onClick={vi.fn()} />);
    const tab = screen.getByTestId("tab-summary");
    expect(tab).toHaveAttribute("id", "tab-summary");
    expect(tab).toHaveAttribute("type", "button");
    expect(tab).not.toHaveAttribute("aria-pressed");
  });

  it("calls onClick when clicked", () => {
    const onClick = vi.fn();
    render(<UnderlineTab id="chat" label="Chat" active={false} onClick={onClick} />);
    fireEvent.click(screen.getByRole("tab"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

function renderTablist(selected: number | null, onClicks: readonly (() => void)[]) {
  return render(
    <div role="tablist" tabIndex={-1} onKeyDown={handleTablistKeyDown}>
      {onClicks.map((onClick, index) => (
        <UnderlineTab
          key={index}
          id={`t${String(index)}`}
          label={`Tab ${String(index)}`}
          active={index === selected}
          onClick={onClick}
        />
      ))}
    </div>,
  );
}

describe("handleTablistKeyDown", () => {
  it("moves to and clicks the next tab on ArrowRight", () => {
    const clicks = [vi.fn(), vi.fn(), vi.fn()];
    renderTablist(0, clicks);
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(clicks[1]).toHaveBeenCalledTimes(1);
    expect(clicks[0]).not.toHaveBeenCalled();
    expect(screen.getByTestId("tab-t1")).toHaveFocus();
  });

  it("moves to and clicks the previous tab on ArrowLeft", () => {
    const clicks = [vi.fn(), vi.fn(), vi.fn()];
    renderTablist(2, clicks);
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    expect(clicks[1]).toHaveBeenCalledTimes(1);
  });

  it("marks the arrow key handled so the page does not scroll", () => {
    renderTablist(0, [vi.fn(), vi.fn()]);
    const notPrevented = fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(notPrevented).toBe(false);
  });

  it("clamps at the last tab instead of wrapping", () => {
    const clicks = [vi.fn(), vi.fn()];
    renderTablist(1, clicks);
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(clicks[1]).toHaveBeenCalledTimes(1);
    expect(clicks[0]).not.toHaveBeenCalled();
  });

  it("clamps at the first tab instead of wrapping", () => {
    const clicks = [vi.fn(), vi.fn()];
    renderTablist(0, clicks);
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    expect(clicks[0]).toHaveBeenCalledTimes(1);
    expect(clicks[1]).not.toHaveBeenCalled();
  });

  it("ignores keys other than the two arrows and leaves them unhandled", () => {
    const clicks = [vi.fn(), vi.fn()];
    renderTablist(0, clicks);
    for (const key of ["Home", "End", "Enter", "ArrowDown", "a"]) {
      expect(fireEvent.keyDown(screen.getByRole("tablist"), { key })).toBe(true);
    }
    expect(clicks[0]).not.toHaveBeenCalled();
    expect(clicks[1]).not.toHaveBeenCalled();
  });

  it("does nothing when no tab in the tablist is selected", () => {
    const clicks = [vi.fn(), vi.fn()];
    renderTablist(null, clicks);
    expect(fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" })).toBe(true);
    expect(clicks[0]).not.toHaveBeenCalled();
    expect(clicks[1]).not.toHaveBeenCalled();
  });
});
