import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import {
  BASE_PROJECT,
  mockDoc,
  mockProject,
  renderAt,
  trackProjectPatch,
} from "../../../test/projectPageHarness";
import { server } from "../../../test/server";

describe("Project", () => {
  describe("autonomy + budget editor", () => {
    it("renders the current autonomy and budget values", async () => {
      mockProject({ autonomy: "auto_continue", budget_usd: 42.5 });
      mockDoc();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      expect(screen.getByTestId("project-autonomy-select")).toHaveValue("auto_continue");
      expect(screen.getByTestId("project-budget-input")).toHaveValue("42.5");
    });

    it("describes both autonomy modes' real destination, with no inbox reference", async () => {
      mockProject();
      mockDoc();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      const help = screen.getByTestId("project-autonomy-help");
      expect(help).toHaveTextContent(/appears in the Roadmap section below/);
      expect(help).toHaveTextContent(/never resolves a decision/);
      expect(help).toHaveTextContent(/implementation, implementation_fix, verification, pr_merge, deploy_verify/);
      expect(within(help).queryByRole("link")).not.toBeInTheDocument();
      expect(help).not.toHaveTextContent("inbox");
    });

    it("renders an empty budget input with a placeholder when no budget is set", async () => {
      mockProject();
      mockDoc();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      const input = screen.getByTestId("project-budget-input");
      expect(input).toHaveValue("");
      expect(input).toHaveAttribute("placeholder", "No budget set");
    });

    it("saves the autonomy dial and budget via PATCH, and the change persists across reload", async () => {
      let stored = { ...BASE_PROJECT };
      server.use(
        http.get("/api/projects/alpha", () => HttpResponse.json(stored)),
        http.get("/api/workspaces", () => HttpResponse.json([])),
        http.patch("/api/projects/alpha", async ({ request }) => {
          const body = (await request.json()) as { autonomy: string; budget_usd: number | null };
          stored = { ...stored, ...body };
          return HttpResponse.json(stored);
        }),
      );
      mockDoc();
      const user = userEvent.setup();
      const { unmount } = renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });

      await user.selectOptions(screen.getByTestId("project-autonomy-select"), "auto_continue");
      await user.clear(screen.getByTestId("project-budget-input"));
      await user.type(screen.getByTestId("project-budget-input"), "25");
      await user.click(screen.getByTestId("project-autonomy-save"));

      expect(await screen.findByTestId("project-autonomy-saved")).toBeInTheDocument();
      expect(stored.autonomy).toBe("auto_continue");
      expect(stored.budget_usd).toBe(25);

      unmount();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      expect(screen.getByTestId("project-autonomy-select")).toHaveValue("auto_continue");
      expect(screen.getByTestId("project-budget-input")).toHaveValue("25");
    });

    it("clearing the budget input saves a null budget", async () => {
      mockProject({ budget_usd: 10 });
      mockDoc();
      let capturedBody: unknown = null;
      server.use(
        http.patch("/api/projects/alpha", async ({ request }) => {
          capturedBody = await request.json();
          return HttpResponse.json({ ...BASE_PROJECT, budget_usd: null });
        }),
      );
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      await user.clear(screen.getByTestId("project-budget-input"));
      await user.click(screen.getByTestId("project-autonomy-save"));
      await screen.findByTestId("project-autonomy-saved");
      expect(capturedBody).toEqual({ autonomy: "propose", budget_usd: null });
    });

    it("rejects a negative budget without calling the server", async () => {
      mockProject();
      mockDoc();
      const patched = trackProjectPatch();
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      await user.type(screen.getByTestId("project-budget-input"), "-5");
      await user.click(screen.getByTestId("project-autonomy-save"));
      expect(await screen.findByTestId("project-autonomy-error")).toHaveTextContent(
        "Budget must be a non-negative number.",
      );
      expect(patched()).toBe(false);
    });

    it("rejects a non-numeric budget without calling the server", async () => {
      mockProject();
      mockDoc();
      const patched = trackProjectPatch();
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      fireEvent.change(screen.getByTestId("project-budget-input"), { target: { value: "abc" } });
      await user.click(screen.getByTestId("project-autonomy-save"));
      expect(await screen.findByTestId("project-autonomy-error")).toHaveTextContent(
        "Budget must be a non-negative number.",
      );
      expect(patched()).toBe(false);
    });

    it("surfaces a server error when saving fails", async () => {
      mockProject();
      mockDoc();
      server.use(
        http.patch("/api/projects/alpha", () =>
          HttpResponse.json({ detail: "boom" }, { status: 500 }),
        ),
      );
      const user = userEvent.setup();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      await user.click(screen.getByTestId("project-autonomy-save"));
      expect(await screen.findByTestId("project-autonomy-error")).toHaveTextContent("500");
    });
  });
});
