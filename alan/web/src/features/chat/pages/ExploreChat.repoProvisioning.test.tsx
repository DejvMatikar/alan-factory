import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "../../../test/server";
import {
  BASE,
  SID,
  installExploreChatPageSetup,
  renderExploreChat,
  sessionResp,
  summarizeTo,
} from "../../../test/exploreChatPageHarness";

describe("ExploreChat", () => {
  installExploreChatPageSetup();

  describe("Repo provisioning states", () => {
    it("shows an in-progress banner when repo_provision is pending", async () => {
      server.use(
        http.get(`${BASE}/${SID}`, () =>
          HttpResponse.json(sessionResp({ repo_provision: "pending" })),
        ),
      );
      renderExploreChat();
      const banner = await screen.findByTestId("repo-provision-pending");
      expect(banner).toHaveAttribute("role", "status");
    });

    it("shows no banner and lets the conversation proceed when repo_provision is ready", async () => {
      server.use(
        http.get(`${BASE}/${SID}`, () =>
          HttpResponse.json(sessionResp({ repo_provision: "ready" })),
        ),
      );
      renderExploreChat();
      await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
      expect(screen.queryByTestId("repo-provision-pending")).not.toBeInTheDocument();
      expect(screen.queryByTestId("repo-provision-error")).not.toBeInTheDocument();
    });

    it("shows no banner when repo_provision is not_needed", async () => {
      renderExploreChat();
      await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
      expect(screen.queryByTestId("repo-provision-pending")).not.toBeInTheDocument();
      expect(screen.queryByTestId("repo-provision-error")).not.toBeInTheDocument();
    });

    it("shows a retry-capable error banner and resends the last user message on retry", async () => {
      server.use(
        http.get(`${BASE}/${SID}`, () =>
          HttpResponse.json(
            sessionResp({
              repo_provision: "error",
              messages: [
                { role: "user", content: "explore this repo", ts: "2026-06-01T00:00:00Z" },
              ],
            }),
          ),
        ),
      );
      const sent: string[] = [];
      server.use(
        http.post(`${BASE}/${SID}/messages`, async ({ request }) => {
          sent.push(((await request.json()) as { content: string }).content);
          return HttpResponse.json({ status: "accepted" }, { status: 202 });
        }),
      );
      const user = userEvent.setup();
      renderExploreChat();
      const banner = await screen.findByTestId("repo-provision-error");
      expect(banner).toHaveAttribute("role", "alert");
      await user.click(screen.getByTestId("repo-provision-retry"));
      await waitFor(() => expect(sent).toContain("explore this repo"));
    });

    it("retry is a no-op when there is no prior user message", async () => {
      server.use(
        http.get(`${BASE}/${SID}`, () =>
          HttpResponse.json(sessionResp({ repo_provision: "error" })),
        ),
      );
      const sent: string[] = [];
      server.use(
        http.post(`${BASE}/${SID}/messages`, async ({ request }) => {
          sent.push(((await request.json()) as { content: string }).content);
          return HttpResponse.json({ status: "accepted" }, { status: 202 });
        }),
      );
      const user = userEvent.setup();
      renderExploreChat();
      await screen.findByTestId("repo-provision-error");
      await user.click(screen.getByTestId("repo-provision-retry"));
      await new Promise((r) => setTimeout(r, 50));
      expect(sent).toHaveLength(0);
    });

    it("forwards the session's project_id into create-and-dispatch", async () => {
      server.use(
        http.get(`${BASE}/${SID}`, () =>
          HttpResponse.json(sessionResp({ project_id: "proj-1" })),
        ),
      );
      let dispatched: Record<string, unknown> | null = null;
      server.use(
        http.post("/api/workspaces", async ({ request }) => {
          dispatched = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ task_id: "explore-task-3", root_task_ids: ["explore-task-3"] });
        }),
        http.delete(/\/api\/explore-chat\/sessions\/[^/]+$/, () =>
          new HttpResponse(null, { status: 204 }),
        ),
      );
      renderExploreChat();
      const user = await summarizeTo("Scope: the system");
      await screen.findByTestId("dispatch-proposal-confirm");
      await user.click(screen.getByTestId("dispatch-proposal-confirm"));
      expect(await screen.findByTestId("home")).toBeInTheDocument();
      expect(dispatched).toMatchObject({ project_id: "proj-1" });
    });

    it("omits project_id from create-and-dispatch when the session has none", async () => {
      let dispatched: Record<string, unknown> | null = null;
      server.use(
        http.post("/api/workspaces", async ({ request }) => {
          dispatched = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ task_id: "explore-task-4", root_task_ids: ["explore-task-4"] });
        }),
        http.delete(/\/api\/explore-chat\/sessions\/[^/]+$/, () =>
          new HttpResponse(null, { status: 204 }),
        ),
      );
      renderExploreChat();
      const user = await summarizeTo("Scope: the system");
      await screen.findByTestId("dispatch-proposal-confirm");
      await user.click(screen.getByTestId("dispatch-proposal-confirm"));
      expect(await screen.findByTestId("home")).toBeInTheDocument();
      expect(dispatched).not.toHaveProperty("project_id");
    });

    it("dispatches with the session's own engine_key for a project-less, non-default-repo session", async () => {
      const REPO_ENGINE_KEY = "acme-org/widgets";
      server.use(
        http.get(`${BASE}/${SID}`, () =>
          HttpResponse.json(sessionResp({ engine_key: REPO_ENGINE_KEY })),
        ),
      );
      let dispatched: Record<string, unknown> | null = null;
      server.use(
        http.post("/api/workspaces", async ({ request }) => {
          dispatched = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ task_id: "explore-task-5", root_task_ids: ["explore-task-5"] });
        }),
        http.delete(/\/api\/explore-chat\/sessions\/[^/]+$/, () =>
          new HttpResponse(null, { status: 204 }),
        ),
      );
      renderExploreChat();
      const user = await summarizeTo("Scope: the system");
      await screen.findByTestId("dispatch-proposal-confirm");
      await user.click(screen.getByTestId("dispatch-proposal-confirm"));
      expect(await screen.findByTestId("home")).toBeInTheDocument();
      expect(dispatched).toMatchObject({ engine_key: REPO_ENGINE_KEY });
      expect(dispatched).not.toHaveProperty("project_id");
    });
  });

  describe("Clear during an unsettled engine binding fetch", () => {
    it("falls back to alan's own repo when clear is triggered while the binding fetch is still pending", async () => {
      const NEW_SID = "cccccccc-cccc-cccc-cccc-cccccccccccc";
      const confirmSpy = vi.spyOn(globalThis, "confirm").mockReturnValue(true);
      server.use(
        http.get(`${BASE}/${SID}`, () => new Promise<Response>(() => undefined)),
        http.delete(/\/api\/explore-chat\/sessions\/[^/]+$/, () =>
          new HttpResponse(null, { status: 204 }),
        ),
        http.get(`${BASE}/${NEW_SID}`, () => HttpResponse.json(sessionResp({ session_id: NEW_SID }))),
      );
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post(BASE, async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ session_id: NEW_SID }, { status: 201 });
        }),
      );
      const user = userEvent.setup();
      renderExploreChat();
      await waitFor(() => expect(screen.getByTestId("clear-button")).not.toBeDisabled());
      await user.click(screen.getByTestId("clear-button"));
      expect(confirmSpy).toHaveBeenCalled();
      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody).toMatchObject({ engine_key: "DejvMatikar/alan" });
      confirmSpy.mockRestore();
    });
  });

  describe("Archived session redirect", () => {
    it("replace-navigates to the workspace prep-chat when the session GET redirects (303)", async () => {
      server.use(
        http.get(`${BASE}/${SID}`, ({ request }) =>
          HttpResponse.redirect(
            new URL("/api/workspaces/archived-ws/prep-chat", request.url).toString(),
            303,
          ),
        ),
        http.get("/api/workspaces/archived-ws/prep-chat", () =>
          HttpResponse.json({
            session_id: SID,
            kind: "explore",
            consultant_key: "brainstorming",
            title: "Archived",
            created_at: "2026-06-01T00:00:00Z",
            messages: [],
          }),
        ),
      );
      renderExploreChat("brainstorming", `?s=${SID}`);
      expect(await screen.findByTestId("prep-chat-page")).toBeInTheDocument();
    });
  });
});
