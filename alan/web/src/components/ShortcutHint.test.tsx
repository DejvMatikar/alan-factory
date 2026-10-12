import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ShortcutHint from "./ShortcutHint";

interface Combo {
  readonly label: string;
  readonly glyph: string;
  readonly spoken: string;
}

const COMBOS: readonly Combo[] = [
  { label: "Send", glyph: "⌘/⌃↵", spoken: "Command Enter or Control Enter" },
  { label: "Queue", glyph: "⌘/⌃↵", spoken: "Command Enter or Control Enter" },
  { label: "Stop", glyph: "Esc", spoken: "Escape" },
  { label: "Clear", glyph: "⌃⇧K", spoken: "Control Shift K" },
  { label: "Summarize", glyph: "⌃⇧S", spoken: "Control Shift S" },
  { label: "Build", glyph: "⌃⇧X", spoken: "Control Shift X" },
  { label: "Explore", glyph: "⌃⇧E", spoken: "Control Shift E" },
];

describe.each(COMBOS)("ShortcutHint — $label", ({ label, glyph, spoken }) => {
  it(`announces the ${label} shortcut in the button's accessible name, not by reading the glyph`, () => {
    render(
      <button type="button">
        {label} <ShortcutHint glyph={glyph} spoken={spoken} />
      </button>,
    );
    const button = screen.getByRole("button", { name: new RegExp(`${label}.*shortcut ${spoken}`) });
    expect(button).toBeInTheDocument();
  });
});

describe("ShortcutHint", () => {
  it("hides the glyph span from assistive technology", () => {
    render(<ShortcutHint glyph="⌘↵" spoken="Command Enter" />);
    expect(screen.getByText("⌘↵")).toHaveAttribute("aria-hidden", "true");
  });

  it("keeps the spoken form visually hidden but present in the DOM", () => {
    render(<ShortcutHint glyph="⌘↵" spoken="Command Enter" />);
    const spoken = screen.getByText(/shortcut Command Enter/);
    expect(spoken).toHaveClass("sr-only");
  });
});
