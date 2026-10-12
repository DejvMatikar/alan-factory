import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { mockDoc, mockProject, renderAt } from "../../../test/projectPageHarness";

describe("Project", () => {
  describe("burn view", () => {
    it("shows a visible unavailable state (never a hidden section) when the server sends no budget data", async () => {
      mockProject({ budget: null });
      mockDoc();
      renderAt();
      const burn = await screen.findByTestId("project-burn");
      const unavailable = await screen.findByTestId("project-burn-unavailable");
      expect(unavailable).toHaveTextContent("Spend data unavailable.");
      expect(burn).toContainElement(unavailable);
    });

    it("renders the normal state with no budget set (no 'of $X budget' suffix, no warning glyph)", async () => {
      mockProject({
        budget: { state: "normal", burn_usd: 15, budget_usd: null, remaining_usd: null },
      });
      mockDoc();
      renderAt();
      const burn = await screen.findByTestId("project-burn");
      expect(burn).toHaveTextContent("Total spend: $15.00");
      expect(burn).not.toHaveTextContent("of $");
      expect(screen.getByTestId("project-budget-pill")).toHaveTextContent("Normal");
      expect(burn).toHaveTextContent("Spend is within budget.");
      expect(burn).not.toHaveTextContent("⚠");
      expect(burn).toHaveTextContent(
        "Total spend across every category, including planning-chat and collision-check costs.",
      );
    });

    it("renders the approaching-limit state with distinct prose and the amber pill token", async () => {
      mockProject({
        budget: { state: "approaching", burn_usd: 85, budget_usd: 100, remaining_usd: 15 },
      });
      mockDoc();
      renderAt();
      const burn = await screen.findByTestId("project-burn");
      expect(burn).toHaveTextContent("Total spend: $85.00 of $100.00 budget");
      const pill = screen.getByTestId("project-budget-pill");
      expect(pill).toHaveTextContent("Approaching limit");
      expect(pill).toHaveClass("text-btn-reset");
      expect(burn).toHaveTextContent("Spend is approaching this project's budget limit.");
    });

    it("renders the paused state stating in words that new work is paused and in-flight work will finish", async () => {
      mockProject({
        budget: { state: "paused", burn_usd: 120, budget_usd: 100, remaining_usd: -20 },
      });
      mockDoc();
      renderAt();
      const burn = await screen.findByTestId("project-burn");
      const pill = screen.getByTestId("project-budget-pill");
      expect(pill).toHaveTextContent("Paused");
      expect(pill).toHaveClass("text-btn-destroy");
      expect(burn).toHaveTextContent("New work is paused");
      expect(burn).toHaveTextContent("in flight will finish normally");
    });
  });
});
