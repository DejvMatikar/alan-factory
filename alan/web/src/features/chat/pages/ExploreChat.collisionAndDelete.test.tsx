import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

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

  describe("Collision check trigger", () => {
    it("fires once, session-scoped, right after summarize completes", async () => {
      const calls: unknown[] = [];
      server.use(
        http.post("/api/collision-check", async ({ request }) => {
          calls.push(await request.json());
          return HttpResponse.json({ state: "green", sentence: "", match: null });
        }),
      );
      renderExploreChat();
      await summarizeTo("Scope: the whole system");
      await waitFor(() => expect(calls).toHaveLength(1));
      expect(calls[0]).toEqual({
        brief: "Scope: the whole system",
        chat_family: "explore",
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
        http.put(`${BASE}/${SID}/brief`, () =>
          HttpResponse.json({ content: "edited", updated_at: "2026-06-01T02:00:00Z" }),
        ),
      );
      renderExploreChat();
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
      renderExploreChat();
      await summarizeTo("Scope: the whole system");
      await waitFor(() => expect(calls).toHaveLength(1));
      await user.click(screen.getByTestId("collision-rerun"));
      await waitFor(() => expect(calls).toHaveLength(2));
    });

    it("does not error the Summary tab when the collision check fails", async () => {
      server.use(
        http.post("/api/collision-check", () => new HttpResponse(null, { status: 500 })),
      );
      renderExploreChat();
      await summarizeTo("Scope: the whole system");
      expect(screen.getByTestId("summary-preview")).toHaveTextContent("Scope: the whole system");
      await waitFor(() => expect(screen.getByTestId("collision-box")).toBeInTheDocument());
    });
  });

  describe("In-chat delete", () => {
    it("disables the delete button before the session is ready", async () => {
      let resolveCreate!: (v: Response) => void;
      const createPromise = new Promise<Response>((res) => {
        resolveCreate = res;
      });
      server.use(http.post(BASE, () => createPromise));
      renderExploreChat();
      expect(await screen.findByTestId("delete-chat-button")).toBeDisabled();
      resolveCreate(HttpResponse.json({ session_id: SID }, { status: 201 }));
      await waitFor(() => expect(screen.getByTestId("delete-chat-button")).not.toBeDisabled());
    });

    it("declining the confirmation leaves the chat exactly as it was", async () => {
      const user = userEvent.setup();
      renderExploreChat();
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
        http.post(`${BASE}/${SID}/interrupt`, () => {
          calls.push("interrupt");
          return new HttpResponse(null, { status: 204 });
        }),
        http.delete(`${BASE}/${SID}`, () => {
          calls.push("delete");
          return new HttpResponse(null, { status: 204 });
        }),
      );
      const user = userEvent.setup();
      renderExploreChat();
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
        http.get(`${BASE}/${SID}`, () =>
          HttpResponse.json(sessionResp({ in_flight_turn: { started_at: "t", user_text: "q" } })),
        ),
        http.post(`${BASE}/${SID}/interrupt`, () => {
          calls.push("interrupt");
          return new HttpResponse(null, { status: 204 });
        }),
        http.delete(`${BASE}/${SID}`, () => {
          calls.push("delete");
          return new HttpResponse(null, { status: 204 });
        }),
      );
      const user = userEvent.setup();
      renderExploreChat();
      await waitFor(() => expect(screen.getByTestId("stop-button")).toBeInTheDocument());
      await user.click(screen.getByTestId("delete-chat-button"));
      const dialog = screen.getByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: "Delete chat" }));
      expect(await screen.findByTestId("home")).toBeInTheDocument();
      expect(calls).toEqual(["interrupt", "delete"]);
    });

    it("shows an inline error and stays in the chat when delete fails", async () => {
      server.use(
        http.post(`${BASE}/${SID}/interrupt`, () => new HttpResponse(null, { status: 204 })),
        http.delete(`${BASE}/${SID}`, () =>
          HttpResponse.json({ detail: "cannot delete" }, { status: 500 }),
        ),
      );
      const user = userEvent.setup();
      renderExploreChat();
      await waitFor(() => expect(screen.getByTestId("delete-chat-button")).not.toBeDisabled());
      await user.click(screen.getByTestId("delete-chat-button"));
      const dialog = screen.getByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: "Delete chat" }));
      await waitFor(() => expect(screen.getByText(/cannot delete/)).toBeInTheDocument());
      expect(screen.queryByTestId("home")).toBeNull();
      expect(screen.getByTestId("delete-chat-button")).not.toBeDisabled();
    });
  });
});
