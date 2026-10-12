import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { settleDefaultEngineKey, STUB_DEFAULT_ENGINE_KEY } from "../../../test/engineFixtures";
import { mockDoc, mockProject, renderAt } from "../../../test/projectPageHarness";
import { server } from "../../../test/server";

describe("Project", () => {
  describe("engine ops panel", () => {
    function mockEngineEndpoints(engineKey: string) {
      const [owner, repo] = engineKey.split("/");
      server.use(
        http.get(`/api/engines/${owner}/${repo}`, () =>
          HttpResponse.json({ repo: { owner, repo }, posture: "propose" }),
        ),
        http.get(`/api/engines/${owner}/${repo}/pool`, () =>
          HttpResponse.json({
            slug: engineKey,
            size: 1,
            free_count: 1,
            leased_count: 0,
            warm: true,
            quiescing: false,
            disk_usage_bytes: 0,
          }),
        ),
        http.get(`/api/engines/${owner}/${repo}/preflight`, () => HttpResponse.json(null)),
      );
    }

    it("does not render the ops panel when the project has no engine_key", async () => {
      mockProject({ engine_key: null });
      mockDoc();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      expect(screen.queryByTestId("engine-ops-panel")).not.toBeInTheDocument();
    });

    it("does not render the ops panel for a project bound to the default engine", async () => {
      settleDefaultEngineKey(STUB_DEFAULT_ENGINE_KEY);
      mockProject({ engine_key: STUB_DEFAULT_ENGINE_KEY });
      mockDoc();
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      expect(screen.queryByTestId("engine-ops-panel")).not.toBeInTheDocument();
    });

    it("renders the ops panel between Autonomy & budget and Project doc for a non-default engine", async () => {
      settleDefaultEngineKey(STUB_DEFAULT_ENGINE_KEY);
      mockProject({ engine_key: "acme/widget" });
      mockDoc();
      mockEngineEndpoints("acme/widget");
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      const panel = await screen.findByTestId("engine-ops-panel");
      expect(panel).toBeInTheDocument();
      expect(await screen.findByTestId("engine-ops-identity")).toHaveTextContent("acme/widget");

      const sections = Array.from(document.querySelectorAll('[data-testid="project-detail"] > *'));
      const budgetIdx = sections.findIndex((el) => el.textContent?.includes("Autonomy & budget"));
      const panelIdx = sections.indexOf(panel);
      const docIdx = sections.findIndex((el) => el.textContent?.includes("Project doc"));
      expect(budgetIdx).toBeGreaterThanOrEqual(0);
      expect(panelIdx).toBeGreaterThan(budgetIdx);
      expect(docIdx).toBeGreaterThan(panelIdx);
    });
  });
});
