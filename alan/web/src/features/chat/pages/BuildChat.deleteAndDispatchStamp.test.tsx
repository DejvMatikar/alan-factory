import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import {
  BUILD_CHAT_PATH,
  DEFAULT_COUNTER_CASE,
  SID,
  installBuildChatPageSetup,
  readyProposal,
  renderBuildChat,
  sessionResp,
  summarizeTo,
} from "../../../test/buildChatPageHarness";

describe("BuildChat", () => {
  installBuildChatPageSetup();

  describe("In-chat delete", () => {
    it("disables the delete button before the session is ready", async () => {
      let resolveCreate!: (v: Response) => void;
      const createPromise = new Promise<Response>((res) => {
        resolveCreate = res;
      });
      server.use(http.post(BUILD_CHAT_PATH, () => createPromise));
      renderBuildChat();
      expect(screen.getByTestId("delete-chat-button")).toBeDisabled();
      resolveCreate(HttpResponse.json({ session_id: SID }, { status: 201 }));
      await waitFor(() => expect(screen.getByTestId("delete-chat-button")).not.toBeDisabled());
    });

    it("declining the confirmation leaves the chat exactly as it was", async () => {
      const user = userEvent.setup();
      renderBuildChat();
      await waitFor(() => expect(screen.getByTestId("delete-chat-button")).not.toBeDisabled());
      await user.type(screen.getByLabelText("Message"), "keep me");
      await user.click(screen.getByTestId("delete-chat-button"));
      const dialog = screen.getByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.getByLabelText("Message")).toHaveValue("keep me");
      expect(screen.getByTestId("tab-chat")).toHaveAttribute("aria-selected", "true");
    });

    it("confirming stops any in-flight turn, deletes the session, and lands on home", async () => {
      const calls: string[] = [];
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/interrupt`, () => {
          calls.push("interrupt");
          return new HttpResponse(null, { status: 204 });
        }),
        http.delete(`${BUILD_CHAT_PATH}/${SID}`, () => {
          calls.push("delete");
          return new HttpResponse(null, { status: 204 });
        }),
      );
      const user = userEvent.setup();
      renderBuildChat();
      await waitFor(() => expect(screen.getByTestId("delete-chat-button")).not.toBeDisabled());
      await user.click(screen.getByTestId("delete-chat-button"));
      const dialog = screen.getByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: "Delete chat" }));
      expect(await screen.findByTestId("home")).toBeInTheDocument();
      expect(calls).toEqual(["interrupt", "delete"]);
    });

    it("stops the in-flight turn before deleting when a turn is streaming", async () => {
      const calls: string[] = [];
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
          HttpResponse.json(sessionResp({ in_flight_turn: { started_at: "t", user_text: "q" } })),
        ),
        http.post(`${BUILD_CHAT_PATH}/${SID}/interrupt`, () => {
          calls.push("interrupt");
          return new HttpResponse(null, { status: 204 });
        }),
        http.delete(`${BUILD_CHAT_PATH}/${SID}`, () => {
          calls.push("delete");
          return new HttpResponse(null, { status: 204 });
        }),
      );
      const user = userEvent.setup();
      renderBuildChat();
      await waitFor(() => expect(screen.getByTestId("stop-button")).toBeInTheDocument());
      await user.click(screen.getByTestId("delete-chat-button"));
      const dialog = screen.getByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: "Delete chat" }));
      expect(await screen.findByTestId("home")).toBeInTheDocument();
      expect(calls).toEqual(["interrupt", "delete"]);
    });

    it("shows an inline error and stays in the chat when delete fails", async () => {
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/interrupt`, () =>
          new HttpResponse(null, { status: 204 }),
        ),
        http.delete(`${BUILD_CHAT_PATH}/${SID}`, () =>
          HttpResponse.json({ detail: "cannot delete" }, { status: 500 }),
        ),
      );
      const user = userEvent.setup();
      renderBuildChat();
      await waitFor(() => expect(screen.getByTestId("delete-chat-button")).not.toBeDisabled());
      await user.click(screen.getByTestId("delete-chat-button"));
      const dialog = screen.getByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: "Delete chat" }));
      await waitFor(() => expect(screen.getByText(/cannot delete/)).toBeInTheDocument());
      expect(screen.queryByTestId("home")).toBeNull();
      expect(screen.getByTestId("delete-chat-button")).not.toBeDisabled();
    });
  });

  describe("Chat header engine binding (AC#3)", () => {
    it("shows the session's own engine badge in the chat header once bound to a non-default engine", async () => {
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
          HttpResponse.json(
            sessionResp({ project_id: "proj-a", engine_key: "acme/sprocket" }),
          ),
        ),
      );
      renderBuildChat();
      const header = await screen.findByTestId("build-chat-engine-header");
      expect(within(header).getByTestId("engine-badge-build-chat-header")).toHaveTextContent(
        "acme/sprocket",
      );
    });

    it("shows no engine badge in the chat header for a session with no project binding", async () => {
      renderBuildChat();
      await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
      expect(screen.queryByTestId("build-chat-engine-header")).not.toBeInTheDocument();
    });
  });

  describe("Roadmap item dispatch stamp", () => {
    function projectDetail(roadmap: object[] = []) {
      return {
        id: "proj-a",
        title: "Proj A",
        status: "active",
        doc_ref: null,
        autonomy: "propose",
        budget_usd: null,
        engine_key: null,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
        workspaces: [],
        ready_to_complete: true,
        roadmap,
      };
    }

    it("passes the bound project id and the URL's roadmap item id through to the fused dispatch call, and shows the resolved item title", async () => {
      let dispatched: Record<string, unknown> | null = null;
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
          HttpResponse.json(sessionResp({ project_id: "proj-a" })),
        ),
        http.get("/api/projects/proj-a", () =>
          HttpResponse.json(
            projectDetail([
              {
                id: "itm_1",
                title: "Ship it",
                text: "",
                order: 0,
                status: "not_started",
                merged_prs: [],
              },
            ]),
          ),
        ),
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(readyProposal(DEFAULT_COUNTER_CASE, { title: "add-x" })),
        ),
        http.post("/api/workspaces", async ({ request }) => {
          dispatched = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ task_id: "add-x", root_task_ids: ["add-x"] });
        }),
        http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () =>
          new HttpResponse(null, { status: 204 }),
        ),
      );
      renderBuildChat("/build?roadmapItem=itm_1");
      const user = await summarizeTo("Build the X feature");
      await waitFor(() =>
        expect(screen.getByTestId("dispatch-proposal-roadmap-item")).toHaveTextContent("Ship it"),
      );
      await user.click(screen.getByTestId("dispatch-proposal-confirm"));
      expect(await screen.findByTestId("home")).toBeInTheDocument();
      expect(dispatched).toMatchObject({ project_id: "proj-a", roadmap_item_id: "itm_1" });
    });

    it("falls back to the raw roadmap item id when the project lookup has no matching item", async () => {
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
          HttpResponse.json(sessionResp({ project_id: "proj-a" })),
        ),
        http.get("/api/projects/proj-a", () => HttpResponse.json(projectDetail())),
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(readyProposal(DEFAULT_COUNTER_CASE, { title: "add-x" })),
        ),
      );
      renderBuildChat("/build?roadmapItem=itm_missing");
      await summarizeTo("Build the X feature");
      await waitFor(() =>
        expect(screen.getByTestId("dispatch-proposal-roadmap-item")).toHaveTextContent(
          "itm_missing",
        ),
      );
    });

    it("falls back to the raw roadmap item id when the project lookup itself fails", async () => {
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
          HttpResponse.json(sessionResp({ project_id: "proj-a" })),
        ),
        http.get("/api/projects/proj-a", () =>
          HttpResponse.json({ detail: "boom" }, { status: 500 }),
        ),
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(readyProposal(DEFAULT_COUNTER_CASE, { title: "add-x" })),
        ),
      );
      renderBuildChat("/build?roadmapItem=itm_2");
      await summarizeTo("Build the X feature");
      await waitFor(() =>
        expect(screen.getByTestId("dispatch-proposal-roadmap-item")).toHaveTextContent("itm_2"),
      );
    });

    it("shows no roadmap-item provenance line when the URL carries no roadmapItem and no project is bound", async () => {
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(readyProposal(DEFAULT_COUNTER_CASE, { title: "add-x" })),
        ),
      );
      renderBuildChat();
      await summarizeTo("Build the X feature");
      await screen.findByTestId("dispatch-proposal-confirm");
      expect(screen.queryByTestId("dispatch-proposal-roadmap-item")).not.toBeInTheDocument();
    });

    it("treats a blank roadmapItem query param as absent", async () => {
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(readyProposal(DEFAULT_COUNTER_CASE, { title: "add-x" })),
        ),
      );
      renderBuildChat("/build?roadmapItem=%20%20");
      await summarizeTo("Build the X feature");
      await screen.findByTestId("dispatch-proposal-confirm");
      expect(screen.queryByTestId("dispatch-proposal-roadmap-item")).not.toBeInTheDocument();
    });

    it("does not fetch the project or show a provenance line when the session is project-bound but the URL carries no roadmapItem", async () => {
      let getProjectCalls = 0;
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
          HttpResponse.json(sessionResp({ project_id: "proj-a" })),
        ),
        http.get("/api/projects/proj-a", () => {
          getProjectCalls += 1;
          return HttpResponse.json(projectDetail());
        }),
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(readyProposal(DEFAULT_COUNTER_CASE, { title: "add-x" })),
        ),
      );
      renderBuildChat();
      await summarizeTo("Build the X feature");
      await screen.findByTestId("dispatch-proposal-confirm");
      expect(screen.queryByTestId("dispatch-proposal-roadmap-item")).not.toBeInTheDocument();
      expect(getProjectCalls).toBe(0);
    });

    it("ignores a project-lookup resolution that lands after unmount", async () => {
      let resolve: ((value: Response) => void) | null = null;
      let getProjectCalls = 0;
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
          HttpResponse.json(sessionResp({ project_id: "proj-a" })),
        ),
        http.get(
          "/api/projects/proj-a",
          () =>
            new Promise<Response>((res) => {
              getProjectCalls += 1;
              resolve = res;
            }),
        ),
      );
      const { unmount } = renderBuildChat("/build?roadmapItem=itm_1");
      await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
      await waitFor(() => expect(getProjectCalls).toBe(1));
      unmount();
      resolve!(
        HttpResponse.json(
          projectDetail([
            {
              id: "itm_1",
              title: "Ship it",
              text: "",
              order: 0,
              status: "not_started",
              merged_prs: [],
            },
          ]),
        ),
      );
      await new Promise((r) => setTimeout(r, 10));
    });
  });
});
