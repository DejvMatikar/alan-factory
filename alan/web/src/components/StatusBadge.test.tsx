import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { WorkspaceStatus } from "../api";
import StatusBadge from "./StatusBadge";

function visibleText(el: Element): string {
  return Array.from(el.childNodes)
    .filter((node) => !(node instanceof Element && node.getAttribute("aria-hidden") === "true"))
    .map((node) => node.textContent ?? "")
    .join("");
}

const STATUS_EXPECTATIONS: readonly {
  status: WorkspaceStatus;
  label: string;
  token: string;
  glyph?: string;
}[] = [
  { status: "active", label: "active", token: "active" },
  { status: "awaiting_input", label: "awaiting input", token: "attention" },
  { status: "completed", label: "completed", token: "success" },
  { status: "archived", label: "archived", token: "neutral" },
  { status: "escalated", label: "escalated", token: "danger", glyph: "↑" },
  { status: "paused", label: "paused", token: "paused" },
  { status: "failed", label: "failed", token: "danger", glyph: "✕" },
  { status: "partial", label: "partial", token: "warning" },
];

describe("StatusBadge", () => {
  for (const { status, label, token, glyph } of STATUS_EXPECTATIONS) {
    it(`renders the ${status} status with the ${token} tinted-translucent pair`, () => {
      render(<StatusBadge status={status} />);
      const el = screen.getByTestId(`status-${status}`);
      expect(el).toHaveTextContent(label);
      expect(el.className).toContain(`bg-status-${token}-tint/15`);
      expect(el.className).toContain(`text-status-${token}-fg`);
      expect(el.className.startsWith("inline-block rounded px-2 py-0.5 text-xs")).toBe(true);
    });

    it(`gives ${status} ${glyph ? "a" : "no"} redundant glyph, announced only once`, () => {
      render(<StatusBadge status={status} />);
      const el = screen.getByTestId(`status-${status}`);
      const glyphEl = el.querySelector('[aria-hidden="true"]');
      if (glyph === undefined) {
        expect(glyphEl).toBeNull();
      } else {
        expect(glyphEl).not.toBeNull();
        expect(glyphEl).toHaveTextContent(glyph);
      }
      expect(visibleText(el)).toBe(label);
    });
  }

  it("gives escalated and failed visually distinct glyphs despite sharing the danger token", () => {
    const { unmount } = render(<StatusBadge status="escalated" />);
    const escalatedGlyph = screen
      .getByTestId("status-escalated")
      .querySelector('[aria-hidden="true"]')?.textContent;
    unmount();
    render(<StatusBadge status="failed" />);
    const failedGlyph = screen
      .getByTestId("status-failed")
      .querySelector('[aria-hidden="true"]')?.textContent;
    expect(escalatedGlyph).not.toBe(failedGlyph);
  });
});
