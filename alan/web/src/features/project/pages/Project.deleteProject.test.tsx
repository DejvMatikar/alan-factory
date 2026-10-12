import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { mockDoc, mockProject, renderAt, trackProjectDelete } from "../../../test/projectPageHarness";
import { server } from "../../../test/server";

describe("Project", () => {
  describe("delete project action", () => {
    it("disables delete and explains why when the project still has workspaces", async () => {
      mockProject({
        workspaces: [{ task_id: "ws-a", title: "A", status: "active", pr_number: null }],
      });
      mockDoc();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      expect(screen.getByTestId("project-delete-button")).toBeDisabled();
      expect(screen.getByTestId("project-delete-blocked-hint")).toHaveTextContent(
        "Archive it instead",
      );
      expect(screen.queryByTestId("delete-project-dialog")).not.toBeInTheDocument();
    });

    it("opens a confirmation dialog before deleting a workspace-free project", async () => {
      mockProject();
      mockDoc();
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      expect(screen.getByTestId("project-delete-button")).not.toBeDisabled();
      await user.click(screen.getByTestId("project-delete-button"));
      expect(screen.getByTestId("delete-project-dialog")).toBeInTheDocument();
    });

    it("deletes the project and navigates to the project list on confirm", async () => {
      mockProject();
      mockDoc();
      server.use(http.delete("/api/projects/alpha", () => new HttpResponse(null, { status: 204 })));
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      await user.click(screen.getByTestId("project-delete-button"));
      await user.click(screen.getByTestId("delete-project-confirm"));

      expect(await screen.findByTestId("projects-list-page")).toBeInTheDocument();
    });

    it("shows a friendly, actionable error when delete is blocked by a referencing workspace (TOCTOU race)", async () => {
      mockProject();
      mockDoc();
      server.use(
        http.delete("/api/projects/alpha", () =>
          HttpResponse.json({ detail: "project_has_workspaces" }, { status: 409 }),
        ),
      );
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      await user.click(screen.getByTestId("project-delete-button"));
      await user.click(screen.getByTestId("delete-project-confirm"));

      const error = await screen.findByTestId("delete-project-error");
      expect(error).toHaveTextContent("project_has_workspaces");
      expect(error).toHaveTextContent("archive the project instead");
      expect(screen.getByTestId("delete-project-dialog")).toBeInTheDocument();
    });

    it("surfaces a generic server error without appending the workspaces hint", async () => {
      mockProject();
      mockDoc();
      server.use(
        http.delete("/api/projects/alpha", () =>
          HttpResponse.json({ detail: "boom" }, { status: 500 }),
        ),
      );
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      await user.click(screen.getByTestId("project-delete-button"));
      await user.click(screen.getByTestId("delete-project-confirm"));

      const error = await screen.findByTestId("delete-project-error");
      expect(error).toHaveTextContent("500");
      expect(error).not.toHaveTextContent("archive the project instead");
    });

    it("Cancel closes the delete dialog without calling the server", async () => {
      mockProject();
      mockDoc();
      const deleted = trackProjectDelete();
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      await user.click(screen.getByTestId("project-delete-button"));
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByTestId("delete-project-dialog")).not.toBeInTheDocument();
      expect(deleted()).toBe(false);
    });

    it("Escape closes the delete dialog", async () => {
      mockProject();
      mockDoc();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      fireEvent.click(screen.getByTestId("project-delete-button"));
      const form = screen.getByTestId("delete-project-dialog").querySelector("form")!;
      fireEvent.keyDown(form, { key: "Escape" });
      expect(screen.queryByTestId("delete-project-dialog")).not.toBeInTheDocument();
    });

    it("blocks Escape dismissal while a delete request is in flight", async () => {
      mockProject();
      mockDoc();
      server.use(
        http.delete("/api/projects/alpha", async () => {
          await new Promise(() => undefined);
          return new HttpResponse(null, { status: 204 });
        }),
      );
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      await user.click(screen.getByTestId("project-delete-button"));
      await user.click(screen.getByTestId("delete-project-confirm"));
      expect(screen.getByTestId("delete-project-confirm")).toHaveTextContent("Deleting…");

      const form = screen.getByTestId("delete-project-dialog").querySelector("form")!;
      fireEvent.keyDown(form, { key: "Escape" });
      expect(screen.getByTestId("delete-project-dialog")).toBeInTheDocument();
    });

    it("the dialog's native cancel event closes it when idle", async () => {
      mockProject();
      mockDoc();
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      await user.click(screen.getByTestId("project-delete-button"));
      const dialog = screen.getByTestId("delete-project-dialog");
      fireEvent(dialog, new Event("cancel", { cancelable: true }));
      expect(screen.queryByTestId("delete-project-dialog")).not.toBeInTheDocument();
    });

    it("ignores a plain form submit event", async () => {
      mockProject();
      mockDoc();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      fireEvent.click(screen.getByTestId("project-delete-button"));
      const form = screen.getByTestId("delete-project-dialog").querySelector("form")!;
      fireEvent.submit(form);
      expect(screen.getByTestId("delete-project-dialog")).toBeInTheDocument();
    });
  });
});
