import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { STUB_DEFAULT_ENGINE_KEY } from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import { BASE, SID, renderExploreChat } from "../../../test/exploreChatPageHarness";

describe("Repo chooser (repo-visibility-and-selector)", () => {
  function stubEngines(repos: { owner: string; repo: string }[]) {
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          default_engine_key: STUB_DEFAULT_ENGINE_KEY,
          engines: repos.map((r) => ({ repo: r, posture: "autonomous" })),
        }),
      ),
    );
  }

  it("shows no chooser and creates a session immediately with only the default repo registered", async () => {
    stubEngines([]);
    renderExploreChat();
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
  });

  it("shows a chooser with more than one repository connected, gates session creation, and binds the pick", async () => {
    let createCalls = 0;
    let capturedBody: { consultant_key?: string; engine_key?: string } | null = null;
    server.use(
      http.post(BASE, async ({ request }) => {
        createCalls += 1;
        capturedBody = (await request.json()) as { consultant_key?: string; engine_key?: string };
        return HttpResponse.json({ session_id: SID }, { status: 201 });
      }),
    );
    stubEngines([{ owner: "acme-org", repo: "widgets" }]);
    renderExploreChat();
    const chooser = await screen.findByTestId("repo-chooser-modal");
    expect(screen.getByTestId("repo-option-DejvMatikar/alan")).toBeInTheDocument();
    expect(screen.getByTestId("repo-option-acme-org/widgets")).toBeInTheDocument();
    expect(createCalls).toBe(0);

    const user = userEvent.setup();
    await user.click(screen.getByTestId("repo-option-acme-org/widgets"));
    await waitFor(() => expect(chooser).not.toBeInTheDocument());
    await waitFor(() => expect(createCalls).toBe(1));
    expect(capturedBody).toEqual({
      consultant_key: "brainstorming",
      engine_key: "acme-org/widgets",
    });
  });

  it("closing the chooser navigates home without creating a session", async () => {
    let createCalls = 0;
    server.use(
      http.post(BASE, () => {
        createCalls += 1;
        return HttpResponse.json({ session_id: SID }, { status: 201 });
      }),
    );
    stubEngines([{ owner: "acme-org", repo: "widgets" }]);
    renderExploreChat();
    await screen.findByTestId("repo-chooser-modal");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("modal-close"));
    expect(await screen.findByTestId("home")).toBeInTheDocument();
    expect(createCalls).toBe(0);
  });

  it("does not show the chooser when resuming a session via ?s=", async () => {
    let createCalls = 0;
    server.use(
      http.post(BASE, () => {
        createCalls += 1;
        return HttpResponse.json({ session_id: SID }, { status: 201 });
      }),
    );
    stubEngines([{ owner: "acme-org", repo: "widgets" }]);
    renderExploreChat("brainstorming", `?s=${SID}`);
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
    expect(createCalls).toBe(0);
  });

  it("starts no session and shows no chooser when the engines fetch fails, since no default repository is known", async () => {
    let createCalls = 0;
    let engineRequests = 0;
    server.use(
      http.post(BASE, () => {
        createCalls += 1;
        return HttpResponse.json({ session_id: SID }, { status: 201 });
      }),
      http.get("/api/engines", () => {
        engineRequests += 1;
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      }),
    );
    renderExploreChat();
    await waitFor(() => expect(engineRequests).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 20));
    expect(createCalls).toBe(0);
    expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
  });

  it("starts no session when no repository is registered and the list names no default", async () => {
    let createCalls = 0;
    let engineRequests = 0;
    server.use(
      http.post(BASE, () => {
        createCalls += 1;
        return HttpResponse.json({ session_id: SID }, { status: 201 });
      }),
      http.get("/api/engines", () => {
        engineRequests += 1;
        return HttpResponse.json({ engines: [] });
      }),
    );
    renderExploreChat();
    await waitFor(() => expect(engineRequests).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 20));
    expect(createCalls).toBe(0);
    expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
  });
});
