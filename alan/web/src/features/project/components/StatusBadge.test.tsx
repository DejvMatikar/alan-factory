import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RoadmapDeliveryStatus } from "../../../api";
import StatusBadge from "./StatusBadge";

const STATUS_EXPECTATIONS: readonly {
  status: RoadmapDeliveryStatus;
  label: string;
  tint: string;
}[] = [
  { status: "not_started", label: "not started", tint: "zinc" },
  { status: "in_flight", label: "in flight", tint: "blue" },
  { status: "delivered", label: "delivered", tint: "green" },
];

describe("StatusBadge (roadmap)", () => {
  for (const { status, label, tint } of STATUS_EXPECTATIONS) {
    it(`renders the ${status} status with the ${tint} tinted-translucent pair`, () => {
      render(<StatusBadge status={status} />);
      const el = screen.getByTestId(`roadmap-status-${status}`);
      expect(el).toHaveTextContent(label);
      expect(el.className).toContain(`bg-${tint}-500/15`);
      expect(el.className).toContain(`text-${tint}-300`);
      expect(el.querySelector("button, select, a, input")).toBeNull();
    });
  }
});
