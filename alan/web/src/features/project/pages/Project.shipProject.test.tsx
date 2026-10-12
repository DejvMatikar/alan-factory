import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import {
  BASE_PROJECT,
  EMPTY_DOC,
  mockDoc,
  mockProject,
  renderAt,
} from "../../../test/projectPageHarness";
import { server } from "../../../test/server";

describe("Project", () => {
  describe("ship project", () => {
    it("shows a ready 'mark shipped' button when ready_to_complete is true, and completing shows the shipped state with the refreshed final summary", async () => {
      mockProject({ ready_to_complete: true });
      let docCalls = 0;
      server.use(
        http.get("/api/projects/alpha/doc", () => {
          docCalls += 1;
          return HttpResponse.json({
            ...EMPTY_DOC,
            decision_log: docCalls === 1 ? "" : 'Project "Alpha" marked complete.',
          });
        }),
      );
      let captured: unknown = null;
      server.use(
        http.post("/api/projects/alpha/complete", async ({ request }) => {
          captured = await request.json();
          return HttpResponse.json({ ...BASE_PROJECT, status: "completed" });
        }),
      );
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("project-complete-button"));
      expect(await screen.findByTestId("project-complete-shipped")).toBeInTheDocument();
      expect(captured).toEqual({ force: false, reason: "" });
      expect(screen.queryByTestId("project-complete-button")).not.toBeInTheDocument();
      const summary = await screen.findByTestId("project-complete-summary");
      expect(summary).toHaveTextContent('Project "Alpha" marked complete.');
      expect(docCalls).toBe(2);
    });

    it("shows a force-with-reason disclosure when ready_to_complete is false, disabling submit until a reason is entered", async () => {
      mockProject({ ready_to_complete: false });
      mockDoc();
      let captured: unknown = null;
      server.use(
        http.post("/api/projects/alpha/complete", async ({ request }) => {
          captured = await request.json();
          return HttpResponse.json({ ...BASE_PROJECT, status: "completed" });
        }),
      );
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      expect(screen.queryByTestId("project-complete-button")).not.toBeInTheDocument();
      expect(screen.getByTestId("project-force-complete-submit")).toBeDisabled();
      await user.type(
        screen.getByTestId("project-force-reason"),
        "shipping now, blockers waived",
      );
      expect(screen.getByTestId("project-force-complete-submit")).not.toBeDisabled();
      await user.click(screen.getByTestId("project-force-complete-submit"));
      expect(await screen.findByTestId("project-complete-shipped")).toBeInTheDocument();
      expect(captured).toEqual({ force: true, reason: "shipping now, blockers waived" });
    });

    it("renders a static shipped state with the final summary when the project is already completed", async () => {
      mockProject({ status: "completed", ready_to_complete: false });
      mockDoc({ decision_log: 'Project "Alpha" marked complete at 2026-07-10T00:00:00Z.' });
      renderAt();
      const shipped = await screen.findByTestId("project-complete-shipped");
      expect(shipped).toHaveTextContent("shipped");
      expect(screen.queryByTestId("project-complete-button")).not.toBeInTheDocument();
      expect(screen.queryByTestId("project-force-complete-disclosure")).not.toBeInTheDocument();
      const summary = await screen.findByTestId("project-complete-summary");
      expect(summary).toHaveTextContent("marked complete at 2026-07-10T00:00:00Z");
    });

    it("surfaces a server error inline when the ready-path complete call fails", async () => {
      mockProject({ ready_to_complete: true });
      mockDoc();
      server.use(
        http.post("/api/projects/alpha/complete", () =>
          HttpResponse.json({ detail: "project_already_completed" }, { status: 409 }),
        ),
      );
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("project-complete-button"));
      expect(await screen.findByTestId("project-complete-error")).toHaveTextContent(
        "project_already_completed",
      );
    });

    it("skips the doc refetch and shows no summary yet when the project doc is still loading at completion time", async () => {
      mockProject({ ready_to_complete: true });
      server.use(
        http.get("/api/projects/alpha/doc", async () => {
          await new Promise(() => undefined);
          return HttpResponse.json(EMPTY_DOC);
        }),
        http.post("/api/projects/alpha/complete", () =>
          HttpResponse.json({ ...BASE_PROJECT, status: "completed" }),
        ),
      );
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("project-complete-button"));
      expect(await screen.findByTestId("project-complete-shipped")).toBeInTheDocument();
      expect(screen.queryByTestId("project-complete-summary")).not.toBeInTheDocument();
    });

    it("surfaces a server error inline when the forced complete call fails", async () => {
      mockProject({ ready_to_complete: false });
      mockDoc();
      server.use(
        http.post("/api/projects/alpha/complete", () =>
          HttpResponse.json({ detail: "boom" }, { status: 500 }),
        ),
      );
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      await user.type(screen.getByTestId("project-force-reason"), "reason text");
      await user.click(screen.getByTestId("project-force-complete-submit"));
      expect(await screen.findByTestId("project-force-complete-error")).toHaveTextContent("500");
    });
  });
});
