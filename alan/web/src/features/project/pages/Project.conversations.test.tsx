import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import {
  BASE_PROJECT,
  mockDoc,
  mockProject,
  renderAt,
  renderWithNav,
} from "../../../test/projectPageHarness";
import { server } from "../../../test/server";

describe("Project", () => {
  describe("conversations section", () => {
    const UUID_B = "44444444-4444-4444-4444-444444444444";
    const UUID_E = "55555555-5555-5555-5555-555555555555";
    const BUILD_SESSION = {
      session_id: UUID_B,
      created_at: "2026-05-20T09:00:00Z",
      last_message_preview: "build message",
      in_flight: false,
      summary_ready: true,
    };
    const EXPLORE_SESSION = {
      session_id: UUID_E,
      created_at: "2026-05-20T08:00:00Z",
      last_message_preview: "",
      in_flight: true,
      summary_ready: false,
      consultant_key: "brainstorming",
    };

    it("shows a loading state before the conversation fetch resolves", async () => {
      mockProject();
      mockDoc();
      server.use(
        http.get("/api/build-chat/sessions", async () => {
          await new Promise(() => undefined);
          return HttpResponse.json([]);
        }),
      );
      renderAt();
      await screen.findByRole("heading", { name: "Conversations" });
      expect(screen.getByTestId("project-conversations-loading")).toBeInTheDocument();
    });

    it("fetches both families scoped by project_id and renders rows newest-first", async () => {
      let buildProjectId: string | null = null;
      let exploreProjectId: string | null = null;
      server.use(
        http.get("/api/build-chat/sessions", ({ request }) => {
          buildProjectId = new URL(request.url).searchParams.get("project_id");
          return HttpResponse.json([BUILD_SESSION]);
        }),
        http.get("/api/explore-chat/sessions", ({ request }) => {
          exploreProjectId = new URL(request.url).searchParams.get("project_id");
          return HttpResponse.json([EXPLORE_SESSION]);
        }),
      );
      mockProject();
      mockDoc();
      renderAt();
      const list = await screen.findByTestId("project-conversations-list");
      const rows = within(list).getAllByTestId(/^draft-row-/);
      expect(rows).toHaveLength(2);
      expect(rows[0]).toHaveAttribute("data-testid", `draft-row-${UUID_B}`);
      expect(rows[1]).toHaveAttribute("data-testid", `draft-row-${UUID_E}`);
      expect(buildProjectId).toBe("alpha");
      expect(exploreProjectId).toBe("alpha");
    });

    it("still renders the surviving family's rows when one family fails", async () => {
      server.use(
        http.get("/api/build-chat/sessions", () => HttpResponse.json([BUILD_SESSION])),
        http.get("/api/explore-chat/sessions", () =>
          HttpResponse.json({ detail: "error" }, { status: 500 }),
        ),
      );
      mockProject();
      mockDoc();
      renderAt();
      const list = await screen.findByTestId("project-conversations-list");
      expect(within(list).getByTestId(`draft-row-${UUID_B}`)).toBeInTheDocument();
    });

    it("shows an inline error alert only when both families fail", async () => {
      server.use(
        http.get("/api/build-chat/sessions", () =>
          HttpResponse.json({ detail: "boom" }, { status: 500 }),
        ),
        http.get("/api/explore-chat/sessions", () =>
          HttpResponse.json({ detail: "boom" }, { status: 500 }),
        ),
      );
      mockProject();
      mockDoc();
      renderAt();
      const alert = await screen.findByTestId("project-conversations-error");
      expect(alert).toHaveTextContent("boom");
    });

    it("re-fetches conversations scoped to the new project when navigating between projects", async () => {
      const seenProjectIds: string[] = [];
      server.use(
        http.get("/api/build-chat/sessions", ({ request }) => {
          seenProjectIds.push(new URL(request.url).searchParams.get("project_id") ?? "");
          return HttpResponse.json([]);
        }),
        http.get("/api/projects/beta", () =>
          HttpResponse.json({ ...BASE_PROJECT, id: "beta", title: "Beta" }),
        ),
      );
      mockProject();
      mockDoc();
      renderWithNav("/projects/alpha", "/projects/beta");
      await screen.findByTestId("project-conversations-empty");

      fireEvent.click(screen.getByTestId("nav-to-other-project"));

      await waitFor(() => expect(seenProjectIds).toContain("beta"));
      expect(seenProjectIds).toContain("alpha");
    });

    it("removes a row from the list once it is deleted", async () => {
      server.use(
        http.get("/api/build-chat/sessions", () => HttpResponse.json([BUILD_SESSION])),
        http.delete(`/api/build-chat/sessions/${UUID_B}`, () => new HttpResponse(null, { status: 204 })),
      );
      mockProject();
      mockDoc();
      const user = userEvent.setup();
      renderAt();
      await screen.findByTestId(`draft-row-${UUID_B}`);
      await user.click(screen.getByTestId(`delete-draft-${UUID_B}`));
      await user.click(screen.getByRole("button", { name: "Delete chat" }));
      await waitFor(() =>
        expect(screen.queryByTestId(`draft-row-${UUID_B}`)).not.toBeInTheDocument(),
      );
      expect(screen.getByTestId("project-conversations-empty")).toBeInTheDocument();
    });

    it("conversation rows are keyboard-reachable links", async () => {
      server.use(
        http.get("/api/build-chat/sessions", () => HttpResponse.json([BUILD_SESSION])),
      );
      mockProject();
      mockDoc();
      const user = userEvent.setup();
      renderAt();
      const link = await screen.findByTestId(`resume-draft-${UUID_B}`);
      expect(link.tagName).toBe("A");
      await user.tab({ shift: false });
      let reached = document.activeElement === link;
      for (let i = 0; i < 20 && !reached; i += 1) {
        await user.tab();
        reached = document.activeElement === link;
      }
      expect(reached).toBe(true);
    });
  });
});
