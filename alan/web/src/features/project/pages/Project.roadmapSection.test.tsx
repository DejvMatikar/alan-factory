import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { BASE_PROJECT, mockDoc, mockProject, renderAt } from "../../../test/projectPageHarness";
import { server } from "../../../test/server";

describe("Project", () => {
  describe("roadmap section", () => {
    function roadmapItem(
      overrides: Partial<{
        id: string;
        title: string;
        text: string;
        order: number;
        status: string;
        merged_prs: number[];
      }> = {},
    ) {
      return {
        id: "itm_1",
        title: "Ship the widget",
        text: "Build the core widget end to end.",
        order: 0,
        status: "not_started",
        merged_prs: [],
        ...overrides,
      };
    }

    it("shows an empty state with an Import roadmap action when no roadmap is imported", async () => {
      mockProject();
      mockDoc();
      renderAt();
      expect(await screen.findByTestId("project-roadmap-empty")).toHaveTextContent(
        "No roadmap imported yet.",
      );
      expect(screen.queryByTestId("project-roadmap-list")).not.toBeInTheDocument();
      expect(screen.queryByTestId("propose-roadmap-changes-open")).not.toBeInTheDocument();
    });

    it("renders imported items ordered by their order field, not array position", async () => {
      mockProject({
        roadmap: [
          roadmapItem({ id: "itm_2", title: "Polish the dashboard", order: 1, status: "in_flight" }),
          roadmapItem({
            id: "itm_1",
            title: "Ship the widget",
            order: 0,
            status: "delivered",
          }),
        ],
      });
      mockDoc();
      renderAt();
      const list = await screen.findByTestId("project-roadmap-list");
      const items = list.querySelectorAll(":scope > li");
      expect(items).toHaveLength(2);
      expect(items[0]).toHaveTextContent("Ship the widget");
      expect(items[1]).toHaveTextContent("Polish the dashboard");
      expect(screen.getByTestId("roadmap-status-delivered")).toBeInTheDocument();
      expect(screen.getByTestId("roadmap-status-in_flight")).toBeInTheDocument();
    });

    it("surfaces merged PR links for a delivered item", async () => {
      mockProject({
        engine_key: "DejvMatikar/alan",
        roadmap: [roadmapItem({ status: "delivered", merged_prs: [7, 9] })],
      });
      mockDoc();
      renderAt();
      await screen.findByTestId("project-roadmap-list");
      const link7 = screen.getByTestId("roadmap-item-pr-link-7");
      expect(link7).toHaveAttribute("href", "https://github.com/DejvMatikar/alan/pull/7");
      expect(link7).toHaveTextContent("PR #7");
      expect(link7).toHaveClass("text-btn-action", "underline");
      expect(screen.getByTestId("roadmap-item-pr-link-9")).toBeInTheDocument();
    });

    it("resolves merged PR links against a non-default engine's repo", async () => {
      mockProject({
        engine_key: "acme-corp/sprocket",
        roadmap: [roadmapItem({ status: "delivered", merged_prs: [7] })],
      });
      mockDoc();
      renderAt();
      const link7 = await screen.findByTestId("roadmap-item-pr-link-7");
      expect(link7).toHaveAttribute("href", "https://github.com/acme-corp/sprocket/pull/7");
    });

    it("renders a degraded, non-clickable merged-PR reference when the project has no engine_key", async () => {
      mockProject({
        engine_key: null,
        roadmap: [roadmapItem({ status: "delivered", merged_prs: [7] })],
      });
      mockDoc();
      renderAt();
      const el = await screen.findByTestId("roadmap-item-pr-link-7");
      expect(el.tagName).toBe("SPAN");
      expect(el).toHaveTextContent("PR #7");
      expect(el).toHaveTextContent("repository unresolved");
    });

    it("shows no merged-PR list for an item with none", async () => {
      mockProject({ roadmap: [roadmapItem({ status: "in_flight" })] });
      mockDoc();
      renderAt();
      await screen.findByTestId("project-roadmap-list");
      expect(screen.queryByTestId("roadmap-item-merged-prs")).not.toBeInTheDocument();
    });

    it("renders non-empty item text as a Details markdown panel and omits it when text is blank", async () => {
      mockProject({
        roadmap: [
          roadmapItem({ id: "itm_1", text: "Build the core widget end to end." }),
          roadmapItem({ id: "itm_2", title: "No details", order: 1, text: "" }),
        ],
      });
      mockDoc();
      renderAt();
      const details = await screen.findByTestId("roadmap-item-text-itm_1");
      expect(details).toHaveTextContent("Build the core widget end to end.");
      expect(screen.getByText("Details")).toBeInTheDocument();
      expect(screen.queryByTestId("roadmap-item-text-itm_2")).not.toBeInTheDocument();
    });

    it("shows a Start work link only for a not-started item, targeting Build chat with the project and item stamped", async () => {
      mockProject({
        roadmap: [
          roadmapItem({ id: "itm_1", status: "not_started", text: "" }),
          roadmapItem({ id: "itm_2", status: "delivered", order: 1, merged_prs: [1] }),
        ],
      });
      mockDoc();
      renderAt();
      await screen.findByTestId("project-roadmap-list");
      const startLink = screen.getByTestId("roadmap-item-dispatch-itm_1");
      expect(startLink).toHaveAttribute("href", "/build?project=alpha&roadmapItem=itm_1");
      expect(screen.queryByTestId("roadmap-item-dispatch-itm_2")).not.toBeInTheDocument();
    });

    it("opens the import modal, previews, imports, and refreshes the section with the imported items", async () => {
      mockDoc();
      let getProjectCalls = 0;
      server.use(
        http.get("/api/projects/alpha", () => {
          getProjectCalls += 1;
          if (getProjectCalls === 1) return HttpResponse.json(BASE_PROJECT);
          return HttpResponse.json({
            ...BASE_PROJECT,
            roadmap: [roadmapItem({ id: "itm_1", title: "Ship the widget" })],
          });
        }),
        http.post("/api/projects/alpha/roadmap/preview", async ({ request }) => {
          const body = (await request.json()) as { markdown: string };
          expect(body.markdown).toContain("Ship the widget");
          return HttpResponse.json({ items: [{ title: "Ship the widget", text: "Build it." }] });
        }),
        http.post("/api/projects/alpha/roadmap/import", () =>
          HttpResponse.json(
            { items: [{ id: "itm_1", title: "Ship the widget", text: "Build it." }] },
            { status: 201 },
          ),
        ),
      );
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("import-roadmap-open"));
      expect(screen.getByTestId("import-roadmap-dialog")).toBeInTheDocument();
      await user.type(screen.getByTestId("import-roadmap-markdown"), "## Ship the widget");
      await user.click(screen.getByTestId("import-roadmap-preview-button"));
      expect(await screen.findByTestId("import-roadmap-preview-list")).toHaveTextContent(
        "Ship the widget",
      );
      await user.click(screen.getByTestId("import-roadmap-submit"));
      expect(screen.queryByTestId("import-roadmap-dialog")).not.toBeInTheDocument();
      expect(await screen.findByTestId("project-roadmap-list")).toHaveTextContent(
        "Ship the widget",
      );
      expect(screen.queryByTestId("import-roadmap-open")).not.toBeInTheDocument();
    });

    it("keeps the current view (fails open) if the refetch after a successful import fails", async () => {
      mockDoc();
      let getProjectCalls = 0;
      server.use(
        http.get("/api/projects/alpha", () => {
          getProjectCalls += 1;
          if (getProjectCalls === 1) return HttpResponse.json(BASE_PROJECT);
          return HttpResponse.json({ detail: "boom" }, { status: 500 });
        }),
        http.post("/api/projects/alpha/roadmap/preview", () =>
          HttpResponse.json({ items: [{ title: "Ship the widget", text: "" }] }),
        ),
        http.post("/api/projects/alpha/roadmap/import", () =>
          HttpResponse.json(
            { items: [{ id: "itm_1", title: "Ship the widget", text: "" }] },
            { status: 201 },
          ),
        ),
      );
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("import-roadmap-open"));
      await user.type(screen.getByTestId("import-roadmap-markdown"), "## Ship the widget");
      await user.click(screen.getByTestId("import-roadmap-preview-button"));
      await screen.findByTestId("import-roadmap-preview-list");
      await user.click(screen.getByTestId("import-roadmap-submit"));
      expect(screen.queryByTestId("import-roadmap-dialog")).not.toBeInTheDocument();
      await waitFor(() => expect(getProjectCalls).toBe(2));
      expect(screen.getByTestId("project-roadmap-empty")).toBeInTheDocument();
    });

    it("Escape closes the import modal without importing", async () => {
      mockProject();
      mockDoc();
      const user = userEvent.setup();
      renderAt();
      await user.click(await screen.findByTestId("import-roadmap-open"));
      const form = screen.getByTestId("import-roadmap-dialog").querySelector("form")!;
      fireEvent.keyDown(form, { key: "Escape" });
      expect(screen.queryByTestId("import-roadmap-dialog")).not.toBeInTheDocument();
    });
  });
});
