import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { NodeStatus } from "../api";
import NodeStatusBadge from "./NodeStatusBadge";

const STATUS_EXPECTATIONS: readonly { status: NodeStatus; tint: string }[] = [
  { status: "pending", tint: "slate" },
  { status: "running", tint: "blue" },
  { status: "success", tint: "green" },
  { status: "failed", tint: "red" },
  { status: "interrupted", tint: "amber" },
];

describe("NodeStatusBadge", () => {
  for (const { status, tint } of STATUS_EXPECTATIONS) {
    it(`renders the ${status} status with the ${tint} tinted-translucent pair`, () => {
      render(<NodeStatusBadge status={status} />);
      const el = screen.getByTestId(`node-status-${status}`);
      expect(el).toHaveTextContent(status);
      expect(el.className).toContain(`bg-${tint}-500/15`);
      expect(el.className).toContain(`text-${tint}-300`);
    });
  }
});
