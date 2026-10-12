import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { EMPTY_DOC, mockDoc, mockProject, renderAt } from "../../../test/projectPageHarness";
import { server } from "../../../test/server";

describe("Project", () => {
  describe("project doc", () => {
    it("shows a loading state before the doc fetch resolves", async () => {
      mockProject();
      server.use(
        http.get("/api/projects/alpha/doc", async () => {
          await new Promise(() => undefined);
          return HttpResponse.json(EMPTY_DOC);
        }),
      );
      renderAt();
      await screen.findByRole("heading", { level: 2, name: "Alpha" });
      expect(screen.getByTestId("project-doc-loading")).toBeInTheDocument();
    });

    it("shows an error and a working retry button when the doc fetch fails", async () => {
      mockProject();
      let attempts = 0;
      server.use(
        http.get("/api/projects/alpha/doc", () => {
          attempts += 1;
          if (attempts === 1) {
            return HttpResponse.json({ detail: "boom" }, { status: 500 });
          }
          return HttpResponse.json(EMPTY_DOC);
        }),
      );
      const user = userEvent.setup();
      renderAt();
      const errorBox = await screen.findByTestId("project-doc-error");
      expect(errorBox).toHaveTextContent("500");

      await user.click(screen.getByTestId("project-doc-retry"));
      expect(await screen.findByTestId("project-doc")).toBeInTheDocument();
    });

    it("renders an honest empty state per anchor when the doc is blank", async () => {
      mockProject();
      mockDoc();
      renderAt();
      expect(await screen.findByTestId("project-doc-vision-empty")).toHaveTextContent(
        "No vision recorded yet.",
      );
      expect(screen.getByTestId("project-doc-scope-empty")).toHaveTextContent(
        "No scope recorded yet.",
      );
      expect(screen.getByTestId("project-doc-decision-log-empty")).toHaveTextContent(
        "No decisions recorded yet.",
      );
      expect(screen.getByTestId("project-doc-discovered-facts-empty")).toHaveTextContent(
        "No discovered facts yet.",
      );
    });

    it("renders non-empty anchor content as lightly-formatted markdown", async () => {
      mockProject();
      mockDoc({
        vision: "Ship the **spine**.",
        scope: "In: store. Out: UI.",
        decision_log: "Adopted the four-anchor doc.",
        discovered_facts: "Found a thing.",
      });
      renderAt();
      const vision = await screen.findByTestId("project-doc-vision");
      expect(vision).toHaveTextContent("Ship the spine.");
      expect(vision.querySelector("strong")).toHaveTextContent("spine");
      expect(screen.getByTestId("project-doc-scope")).toHaveTextContent("In: store. Out: UI.");
      expect(screen.getByTestId("project-doc-decision-log")).toHaveTextContent(
        "Adopted the four-anchor doc.",
      );
      expect(screen.getByTestId("project-doc-discovered-facts")).toHaveTextContent(
        "Found a thing.",
      );
    });
  });
});
