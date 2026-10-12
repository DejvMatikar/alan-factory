import { describe, expect, it } from "vitest";

import {
  ariaCurrentSelection,
  selectionRowSurfaceClass,
  selectionTextClass,
  SELECTION_FOCUS_CLASS,
  SELECTION_HOVER_CLASS,
  SELECTION_INTERACTIVE_CLASS,
  SELECTION_ROW_SELECTED_CLASS,
  SELECTION_TEXT_SELECTED_CLASS,
  SELECTION_TEXT_UNSELECTED_CLASS,
  UNSELECTED_PREVIEW_TEXT_CLASS,
} from "./selection";

describe("selectionTextClass", () => {
  it("returns the primary text-contrast step when selected", () => {
    expect(selectionTextClass(true)).toBe(SELECTION_TEXT_SELECTED_CLASS);
  });

  it("returns the muted text-contrast step when unselected", () => {
    expect(selectionTextClass(false)).toBe(SELECTION_TEXT_UNSELECTED_CLASS);
  });
});

describe("ariaCurrentSelection", () => {
  it("returns 'true' when selected", () => {
    expect(ariaCurrentSelection(true)).toBe("true");
  });

  it("returns undefined when unselected, never 'page'", () => {
    expect(ariaCurrentSelection(false)).toBeUndefined();
  });
});

describe("pre-existing export literals stay byte-identical", () => {
  it("pins SELECTION_INTERACTIVE_CLASS", () => {
    expect(SELECTION_INTERACTIVE_CLASS).toBe(
      "hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60",
    );
  });

  it("pins SELECTION_TEXT_SELECTED_CLASS and SELECTION_TEXT_UNSELECTED_CLASS", () => {
    expect(SELECTION_TEXT_SELECTED_CLASS).toBe("text-text-primary");
    expect(SELECTION_TEXT_UNSELECTED_CLASS).toBe("text-text-muted");
  });
});

describe("SELECTION_HOVER_CLASS / SELECTION_FOCUS_CLASS partition", () => {
  it("concatenate to exactly SELECTION_INTERACTIVE_CLASS", () => {
    expect(`${SELECTION_HOVER_CLASS} ${SELECTION_FOCUS_CLASS}`).toBe(SELECTION_INTERACTIVE_CLASS);
  });

  it("carries the hover treatment on the hover half only", () => {
    expect(SELECTION_HOVER_CLASS).toBe("hover:bg-surface-elevated");
    expect(SELECTION_FOCUS_CLASS).not.toContain("hover:");
  });

  it("carries the focus-visible ring on the focus half only", () => {
    expect(SELECTION_FOCUS_CLASS).toContain("focus-visible:ring-text-primary/60");
    expect(SELECTION_HOVER_CLASS).not.toContain("focus-visible:");
  });
});

describe("selectionRowSurfaceClass", () => {
  it("returns the raised-surface class when selected", () => {
    expect(selectionRowSurfaceClass(true)).toBe(SELECTION_ROW_SELECTED_CLASS);
  });

  it("returns an empty string when unselected", () => {
    expect(selectionRowSurfaceClass(false)).toBe("");
  });
});

describe("UNSELECTED_PREVIEW_TEXT_CLASS", () => {
  it("pins the neutral text class, now the only preview text class — selection no longer carries an accent-coloured preview", () => {
    expect(UNSELECTED_PREVIEW_TEXT_CLASS).toBe("text-text-primary");
  });
});
