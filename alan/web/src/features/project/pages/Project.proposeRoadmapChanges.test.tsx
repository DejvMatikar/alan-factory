import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { mockDoc, mockProject, renderAt } from "../../../test/projectPageHarness";
import { server } from "../../../test/server";

describe("Project", () => {
  describe("propose roadmap changes action", () => {
    function roadmapItem(
      overrides: Partial<{ id: string; title: string; text: string; order: number; status: string }> = {},
    ) {
      return {
        id: "itm_1",
        title: "Ship the widget",
        text: "",
        order: 0,
        status: "not_started",
        merged_prs: [],
        ...overrides,
      };
    }

    it("hides the action when no roadmap is imported", async () => {
      mockProject();
      mockDoc();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      expect(screen.queryByTestId("propose-roadmap-changes-open")).not.toBeInTheDocument();
    });

    it("opens the propose-changes modal and shows a plain confirmation with no link", async () => {
      mockProject({
        roadmap: [
          roadmapItem({ id: "itm_1", title: "Ship the widget", order: 0 }),
          roadmapItem({ id: "itm_2", title: "Polish the dashboard", order: 1 }),
        ],
      });
      mockDoc();
      server.use(
        http.post("/api/projects/alpha/roadmap/reorder", async ({ request }) => {
          const body = (await request.json()) as { ordered_ids: string[] };
          expect(body.ordered_ids).toEqual(["itm_2", "itm_1"]);
          return HttpResponse.json(
            { fingerprint: "abc123", project_id: "alpha", proposed_at: "2026-07-08T00:00:00Z" },
            { status: 201 },
          );
        }),
      );
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("propose-roadmap-changes-open"));
      expect(screen.getByTestId("propose-roadmap-changes-dialog")).toBeInTheDocument();
      await user.click(screen.getByTestId("propose-roadmap-reorder-down-itm_1"));
      await user.click(screen.getByTestId("propose-roadmap-submit"));

      const success = await screen.findByTestId("propose-roadmap-changes-success");
      expect(success).toHaveTextContent("Change proposed.");
      expect(screen.queryByTestId("propose-roadmap-changes-dialog")).not.toBeInTheDocument();
      expect(within(success).queryByRole("link")).not.toBeInTheDocument();
      expect(success).not.toHaveTextContent("inbox");
    });

    it("Cancel button closes the propose-changes modal without proposing", async () => {
      mockProject({ roadmap: [roadmapItem()] });
      mockDoc();
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("propose-roadmap-changes-open"));
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByTestId("propose-roadmap-changes-dialog")).not.toBeInTheDocument();
    });
  });
});
