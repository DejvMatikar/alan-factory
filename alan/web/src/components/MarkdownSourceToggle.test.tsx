import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import MarkdownSourceToggle, {
  sourceToggleLabel,
  type SourceToggleLabelSet,
} from "./MarkdownSourceToggle";

describe("sourceToggleLabel", () => {
  it("defaults to the 'source' label set when none is given", () => {
    expect(sourceToggleLabel(true)).toBe("View formatted");
    expect(sourceToggleLabel(false)).toBe("View source");
  });

  it.each([
    ["source", false, "View source"],
    ["source", true, "View formatted"],
    ["edit", false, "Edit"],
    ["edit", true, "Preview"],
  ] as const)("labelSet=%s, showSource=%s -> %s", (labelSet, showSource, expected) => {
    expect(sourceToggleLabel(showSource, labelSet)).toBe(expected);
  });
});

interface ToggleCase {
  labelSet: SourceToggleLabelSet;
  showSource: boolean;
  expectedLabel: string;
  expectsAriaPressed: boolean;
}

const TOGGLE_CASES: ToggleCase[] = [
  { labelSet: "source", showSource: false, expectedLabel: "View source", expectsAriaPressed: true },
  { labelSet: "source", showSource: true, expectedLabel: "View formatted", expectsAriaPressed: true },
  { labelSet: "edit", showSource: false, expectedLabel: "Edit", expectsAriaPressed: false },
  { labelSet: "edit", showSource: true, expectedLabel: "Preview", expectsAriaPressed: false },
];

describe.each(TOGGLE_CASES)(
  "MarkdownSourceToggle labelSet=$labelSet showSource=$showSource",
  ({ labelSet, showSource, expectedLabel, expectsAriaPressed }) => {
    it(`renders the "${expectedLabel}" label and ${expectsAriaPressed ? "tracks" : "omits"} aria-pressed`, () => {
      render(
        <MarkdownSourceToggle
          showSource={showSource}
          onToggle={vi.fn()}
          testId="my-toggle"
          labelSet={labelSet}
        />,
      );
      const toggle = screen.getByTestId("my-toggle");
      expect(toggle).toHaveTextContent(expectedLabel);
      if (expectsAriaPressed) {
        expect(toggle).toHaveAttribute("aria-pressed", String(showSource));
      } else {
        expect(toggle).not.toHaveAttribute("aria-pressed");
      }
    });
  },
);

describe("MarkdownSourceToggle", () => {
  it("defaults labelSet to 'source' when omitted", () => {
    render(<MarkdownSourceToggle showSource={false} onToggle={vi.fn()} testId="my-toggle" />);
    const toggle = screen.getByTestId("my-toggle");
    expect(toggle).toHaveTextContent("View source");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
  });

  it("calls onToggle when clicked", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<MarkdownSourceToggle showSource={false} onToggle={onToggle} testId="my-toggle" />);
    await user.click(screen.getByTestId("my-toggle"));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("is operable by keyboard, since it renders a native button", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<MarkdownSourceToggle showSource={false} onToggle={onToggle} testId="my-toggle" />);
    screen.getByTestId("my-toggle").focus();
    await user.keyboard("{Enter}");
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
