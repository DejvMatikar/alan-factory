import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { server } from "../../../test/server";
import {
  BUILD_CHAT_PATH,
  SID,
  defaultReadyProposal,
  installBuildChatPageSetup,
  renderBuildChat,
  sessionResp,
} from "../../../test/buildChatPageHarness";
import BuildChat from "./BuildChat";

describe("BuildChat", () => {
  installBuildChatPageSetup();

  describe("Tab selection across session back-fill / switch", () => {
    it("selecting Summary before the session id arrives leaves the user on Summary once it arrives", async () => {
      let resolveCreate!: () => void;
      let sessionGetCalls = 0;
      server.use(
        http.post(BUILD_CHAT_PATH, () =>
          new Promise<Response>((res) => {
            resolveCreate = () =>
              res(HttpResponse.json({ session_id: SID }, { status: 201 }));
          }),
        ),
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () => {
          sessionGetCalls += 1;
          return HttpResponse.json(sessionResp());
        }),
      );
      const user = userEvent.setup();
      renderBuildChat();
      await user.click(screen.getByTestId("tab-summary"));
      expect(screen.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true");
      resolveCreate();
      await waitFor(() => expect(sessionGetCalls).toBeGreaterThan(0));
      expect(screen.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true");
      expect(screen.getByTestId("tab-chat")).toHaveAttribute("aria-selected", "false");
    });

    it("clearing the chat while on Summary returns to the Chat tab", async () => {
      const NEW_SID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
      const confirmSpy = vi.spyOn(globalThis, "confirm").mockReturnValue(true);
      let resolveCreate!: () => void;
      server.use(
        http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () =>
          new HttpResponse(null, { status: 204 }),
        ),
        http.get(`${BUILD_CHAT_PATH}/${NEW_SID}`, () =>
          HttpResponse.json(sessionResp({ session_id: NEW_SID })),
        ),
      );
      const user = userEvent.setup();
      renderBuildChat();
      await waitFor(() => expect(screen.getByTestId("clear-button")).not.toBeDisabled());
      server.use(
        http.post(BUILD_CHAT_PATH, () =>
          new Promise<Response>((res) => {
            resolveCreate = () =>
              res(HttpResponse.json({ session_id: NEW_SID }, { status: 201 }));
          }),
        ),
      );
      await user.click(screen.getByTestId("clear-button"));
      expect(confirmSpy).toHaveBeenCalled();
      await user.click(screen.getByTestId("tab-summary"));
      expect(screen.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true");
      resolveCreate();
      await waitFor(() =>
        expect(screen.getByTestId("tab-chat")).toHaveAttribute("aria-selected", "true"),
      );
      confirmSpy.mockRestore();
    });

    it("does not force Chat when mounting with a session id already present in the URL", async () => {
      const user = userEvent.setup();
      renderBuildChat(`/build?s=${SID}`);
      await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
      await user.click(screen.getByTestId("tab-summary"));
      expect(screen.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true");
      await new Promise((r) => setTimeout(r, 20));
      expect(screen.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true");
    });
  });

  describe("Heading focus when switching sessions in place", () => {
    it("moves focus to the heading when switching from a fresh draft to a different existing session without a remount", async () => {
      const NEW_SID = "dddddddd-dddd-dddd-dddd-dddddddddddd";
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${NEW_SID}`, () =>
          HttpResponse.json(sessionResp({ session_id: NEW_SID })),
        ),
        http.post(`${BUILD_CHAT_PATH}/${NEW_SID}/propose`, () =>
          HttpResponse.json(defaultReadyProposal()),
        ),
      );
      const user = userEvent.setup();
      render(
        <MemoryRouter initialEntries={["/build"]}>
          <Routes>
            <Route
              path="/build"
              element={
                <>
                  <Link to={`/build?s=${NEW_SID}`} data-testid="switch-session">
                    switch
                  </Link>
                  <BuildChat />
                </>
              }
            />
          </Routes>
        </MemoryRouter>,
      );
      await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
      expect(screen.getByRole("heading", { level: 1, name: "Build" })).not.toHaveFocus();
      await user.click(screen.getByTestId("switch-session"));
      await waitFor(() =>
        expect(screen.getByRole("heading", { level: 1, name: "Build" })).toHaveFocus(),
      );
    });
  });
});
