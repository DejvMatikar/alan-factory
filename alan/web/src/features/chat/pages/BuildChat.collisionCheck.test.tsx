import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import {
  BUILD_CHAT_PATH,
  SID,
  installBuildChatPageSetup,
  renderBuildChat,
  sessionResp,
  summarizeTo,
} from "../../../test/buildChatPageHarness";

describe("BuildChat", () => {
  installBuildChatPageSetup();

  describe("Collision check trigger", () => {
    it("fires once, session-scoped, right after summarize completes", async () => {
      const calls: unknown[] = [];
      server.use(
        http.post("/api/collision-check", async ({ request }) => {
          calls.push(await request.json());
          return HttpResponse.json({ state: "green", sentence: "", match: null });
        }),
      );
      renderBuildChat();
      await summarizeTo("Build the X feature");
      await waitFor(() => expect(calls).toHaveLength(1));
      expect(calls[0]).toEqual({
        brief: "Build the X feature",
        chat_family: "build",
        session_id: SID,
      });
    });

    it("does not re-fire the collision check when the brief is edited", async () => {
      const calls: unknown[] = [];
      server.use(
        http.post("/api/collision-check", async ({ request }) => {
          calls.push(await request.json());
          return HttpResponse.json({ state: "green", sentence: "", match: null });
        }),
        http.put(`${BUILD_CHAT_PATH}/${SID}/brief`, () =>
          HttpResponse.json({ content: "edited", updated_at: "2026-05-01T02:00:00Z" }),
        ),
      );
      renderBuildChat();
      const user = await summarizeTo("original brief");
      await waitFor(() => expect(calls).toHaveLength(1));
      await user.click(screen.getByTestId("summary-source-toggle"));
      const textarea = screen.getByTestId("summary-textarea");
      await user.clear(textarea);
      await user.type(textarea, "edited");
      await user.tab();
      await waitFor(() => expect(screen.getByTestId("summary-textarea")).toHaveValue("edited"));
      expect(calls).toHaveLength(1);
    });

    it("re-runs the collision check on explicit Re-run click", async () => {
      const calls: unknown[] = [];
      server.use(
        http.post("/api/collision-check", async ({ request }) => {
          calls.push(await request.json());
          return HttpResponse.json({ state: "green", sentence: "", match: null });
        }),
      );
      const user = userEvent.setup();
      renderBuildChat();
      await summarizeTo("Build the X feature");
      await waitFor(() => expect(calls).toHaveLength(1));
      await user.click(screen.getByTestId("collision-rerun"));
      await waitFor(() => expect(calls).toHaveLength(2));
    });

    it("shows the collision run's spend as a distinct pricing row above the tabs", async () => {
      server.use(
        http.post("/api/collision-check", () =>
          HttpResponse.json({ state: "green", sentence: "", match: null }),
        ),
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
          HttpResponse.json(
            sessionResp({
              run_summary: {
                model: "opus",
                effort: "xhigh",
                system_prompt: "build chat system prompt",
                total_cost_usd: 0.05,
                total_output_tokens: 40,
                total_input_tokens: 60,
                tool_counts: [],
              },
              collision_run_summary: {
                model: "haiku",
                effort: null,
                system_prompt: "collision prompt",
                total_cost_usd: 0.001,
                total_output_tokens: 5,
                total_input_tokens: 8,
                tool_counts: [],
              },
            }),
          ),
        ),
      );
      renderBuildChat();
      const user = await summarizeTo("Build the X feature");
      expect(await screen.findByTestId("run-summary-header-conversation")).toBeInTheDocument();
      expect(screen.getByTestId("run-summary-header-collision")).toBeInTheDocument();
      await user.click(screen.getByTestId("tab-chat"));
      expect(screen.getByTestId("run-summary-header-conversation")).toBeInTheDocument();
      expect(screen.getByTestId("run-summary-header-collision")).toBeInTheDocument();
    });

    it("does not error the Summary tab when the collision check fails", async () => {
      server.use(
        http.post("/api/collision-check", () => new HttpResponse(null, { status: 500 })),
      );
      renderBuildChat();
      await summarizeTo("Build the X feature");
      expect(screen.getByTestId("summary-preview")).toHaveTextContent("Build the X feature");
      await waitFor(() => expect(screen.getByTestId("collision-box")).toBeInTheDocument());
    });
  });
});
