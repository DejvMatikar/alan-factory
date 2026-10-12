import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import ActivityLine from "./ActivityLine";

describe("ActivityLine", () => {
  it("renders nothing when there are no entries and Alan is not thinking", () => {
    const { container } = render(<ActivityLine entries={[]} thinking={false} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the thinking live region when thinking is true, even with no entries yet", () => {
    render(<ActivityLine entries={[]} thinking={true} />);
    const thinking = screen.getByTestId("thinking");
    expect(thinking).toHaveTextContent("Alan is thinking…");
    expect(thinking).toHaveAttribute("aria-live", "polite");
  });

  it("shows the most recent operation label, truncated to one line, with no thinking indicator once done", () => {
    render(<ActivityLine entries={["Read: a.py", "Bash: ls"]} thinking={false} />);
    const current = screen.getByTestId("activity-line-current");
    expect(current).toHaveTextContent("Bash: ls");
    expect(current.className).toContain("truncate");
    expect(screen.queryByTestId("thinking")).not.toBeInTheDocument();
  });

  it("keeps exactly one aria-live region, with the operation label as a sibling outside it", () => {
    render(<ActivityLine entries={["Read: a.py"]} thinking={true} />);
    expect(document.querySelectorAll('[aria-live]')).toHaveLength(1);
    const current = screen.getByTestId("activity-line-current");
    expect(current).not.toHaveAttribute("aria-live");
  });

  it("occupies the same collapsed height with zero, one, and fifty entries", () => {
    const { container: zero } = render(<ActivityLine entries={[]} thinking={true} />);
    const { container: one } = render(<ActivityLine entries={["Read: a.py"]} thinking={true} />);
    const many = Array.from({ length: 50 }, (_, i) => `Read: ${String(i)}.py`);
    const { container: fifty } = render(<ActivityLine entries={many} thinking={true} />);
    const zeroLine = zero.querySelector<HTMLElement>('[data-testid="activity-line"]')!;
    const oneLine = one.querySelector<HTMLElement>('[data-testid="activity-line"]')!;
    const fiftyLine = fifty.querySelector<HTMLElement>('[data-testid="activity-line"]')!;
    expect(zeroLine.className).toBe(oneLine.className);
    expect(oneLine.className).toBe(fiftyLine.className);
  });

  it("starts collapsed, with the toggle stating aria-expanded=false and no aria-controls", () => {
    render(<ActivityLine entries={["Read: a.py"]} thinking={false} />);
    const toggle = screen.getByTestId("activity-line-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).not.toHaveAttribute("aria-controls");
    expect(screen.queryByTestId("activity-line-list")).not.toBeInTheDocument();
  });

  it("expands to show every entry in order on click, and updates aria-expanded", async () => {
    const user = userEvent.setup();
    render(<ActivityLine entries={["Read: a.py", "Bash: ls", "Grep: foo"]} thinking={false} />);
    await user.click(screen.getByTestId("activity-line-toggle"));
    expect(screen.getByTestId("activity-line-toggle")).toHaveAttribute("aria-expanded", "true");
    const list = screen.getByTestId("activity-line-list");
    expect(list.className).not.toContain("max-h-40");
    expect(list.className).not.toContain("overflow-y-auto");
    expect(list.className).not.toContain("absolute");
    const items = list.querySelectorAll("li");
    expect(Array.from(items).map((li) => li.textContent)).toEqual([
      "Read: a.py",
      "Bash: ls",
      "Grep: foo",
    ]);
  });

  it("collapses again on a second click", async () => {
    const user = userEvent.setup();
    render(<ActivityLine entries={["Read: a.py"]} thinking={false} />);
    const toggle = screen.getByTestId("activity-line-toggle");
    await user.click(toggle);
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).not.toHaveAttribute("aria-controls");
    expect(screen.queryByTestId("activity-line-list")).not.toBeInTheDocument();
  });

  it("expands the list in normal flow, with no li truncation and full-width wrapping", async () => {
    const user = userEvent.setup();
    render(
      <ActivityLine
        entries={["a very long operation label that should wrap instead of truncating"]}
        thinking={false}
      />,
    );
    await user.click(screen.getByTestId("activity-line-toggle"));
    const list = screen.getByTestId("activity-line-list");
    expect(list.className).not.toContain("absolute");
    const item = list.querySelector("li")!;
    expect(item.className).not.toContain("truncate");
    expect(item.className).toContain("break-words");
  });

  it("carries no fixed max-width on the wrapper in either state", async () => {
    const user = userEvent.setup();
    render(<ActivityLine entries={["Read: a.py"]} thinking={false} />);
    const line = screen.getByTestId("activity-line");
    expect(line.className).not.toContain("max-w-[min(80%,65ch)]");
    await user.click(screen.getByTestId("activity-line-toggle"));
    expect(line.className).not.toContain("max-w-[min(80%,65ch)]");
  });

  it("associates the toggle with the rendered list via aria-controls", async () => {
    const user = userEvent.setup();
    render(<ActivityLine entries={["Read: a.py"]} thinking={false} />);
    const toggle = screen.getByTestId("activity-line-toggle");
    await user.click(toggle);
    const list = screen.getByTestId("activity-line-list");
    expect(toggle).toHaveAttribute("aria-controls", list.id);
    expect(list.id).not.toBe("");
  });

  it("gives two rendered instances distinct aria-controls ids", async () => {
    const user = userEvent.setup();
    render(
      <>
        <ActivityLine entries={["Read: a.py"]} thinking={false} />
        <ActivityLine entries={["Bash: ls"]} thinking={false} />
      </>,
    );
    const toggles = screen.getAllByTestId("activity-line-toggle");
    await user.click(toggles[0]!);
    await user.click(toggles[1]!);
    const ids = toggles.map((t) => t.getAttribute("aria-controls"));
    expect(ids.every((id) => id !== null)).toBe(true);
    expect(new Set(ids).size).toBe(2);
  });

  it("renders ~200 entries with no inner scroll container", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 200 }, (_, i) => `Read: ${String(i)}.py`);
    render(<ActivityLine entries={many} thinking={false} />);
    await user.click(screen.getByTestId("activity-line-toggle"));
    const list = screen.getByTestId("activity-line-list");
    expect(list.className).not.toContain("overflow-y-auto");
    expect(list.querySelectorAll("li")).toHaveLength(200);
  });

  it("is reachable and operable by keyboard", async () => {
    const user = userEvent.setup();
    render(<ActivityLine entries={["Read: a.py"]} thinking={false} />);
    await user.tab();
    expect(screen.getByTestId("activity-line-toggle")).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByTestId("activity-line-toggle")).toHaveAttribute("aria-expanded", "true");
  });

  it("meets the app's touch-target minimum on the expand control", () => {
    render(<ActivityLine entries={["Read: a.py"]} thinking={false} />);
    const toggle = screen.getByTestId("activity-line-toggle");
    expect(toggle.className).toContain("min-h-11");
    expect(toggle.className).toContain("min-w-11");
  });

  it("gives the toggle a distinct accessible name when open vs closed", async () => {
    const user = userEvent.setup();
    render(<ActivityLine entries={["Read: a.py"]} thinking={false} label="this turn's activity" />);
    const toggle = screen.getByTestId("activity-line-toggle");
    expect(toggle).toHaveAccessibleName("Show this turn's activity");
    await user.click(toggle);
    expect(toggle).toHaveAccessibleName("Hide this turn's activity");
  });

  it("describes the toggle's purpose, following its state, and reveals the description on focus", async () => {
    const user = userEvent.setup();
    render(<ActivityLine entries={["Read: a.py"]} thinking={false} />);
    const toggle = screen.getByTestId("activity-line-toggle");
    const description = document.getElementById(toggle.getAttribute("aria-describedby") ?? "");
    expect(toggle).toHaveAccessibleDescription("See every recent entry");
    expect(description).toHaveClass("sr-only");

    await user.tab();

    expect(description).not.toHaveClass("sr-only");
    await user.keyboard("{Enter}");
    expect(toggle).toHaveAccessibleDescription("Collapse to the latest entry");
  });

  it("keeps the toggle a ghost icon-only button with a decorative glyph", () => {
    render(<ActivityLine entries={["Read: a.py"]} thinking={false} />);
    const toggle = screen.getByTestId("activity-line-toggle");
    expect(toggle).toHaveAttribute("data-icon-only", "true");
    expect(toggle.querySelector("span[aria-hidden='true']")).toHaveTextContent("▸");
  });

  it("describes the toggle in the overlay variant too", () => {
    render(<ActivityLine entries={["Read: a.py"]} thinking={false} variant="overlay" />);
    expect(screen.getByTestId("activity-line-toggle")).toHaveAccessibleDescription(
      "See every recent entry",
    );
  });

  it("renders the legacy absolutely-positioned, height-capped overlay when variant is overlay", async () => {
    const user = userEvent.setup();
    render(
      <ActivityLine
        entries={["Read: a.py", "a very long operation label that should truncate not wrap"]}
        thinking={false}
        variant="overlay"
      />,
    );
    const line = screen.getByTestId("activity-line");
    expect(line.className).toContain("max-w-[min(80%,65ch)]");
    expect(line.className).toContain("relative");
    await user.click(screen.getByTestId("activity-line-toggle"));
    const list = screen.getByTestId("activity-line-list");
    expect(list.className).toContain("absolute");
    expect(list.className).toContain("max-h-40");
    expect(list.className).toContain("overflow-y-auto");
    const item = list.querySelector("li")!;
    expect(item.className).toContain("truncate");
    expect(item.className).not.toContain("break-words");
  });

  it("keeps aria-controls and per-instance list ids correct in the overlay variant too", async () => {
    const user = userEvent.setup();
    render(<ActivityLine entries={["Read: a.py"]} thinking={false} variant="overlay" />);
    const toggle = screen.getByTestId("activity-line-toggle");
    expect(toggle).not.toHaveAttribute("aria-controls");
    await user.click(toggle);
    const list = screen.getByTestId("activity-line-list");
    expect(toggle).toHaveAttribute("aria-controls", list.id);
    expect(list.id).not.toBe("");
  });
});
