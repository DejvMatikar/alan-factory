import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { mockDoc, mockProject, renderAt } from "../../../test/projectPageHarness";
import { server } from "../../../test/server";

describe("Project", () => {
  describe("propose-amendment action", () => {
    it("opens a modal prefilled with the current vision/scope", async () => {
      mockProject();
      mockDoc({ vision: "Current vision", scope: "Current scope" });
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("propose-amendment-button"));
      expect(screen.getByTestId("propose-amendment-dialog")).toBeInTheDocument();
      expect(screen.getByTestId("amendment-vision")).toHaveValue("Current vision");
      expect(screen.getByTestId("amendment-scope")).toHaveValue("Current scope");
      expect(screen.getByTestId("amendment-decision-entry")).toHaveValue("");
    });

    it("disables submit until vision, scope, and a decision entry are all filled", async () => {
      mockProject();
      mockDoc();
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("propose-amendment-button"));
      expect(screen.getByTestId("propose-amendment-submit")).toBeDisabled();
      await user.type(screen.getByTestId("amendment-vision"), "V");
      await user.type(screen.getByTestId("amendment-scope"), "S");
      expect(screen.getByTestId("propose-amendment-submit")).toBeDisabled();
      await user.type(screen.getByTestId("amendment-decision-entry"), "D");
      expect(screen.getByTestId("propose-amendment-submit")).not.toBeDisabled();
    });

    it("proposes an amendment and shows a plain confirmation with no link", async () => {
      mockProject();
      mockDoc();
      let capturedBody: unknown = null;
      server.use(
        http.post("/api/projects/alpha/amendments", async ({ request }) => {
          capturedBody = await request.json();
          return HttpResponse.json(
            { fingerprint: "abc123", project_id: "alpha", proposed_at: "2026-07-08T00:00:00Z" },
            { status: 201 },
          );
        }),
      );
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("propose-amendment-button"));
      await user.type(screen.getByTestId("amendment-vision"), "V");
      await user.type(screen.getByTestId("amendment-scope"), "S");
      await user.type(screen.getByTestId("amendment-decision-entry"), "D");
      await user.click(screen.getByTestId("propose-amendment-submit"));

      const success = await screen.findByTestId("propose-amendment-success");
      expect(success).toHaveTextContent("Amendment proposed.");
      expect(screen.queryByTestId("propose-amendment-dialog")).not.toBeInTheDocument();
      expect(capturedBody).toEqual({ vision: "V", scope: "S", decision_entry: "D" });

      expect(within(success).queryByRole("link")).not.toBeInTheDocument();
      expect(success).not.toHaveTextContent("inbox");
    });

    it("shows an inline error and keeps the modal open when proposing fails", async () => {
      mockProject();
      mockDoc();
      server.use(
        http.post("/api/projects/alpha/amendments", () =>
          HttpResponse.json({ detail: "boom" }, { status: 500 }),
        ),
      );
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("propose-amendment-button"));
      await user.type(screen.getByTestId("amendment-vision"), "V");
      await user.type(screen.getByTestId("amendment-scope"), "S");
      await user.type(screen.getByTestId("amendment-decision-entry"), "D");
      await user.click(screen.getByTestId("propose-amendment-submit"));

      const alert = await screen.findByTestId("propose-amendment-error");
      expect(alert).toHaveTextContent("500");
      expect(screen.getByTestId("propose-amendment-dialog")).toBeInTheDocument();
    });

    it("Escape closes the propose-amendment modal", async () => {
      mockProject();
      mockDoc();
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("propose-amendment-button"));
      const form = screen.getByTestId("propose-amendment-dialog").querySelector("form")!;
      fireEvent.keyDown(form, { key: "Escape" });
      expect(screen.queryByTestId("propose-amendment-dialog")).not.toBeInTheDocument();
    });

    it("Ctrl+Enter submits the propose-amendment modal", async () => {
      mockProject();
      mockDoc();
      server.use(
        http.post("/api/projects/alpha/amendments", () =>
          HttpResponse.json(
            { fingerprint: "abc123", project_id: "alpha", proposed_at: "2026-07-08T00:00:00Z" },
            { status: 201 },
          ),
        ),
      );
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("propose-amendment-button"));
      await user.type(screen.getByTestId("amendment-vision"), "V");
      await user.type(screen.getByTestId("amendment-scope"), "S");
      await user.type(screen.getByTestId("amendment-decision-entry"), "D");
      const form = screen.getByTestId("propose-amendment-dialog").querySelector("form")!;
      fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
      expect(await screen.findByTestId("propose-amendment-success")).toBeInTheDocument();
    });

    it("ignores Ctrl+Enter while fields are incomplete", async () => {
      mockProject();
      mockDoc();
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("propose-amendment-button"));
      const form = screen.getByTestId("propose-amendment-dialog").querySelector("form")!;
      fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
      expect(screen.getByTestId("propose-amendment-dialog")).toBeInTheDocument();
    });

    it("ignores a plain form submit event", async () => {
      mockProject();
      mockDoc();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      fireEvent.click(screen.getByTestId("propose-amendment-button"));
      const form = screen.getByTestId("propose-amendment-dialog").querySelector("form")!;
      fireEvent.submit(form);
      expect(screen.getByTestId("propose-amendment-dialog")).toBeInTheDocument();
    });

    it("blocks Escape and Ctrl+Enter while a propose request is in flight", async () => {
      server.use(
        http.post("/api/projects/alpha/amendments", async () => {
          await new Promise(() => undefined);
          return HttpResponse.json({ fingerprint: "x", project_id: "alpha", proposed_at: "" });
        }),
      );
      mockProject();
      mockDoc();
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("propose-amendment-button"));
      await user.type(screen.getByTestId("amendment-vision"), "V");
      await user.type(screen.getByTestId("amendment-scope"), "S");
      await user.type(screen.getByTestId("amendment-decision-entry"), "D");
      await user.click(screen.getByTestId("propose-amendment-submit"));
      expect(screen.getByTestId("propose-amendment-submit")).toHaveTextContent("Proposing…");

      const form = screen.getByTestId("propose-amendment-dialog").querySelector("form")!;
      fireEvent.keyDown(form, { key: "Escape" });
      fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
      expect(screen.getByTestId("propose-amendment-dialog")).toBeInTheDocument();
    });

    it("the dialog's native cancel event closes the modal when idle", async () => {
      mockProject();
      mockDoc();
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("propose-amendment-button"));
      const dialog = screen.getByTestId("propose-amendment-dialog");
      fireEvent(dialog, new Event("cancel", { cancelable: true }));
      expect(screen.queryByTestId("propose-amendment-dialog")).not.toBeInTheDocument();
    });

    it("blocks the dialog's native cancel event while a propose request is in flight", async () => {
      server.use(
        http.post("/api/projects/alpha/amendments", async () => {
          await new Promise(() => undefined);
          return HttpResponse.json({ fingerprint: "x", project_id: "alpha", proposed_at: "" });
        }),
      );
      mockProject();
      mockDoc();
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("propose-amendment-button"));
      await user.type(screen.getByTestId("amendment-vision"), "V");
      await user.type(screen.getByTestId("amendment-scope"), "S");
      await user.type(screen.getByTestId("amendment-decision-entry"), "D");
      await user.click(screen.getByTestId("propose-amendment-submit"));
      expect(screen.getByTestId("propose-amendment-submit")).toHaveTextContent("Proposing…");

      const dialog = screen.getByTestId("propose-amendment-dialog");
      fireEvent(dialog, new Event("cancel", { cancelable: true }));
      expect(screen.getByTestId("propose-amendment-dialog")).toBeInTheDocument();
    });

    it("Cancel button closes the modal", async () => {
      mockProject();
      mockDoc();
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("propose-amendment-button"));
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByTestId("propose-amendment-dialog")).not.toBeInTheDocument();
    });
  });
});
