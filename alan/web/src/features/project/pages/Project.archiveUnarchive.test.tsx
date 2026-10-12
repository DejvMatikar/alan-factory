import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { BASE_PROJECT, mockDoc, mockProject, renderAt } from "../../../test/projectPageHarness";
import { server } from "../../../test/server";

describe("Project", () => {
  describe("archive/unarchive action", () => {
    function mockPatchEchoesStatus() {
      server.use(
        http.patch("/api/projects/alpha", async ({ request }) => {
          const body = (await request.json()) as { status?: string };
          return HttpResponse.json({ ...BASE_PROJECT, status: body.status ?? BASE_PROJECT.status });
        }),
      );
    }

    it("archives an active project and updates the status badge", async () => {
      mockProject({ status: "active" });
      mockDoc();
      mockPatchEchoesStatus();
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      expect(screen.getByTestId("project-archive-toggle")).toHaveTextContent("Archive");

      await user.click(screen.getByTestId("project-archive-toggle"));

      expect(await screen.findByTestId("project-status-archived")).toBeInTheDocument();
      expect(screen.getByTestId("project-archive-toggle")).toHaveTextContent("Unarchive");
    });

    it("round-trips an archived project back to active", async () => {
      mockProject({ status: "archived" });
      mockDoc();
      mockPatchEchoesStatus();
      const user = userEvent.setup();
      renderAt();
      await screen.findByTestId("project-status-archived");
      expect(screen.getByTestId("project-archive-toggle")).toHaveTextContent("Unarchive");

      await user.click(screen.getByTestId("project-archive-toggle"));

      expect(await screen.findByTestId("project-status-active")).toBeInTheDocument();
      expect(screen.getByTestId("project-archive-toggle")).toHaveTextContent("Archive");
    });

    it("does not render the archive toggle for a completed (shipped) project", async () => {
      mockProject({ status: "completed" });
      mockDoc();
      renderAt();
      await screen.findByTestId("project-status-completed");
      expect(screen.queryByTestId("project-archive-toggle")).not.toBeInTheDocument();
    });

    it("shows an inline field-level error when the archive toggle fails, and resets to idle", async () => {
      mockProject({ status: "active" });
      mockDoc();
      server.use(
        http.patch("/api/projects/alpha", () =>
          HttpResponse.json({ detail: "boom" }, { status: 500 }),
        ),
      );
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      await user.click(screen.getByTestId("project-archive-toggle"));
      expect(await screen.findByTestId("project-archive-error")).toHaveTextContent("500");
      expect(screen.getByTestId("project-archive-toggle")).toHaveTextContent("Archive");
      expect(screen.getByTestId("project-archive-toggle")).not.toBeDisabled();
    });
  });
});
