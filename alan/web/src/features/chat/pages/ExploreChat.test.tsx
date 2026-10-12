import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  LISTED_DEFAULT_KEY,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
} from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import {
  BASE,
  COUNTER_CASE,
  SID,
  installExploreChatPageSetup,
  readyProposal,
  renderExploreChat,
  sessionResp,
  summarizeTo,
} from "../../../test/exploreChatPageHarness";

describe("ExploreChat", () => {
  installExploreChatPageSetup();

  it("shows unknown-consultant error for bad consultant key", async () => {
    renderExploreChat("bogus_consultant_xyz");
    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown consultant");
  });

  it("shows bootstrap error when session creation fails", async () => {
    server.use(
      http.post(BASE, () => HttpResponse.json({ detail: "server error" }, { status: 500 })),
    );
    renderExploreChat();
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent("Could not start explore session");
  });

  it("renders heading with consultant label", async () => {
    renderExploreChat();
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { level: 1, name: /Explore \/ Brainstorming/ }),
      ).toBeInTheDocument(),
    );
  });

  it("shows the persisted run-summary header when the session GET returns run_summary", async () => {
    server.use(
      http.get(`${BASE}/${SID}`, () =>
        HttpResponse.json(
          sessionResp({
            run_summary: {
              model: "opus",
              effort: "xhigh",
              system_prompt: "explore chat system prompt",
              total_cost_usd: 0.02,
              total_output_tokens: 12,
              total_input_tokens: 22,
              tool_counts: [{ name: "Grep", count: 1 }],
            },
          }),
        ),
      ),
    );
    renderExploreChat();
    expect(await screen.findByTestId("run-summary-header-conversation")).toBeInTheDocument();
    expect(screen.getByTestId("run-summary-cost-conversation")).toHaveTextContent("$0.0200");
  });

  it("renders Chat and Summary tabs, with no Open Questions tab until one is needed", async () => {
    renderExploreChat();
    await waitFor(() => expect(screen.getByTestId("tab-chat")).toBeInTheDocument());
    expect(screen.getByTestId("tab-summary")).toBeInTheDocument();
    expect(screen.queryByTestId("tab-questions")).not.toBeInTheDocument();
  });

  it("creates a new session on fresh visit", async () => {
    renderExploreChat();
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
  });

  it("sends a message and clears the draft", async () => {
    const sent: string[] = [];
    server.use(
      http.post(`${BASE}/${SID}/messages`, async ({ request }) => {
        sent.push(((await request.json()) as { content: string }).content);
        return HttpResponse.json({ status: "accepted" }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderExploreChat();
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    await user.type(screen.getByLabelText("Message"), "explore this");
    await user.click(screen.getByRole("button", { name: /Send/ }));
    await waitFor(() => expect(sent).toContain("explore this"));
  });

  it("summarize switches to Summary tab and shows brief, formatted by default", async () => {
    renderExploreChat();
    await summarizeTo("Scope: the whole system");
    expect(screen.getByTestId("summary-preview")).toHaveTextContent("Scope: the whole system");
    expect(screen.queryByTestId("summary-textarea")).not.toBeInTheDocument();
  });

  it("shows alert when summarize fails", async () => {
    server.use(
      http.post(`${BASE}/${SID}/summarize`, () =>
        HttpResponse.json({ detail: "claude failed" }, { status: 502 }),
      ),
    );
    const user = userEvent.setup();
    renderExploreChat();
    await waitFor(() => expect(screen.getByTestId("summarize-button")).not.toBeDisabled());
    await user.click(screen.getByTestId("summarize-button"));
    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThan(0));
  });

  it("shows the idle Dispatch consultant button on Summary before any brief exists", async () => {
    const user = userEvent.setup();
    renderExploreChat();
    await waitFor(() => expect(screen.getByTestId("tab-summary")).toBeInTheDocument());
    await user.click(screen.getByTestId("tab-summary"));
    expect(screen.getByTestId("dispatch-explore")).not.toBeDisabled();
    expect(screen.getByTestId("dispatch-explore")).toHaveTextContent("Dispatch consultant");
  });

  it("propose auto-triggers after summarize, rendering title, rationale, acceptance clauses, counter-case, and collision verdict inline before any workspace exists", async () => {
    let workspaceCalls = 0;
    server.use(
      http.post(`${BASE}/${SID}/propose`, () =>
        HttpResponse.json(
          readyProposal(COUNTER_CASE, { acceptance_clauses: ["ship a scoped writeup"] }),
        ),
      ),
      http.post("/api/workspaces", () => {
        workspaceCalls += 1;
        return HttpResponse.json({ task_id: "explore-task", root_task_ids: ["explore-task"] });
      }),
    );
    renderExploreChat();
    await summarizeTo("Scope: the whole system");

    await screen.findByTestId("dispatch-proposal-confirm");
    expect(screen.getByTestId("dispatch-proposal-title")).toHaveTextContent("explore-thing");
    expect(screen.getByTestId("dispatch-proposal-rationale")).toHaveTextContent(
      "clear enough to hand off",
    );
    expect(screen.getByTestId("dispatch-proposal-acceptance-clauses")).toHaveTextContent(
      "ship a scoped writeup",
    );
    expect(screen.getByTestId("dispatch-counter-case-against")).toHaveTextContent(
      COUNTER_CASE.against,
    );
    expect(screen.getByTestId("collision-sentence")).toHaveTextContent(
      "No overlapping work found.",
    );
    expect(workspaceCalls).toBe(0);
  });

  it("confirming a ready proposal performs the atomic create-and-dispatch call with the counter_case waiver and navigates home", async () => {
    let dispatched: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        dispatched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ task_id: "explore-task", root_task_ids: ["explore-task"] });
      }),
      http.delete(/\/api\/explore-chat\/sessions\/[^/]+$/, () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    renderExploreChat();
    const user = await summarizeTo("Scope: the whole system");
    await screen.findByTestId("dispatch-proposal-confirm");
    expect(screen.queryByTestId("home")).toBeNull();
    await user.click(screen.getByTestId("dispatch-proposal-confirm"));
    expect(await screen.findByTestId("home")).toBeInTheDocument();
    expect(dispatched).toMatchObject({
      team: "brainstorming",
      title: "explore-thing",
      counter_case: COUNTER_CASE,
    });
  });

  it("disables the dispatch-explore button while the atomic create-and-dispatch call is in flight, then navigates home once it resolves", async () => {
    let resolveCreate!: () => void;
    server.use(
      http.post("/api/workspaces", async () => {
        await new Promise<void>((res) => { resolveCreate = res; });
        return HttpResponse.json({ task_id: "explore-task", root_task_ids: ["explore-task"] });
      }),
      http.delete(/\/api\/explore-chat\/sessions\/[^/]+$/, () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    renderExploreChat();
    const user = await summarizeTo("Scope: the whole system");
    await screen.findByTestId("dispatch-proposal-confirm");
    await user.click(screen.getByTestId("dispatch-proposal-confirm"));
    await waitFor(() => expect(screen.queryByTestId("dispatch-proposal-confirm")).toBeNull());
    await waitFor(() => expect(screen.getByTestId("dispatch-explore")).toBeDisabled());
    resolveCreate();
    await screen.findByTestId("home");
  });

  it("re-enables the dispatch-explore button and shows a visible alert when the atomic create-and-dispatch call fails", async () => {
    server.use(
      http.post("/api/workspaces", () =>
        HttpResponse.json({ detail: "team not admitted" }, { status: 422 }),
      ),
    );
    renderExploreChat();
    const user = await summarizeTo("Scope: the whole system");
    await screen.findByTestId("dispatch-proposal-confirm");
    await user.click(screen.getByTestId("dispatch-proposal-confirm"));
    await waitFor(() => expect(screen.getByTestId("dispatch-explore")).not.toBeDisabled());
    expect(screen.getByTestId("dispatch-explore")).toHaveTextContent("Dispatch consultant");
    expect(screen.getByRole("alert")).toHaveTextContent("team not admitted");
  });

  it("opens the summary formatted by default, and the Edit/Preview toggle round-trips an in-progress, unblurred edit without losing it", async () => {
    server.use(
      http.put(`${BASE}/${SID}/brief`, async ({ request }) => {
        const body = (await request.json()) as { content: string };
        return HttpResponse.json({ content: body.content, updated_at: "2026-06-01T02:00:00Z" });
      }),
    );
    renderExploreChat();
    const user = await summarizeTo("original brief");
    expect(screen.getByTestId("summary-source-toggle")).toHaveTextContent("Edit");
    expect(screen.getByTestId("summary-source-toggle")).not.toHaveAttribute("aria-pressed");
    expect(screen.getByTestId("summary-preview")).toBeInTheDocument();

    await user.click(screen.getByTestId("summary-source-toggle"));
    expect(screen.getByTestId("summary-source-toggle")).toHaveTextContent("Preview");
    expect(screen.getByTestId("summary-source-toggle")).not.toHaveAttribute("aria-pressed");
    const textarea = screen.getByTestId("summary-textarea");
    expect(textarea).toHaveFocus();

    await user.clear(textarea);
    await user.type(textarea, "still typing, not blurred yet");

    await user.click(screen.getByTestId("summary-source-toggle"));
    await waitFor(() =>
      expect(screen.getByTestId("summary-preview")).toHaveTextContent(
        "still typing, not blurred yet",
      ),
    );
    expect(screen.queryByTestId("summary-textarea")).not.toBeInTheDocument();
    expect(screen.getByTestId("summary-source-toggle")).toHaveTextContent("Edit");

    await user.click(screen.getByTestId("summary-source-toggle"));
    await waitFor(() =>
      expect(screen.getByTestId("summary-textarea")).toHaveValue(
        "still typing, not blurred yet",
      ),
    );
    expect(screen.getByTestId("summary-source-toggle")).toHaveTextContent("Preview");
  });

  it("summary tab opens formatted (toggle reads 'Edit') even before a brief exists", async () => {
    const user = userEvent.setup();
    renderExploreChat();
    await waitFor(() => expect(screen.getByTestId("tab-summary")).toBeInTheDocument());
    await user.click(screen.getByTestId("tab-summary"));
    expect(screen.getByTestId("summary-source-toggle")).toHaveTextContent("Edit");
    expect(screen.queryByTestId("summary-textarea")).not.toBeInTheDocument();
  });

  it("switching sessions via Clear resets an edit-mode summary back to preview", async () => {
    const NEW_SID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee";
    const confirmSpy = vi.spyOn(globalThis, "confirm").mockReturnValue(true);
    server.use(
      http.delete(/\/api\/explore-chat\/sessions\/[^/]+$/, () =>
        new HttpResponse(null, { status: 204 }),
      ),
      http.get(`${BASE}/${NEW_SID}`, () => HttpResponse.json(sessionResp({ session_id: NEW_SID }))),
    );
    const user = userEvent.setup();
    renderExploreChat();
    await summarizeTo("original brief");
    await user.click(screen.getByTestId("summary-source-toggle"));
    expect(screen.getByTestId("summary-textarea")).toBeInTheDocument();

    await user.click(screen.getByTestId("tab-chat"));
    await waitFor(() => expect(screen.getByTestId("clear-button")).not.toBeDisabled());
    server.use(
      http.post(BASE, () => HttpResponse.json({ session_id: NEW_SID }, { status: 201 })),
    );
    await user.click(screen.getByTestId("clear-button"));
    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId("tab-chat")).toHaveAttribute("aria-selected", "true"));

    await user.click(screen.getByTestId("tab-summary"));
    expect(screen.getByTestId("summary-source-toggle")).toHaveTextContent("Edit");
    expect(screen.queryByTestId("summary-textarea")).not.toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("summarizing again resets an edit-mode summary back to preview", async () => {
    renderExploreChat();
    const user = await summarizeTo("first brief");
    await user.click(screen.getByTestId("summary-source-toggle"));
    expect(screen.getByTestId("summary-textarea")).toBeInTheDocument();

    await user.click(screen.getByTestId("tab-chat"));
    await summarizeTo("second brief");
    expect(screen.getByTestId("summary-source-toggle")).toHaveTextContent("Edit");
    expect(screen.queryByTestId("summary-textarea")).not.toBeInTheDocument();
  });

  it("can switch back to Chat tab from Summary", async () => {
    const user = userEvent.setup();
    renderExploreChat();
    await waitFor(() => expect(screen.getByTestId("tab-summary")).toBeInTheDocument());
    await user.click(screen.getByTestId("tab-summary"));
    await user.click(screen.getByTestId("tab-chat"));
    expect(screen.getByLabelText("Message")).toBeInTheDocument();
  });

  it("Stop button interrupts an in-flight turn", async () => {
    const interruptCalls: number[] = [];
    server.use(
      http.get(`${BASE}/${SID}`, () =>
        HttpResponse.json(sessionResp({ in_flight_turn: { started_at: "t", user_text: "q" } })),
      ),
      http.post(`${BASE}/${SID}/interrupt`, () => {
        interruptCalls.push(1);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderExploreChat();
    await waitFor(() => expect(screen.getByTestId("stop-button")).toBeInTheDocument());
    await user.click(screen.getByTestId("stop-button"));
    await waitFor(() => expect(interruptCalls.length).toBeGreaterThan(0));
  });

  it("clear button confirms and creates a new session", async () => {
    const confirmSpy = vi.spyOn(globalThis, "confirm").mockReturnValue(true);
    const deleteCalls: string[] = [];
    server.use(
      http.delete(/\/api\/explore-chat\/sessions\/[^/]+$/, ({ request }) => {
        deleteCalls.push(request.url);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderExploreChat();
    await waitFor(() => expect(screen.getByTestId("clear-button")).not.toBeDisabled());
    await user.click(screen.getByTestId("clear-button"));
    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() => expect(deleteCalls.length).toBeGreaterThan(0));
    confirmSpy.mockRestore();
  });

  it("clear button does nothing when confirm is declined", async () => {
    const confirmSpy = vi.spyOn(globalThis, "confirm").mockReturnValue(false);
    const deleteCalls: string[] = [];
    server.use(
      http.delete(/\/api\/explore-chat\/sessions\/[^/]+$/, ({ request }) => {
        deleteCalls.push(request.url);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderExploreChat();
    await waitFor(() => expect(screen.getByTestId("clear-button")).not.toBeDisabled());
    await user.click(screen.getByTestId("clear-button"));
    expect(confirmSpy).toHaveBeenCalled();
    expect(deleteCalls).toHaveLength(0);
    confirmSpy.mockRestore();
  });

  it("research consultant uses research as dispatch team", async () => {
    server.use(
      http.get(`${BASE}/${SID}`, () =>
        HttpResponse.json({ ...sessionResp(), consultant_key: "research" }),
      ),
      http.post(`${BASE}/${SID}/propose`, () =>
        HttpResponse.json(readyProposal(COUNTER_CASE, { team: "research" })),
      ),
    );
    let dispatched: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        dispatched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ task_id: "ws-r", root_task_ids: ["ws-r"] });
      }),
      http.delete(/\/api\/explore-chat\/sessions\/[^/]+$/, () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    renderExploreChat("research");
    const user = await summarizeTo("Scope: some research");
    await screen.findByTestId("dispatch-proposal-confirm");
    await user.click(screen.getByTestId("dispatch-proposal-confirm"));
    expect(await screen.findByTestId("home")).toBeInTheDocument();
    expect(dispatched).toMatchObject({ team: "research" });
  });

  it("does nothing on send when draft is empty", async () => {
    const sent: string[] = [];
    server.use(
      http.post(`${BASE}/${SID}/messages`, async ({ request }) => {
        sent.push(((await request.json()) as { content: string }).content);
        return HttpResponse.json({ status: "accepted" }, { status: 202 });
      }),
    );
    renderExploreChat();
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    document.querySelector("form")?.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
    expect(sent).toHaveLength(0);
  });
});

describe("ExploreChat — listed default repository", () => {
  installExploreChatPageSetup();

  const SECOND_REPO_KEY = "acme-org/widgets";
  const CONSULTANT = "brainstorming";

  function captureCreates() {
    const created: { consultant_key?: string; engine_key?: string }[] = [];
    server.use(
      http.post(BASE, async ({ request }) => {
        created.push((await request.json()) as { consultant_key?: string; engine_key?: string });
        return HttpResponse.json({ session_id: SID }, { status: 201 });
      }),
    );
    return created;
  }

  it("opens without the chooser and creates the session for the listed default when it is the only repository", async () => {
    const created = captureCreates();
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY)],
        defaultKey: LISTED_DEFAULT_KEY,
      }),
    );
    renderExploreChat();
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
    expect(created).toEqual([{ consultant_key: CONSULTANT, engine_key: LISTED_DEFAULT_KEY }]);
  });

  it("opens without the chooser and creates the session for a lone registered repository when the list names no default", async () => {
    const created = captureCreates();
    server.use(engineListingHandler({ engines: [makeEngineSummaryFor(SECOND_REPO_KEY)] }));
    renderExploreChat();
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
    expect(created).toEqual([{ consultant_key: CONSULTANT, engine_key: SECOND_REPO_KEY }]);
  });

  it("offers the listed default and the second repository in the chooser, never the built-in key, and creates nothing until a pick", async () => {
    const created = captureCreates();
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY), makeEngineSummaryFor(SECOND_REPO_KEY)],
        defaultKey: LISTED_DEFAULT_KEY,
      }),
    );
    renderExploreChat();
    await screen.findByTestId("repo-chooser-modal");
    expect(screen.getByTestId(`repo-option-${LISTED_DEFAULT_KEY}`)).toBeInTheDocument();
    expect(screen.getByTestId(`repo-option-${SECOND_REPO_KEY}`)).toBeInTheDocument();
    expect(
      screen.queryByTestId(`repo-option-${STUB_DEFAULT_ENGINE_KEY}`),
    ).not.toBeInTheDocument();
    expect(created).toEqual([]);
  });

  it("binds a session to the listed default when it is picked from the chooser", async () => {
    const created = captureCreates();
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY), makeEngineSummaryFor(SECOND_REPO_KEY)],
        defaultKey: LISTED_DEFAULT_KEY,
      }),
    );
    renderExploreChat();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId(`repo-option-${LISTED_DEFAULT_KEY}`));
    await waitFor(() =>
      expect(created).toEqual([{ consultant_key: CONSULTANT, engine_key: LISTED_DEFAULT_KEY }]),
    );
  });

  it.each(DEFAULT_KEY_STATES.filter(([state]) => state !== "other"))(
    "opens without the chooser and creates the session for the built-in key when it is the only repository and the listed default is absent or equal (%s)",
    async (_state, defaultKey) => {
      const created = captureCreates();
      server.use(
        engineListingHandler({
          engines: [makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY)],
          defaultKey,
        }),
      );
      renderExploreChat();
      await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
      expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
      expect(created).toEqual([
        { consultant_key: CONSULTANT, engine_key: STUB_DEFAULT_ENGINE_KEY },
      ]);
    },
  );

  it("shows the chooser for a lone built-in-key repository when the listed default is another repository", async () => {
    const created = captureCreates();
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY)],
        defaultKey: LISTED_DEFAULT_KEY,
      }),
    );
    renderExploreChat();
    await screen.findByTestId("repo-chooser-modal");
    expect(screen.getByTestId(`repo-option-${STUB_DEFAULT_ENGINE_KEY}`)).toBeInTheDocument();
    expect(screen.getByTestId(`repo-option-${LISTED_DEFAULT_KEY}`)).toBeInTheDocument();
    expect(created).toEqual([]);
  });
});
