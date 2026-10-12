import { act, screen, waitFor, within } from "@testing-library/react";
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
  BUILD_CHAT_PATH,
  SID,
  installBuildChatPageSetup,
  readyProposal,
  renderBuildChat,
  sessionResp,
  summarizeTo,
} from "../../../test/buildChatPageHarness";

describe("BuildChat", () => {
  installBuildChatPageSetup();

  it("shows bootstrap error when session creation fails", async () => {
    server.use(
      http.post(BUILD_CHAT_PATH, () => HttpResponse.json({ detail: "server error" }, { status: 500 })),
    );
    renderBuildChat();
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent("Could not start build session");
  });

  it("renders the Build heading and Chat/Summary tabs once loaded", async () => {
    renderBuildChat();
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1, name: "Build" })).toBeInTheDocument(),
    );
    expect(screen.getByTestId("tab-chat")).toBeInTheDocument();
    expect(screen.getByTestId("tab-summary")).toBeInTheDocument();
    expect(screen.queryByTestId("tab-questions")).not.toBeInTheDocument();
  });

  it("creates a new session on fresh visit", async () => {
    renderBuildChat();
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
  });

  it("shows the persisted run-summary header when the session GET returns run_summary", async () => {
    server.use(
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
              tool_counts: [{ name: "Read", count: 4 }],
            },
          }),
        ),
      ),
    );
    renderBuildChat();
    expect(await screen.findByTestId("run-summary-header-conversation")).toBeInTheDocument();
    expect(screen.getByTestId("run-summary-cost-conversation")).toHaveTextContent("$0.0500");
    expect(screen.getByTestId("run-summary-tool-conversation-Read")).toHaveTextContent("Read × 4");
  });

  it("does not show a run-summary header when the session has no run_summary yet", async () => {
    renderBuildChat();
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    expect(screen.queryByTestId(/run-summary-header/)).not.toBeInTheDocument();
  });

  it("sends a message and clears the draft", async () => {
    const sent: string[] = [];
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/messages`, async ({ request }) => {
        sent.push(((await request.json()) as { content: string }).content);
        return HttpResponse.json({ status: "accepted" }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderBuildChat();
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    await user.type(screen.getByLabelText("Message"), "build a thing");
    await user.click(screen.getByRole("button", { name: /Send/ }));
    await waitFor(() => expect(sent).toContain("build a thing"));
  });

  it("does nothing when submitting an empty draft", async () => {
    const sent: string[] = [];
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/messages`, async ({ request }) => {
        sent.push(((await request.json()) as { content: string }).content);
        return HttpResponse.json({ status: "accepted" }, { status: 202 });
      }),
    );
    renderBuildChat();
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    act(() => {
      document.querySelector("form")?.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
    });
    expect(sent).toHaveLength(0);
  });

  it("summarize switches to Summary tab and shows the brief, formatted by default", async () => {
    renderBuildChat();
    await summarizeTo("Build the X feature");
    expect(screen.getByTestId("summary-preview")).toHaveTextContent("Build the X feature");
    expect(screen.queryByTestId("summary-textarea")).not.toBeInTheDocument();
  });

  it("shows an alert when summarize fails", async () => {
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/summarize`, () =>
        HttpResponse.json({ detail: "claude failed" }, { status: 502 }),
      ),
    );
    const user = userEvent.setup();
    renderBuildChat();
    await waitFor(() => expect(screen.getByTestId("summarize-button")).not.toBeDisabled());
    await user.click(screen.getByTestId("summarize-button"));
    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThan(0));
  });

  it("disables Dispatch plan until every open question is answered", async () => {
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
        HttpResponse.json({
          status: "need_more_info",
          questions: ["Which database?", "Which auth?"],
        }),
      ),
    );
    renderBuildChat();
    const user = await summarizeTo("Build the X feature");
    await waitFor(() => expect(screen.getByTestId("answer-0")).toBeInTheDocument());
    expect(screen.getByTestId("dispatch-build")).toBeDisabled();
    await user.type(screen.getByTestId("answer-0"), "Postgres");
    expect(screen.getByTestId("dispatch-build")).toBeDisabled();
    await user.type(screen.getByTestId("answer-1"), "OAuth");
    expect(screen.getByTestId("dispatch-build")).not.toBeDisabled();
  });

  const COUNTER_CASE = {
    against: "the acceptance clauses are still thin",
    cheapest_80: "ship the read path only, defer writes",
    cost_estimate: "insufficient data",
  };

  it("ready extract shows the counter-case gate before dispatching, and confirming dispatches (fused create-and-dispatch call) and navigates home", async () => {
    let dispatched: Record<string, unknown> | null = null;
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
        HttpResponse.json(readyProposal(COUNTER_CASE, { title: "add-x" })),
      ),
      http.post("/api/workspaces", async ({ request }) => {
        dispatched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ task_id: "add-x", root_task_ids: ["add-x"] });
      }),
      http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    renderBuildChat();
    const user = await summarizeTo("Build the X feature");
    await screen.findByTestId("dispatch-proposal-confirm");
    expect(screen.getByTestId("dispatch-counter-case-against")).toHaveTextContent(
      COUNTER_CASE.against,
    );
    expect(screen.getByTestId("dispatch-counter-case-cheapest-80")).toHaveTextContent(
      COUNTER_CASE.cheapest_80,
    );
    expect(screen.queryByTestId("home")).toBeNull();
    await user.click(screen.getByTestId("dispatch-proposal-confirm"));
    expect(await screen.findByTestId("home")).toBeInTheDocument();
    expect(dispatched).toMatchObject({
      team: "implementation_plan",
      counter_case: COUNTER_CASE,
    });
  });

  it("dispatches with the session's own engine_key for a project-less, non-default-repo session", async () => {
    const REPO_ENGINE_KEY = "acme-org/widgets";
    let dispatched: Record<string, unknown> | null = null;
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json(sessionResp({ engine_key: REPO_ENGINE_KEY })),
      ),
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
        HttpResponse.json(readyProposal(COUNTER_CASE, { title: "add-x" })),
      ),
      http.post("/api/workspaces", async ({ request }) => {
        dispatched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ task_id: "add-x", root_task_ids: ["add-x"] });
      }),
      http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    renderBuildChat();
    const user = await summarizeTo("Build the X feature");
    await user.click(await screen.findByTestId("dispatch-proposal-confirm"));
    expect(await screen.findByTestId("home")).toBeInTheDocument();
    expect(dispatched).toMatchObject({ engine_key: REPO_ENGINE_KEY });
    expect(dispatched).not.toHaveProperty("project_id");
  });

  it("disables Build plan while the atomic create-and-dispatch call is in flight, then navigates home once it resolves", async () => {
    let resolveCreate!: () => void;
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
        HttpResponse.json(readyProposal(COUNTER_CASE, { title: "add-x" })),
      ),
      http.post("/api/workspaces", async () => {
        await new Promise<void>((res) => { resolveCreate = res; });
        return HttpResponse.json({ task_id: "add-x", root_task_ids: ["add-x"] });
      }),
      http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    renderBuildChat();
    const user = await summarizeTo("Build the X feature");
    await user.click(await screen.findByTestId("dispatch-proposal-confirm"));
    await waitFor(() => expect(screen.queryByTestId("dispatch-proposal-confirm")).toBeNull());
    await waitFor(() => expect(screen.getByTestId("build-plan")).toBeDisabled());
    resolveCreate();
    await screen.findByTestId("home");
  });

  it("re-enables Build plan and shows a visible alert when the atomic create-and-dispatch call fails", async () => {
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
        HttpResponse.json(readyProposal(COUNTER_CASE, { title: "add-x" })),
      ),
      http.post("/api/workspaces", () =>
        HttpResponse.json({ detail: "team not admitted" }, { status: 422 }),
      ),
    );
    renderBuildChat();
    const user = await summarizeTo("Build the X feature");
    await user.click(await screen.findByTestId("dispatch-proposal-confirm"));
    await waitFor(() => expect(screen.getByTestId("build-plan")).not.toBeDisabled());
    expect(screen.getByRole("alert")).toHaveTextContent("team not admitted");
  });

  it("shows the Preparing… label while extracting, reusing the in-flight prefetch (exactly one propose call)", async () => {
    let resolveExtract!: () => void;
    let proposeCalls = 0;
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, async () => {
        proposeCalls += 1;
        await new Promise<void>((res) => { resolveExtract = res; });
        return HttpResponse.json(readyProposal(COUNTER_CASE));
      }),
      http.post("/api/workspaces", () =>
        HttpResponse.json({ task_id: "t", root_task_ids: ["t"] }),
      ),
      http.post("/api/workspaces/t/tasks/t/dispatch", () =>
        HttpResponse.json({ job_id: 1, invocation_id: "inv_001" }),
      ),
      http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    renderBuildChat();
    const user = await summarizeTo("Build the X feature");
    await waitFor(() => expect(screen.getByTestId("build-plan")).toHaveTextContent("Preparing…"));
    resolveExtract();
    await user.click(await screen.findByTestId("dispatch-proposal-confirm"));
    await screen.findByTestId("home");
    expect(proposeCalls).toBe(1);
  });

  it("shows an extract error when extract fails", async () => {
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
        HttpResponse.json({ detail: "extract failed" }, { status: 502 }),
      ),
    );
    renderBuildChat();
    const user = await summarizeTo("Build the X feature");
    await user.click(screen.getByTestId("build-plan"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("502"));
  });

  it("need_more_info re-proposes after answers, shows the counter-case gate, and dispatches with clarifications", async () => {
    let created: { title: string; description: string } | null = null;
    let extractCalls = 0;
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => {
        extractCalls += 1;
        if (extractCalls === 1) {
          return HttpResponse.json({
            status: "need_more_info",
            questions: ["Which database?"],
          });
        }
        return HttpResponse.json(readyProposal(COUNTER_CASE, { title: "My Build" }));
      }),
      http.put(`${BUILD_CHAT_PATH}/${SID}/brief`, async ({ request }) => {
        const body = (await request.json()) as { content: string };
        return HttpResponse.json({ content: body.content, updated_at: "2026-05-01T02:00:00Z" });
      }),
      http.post("/api/workspaces", async ({ request }) => {
        created = (await request.json()) as { title: string; description: string };
        return HttpResponse.json({ task_id: "build", root_task_ids: ["build"] });
      }),
      http.post("/api/workspaces/build/tasks/build/dispatch", () =>
        HttpResponse.json({ job_id: 1, invocation_id: "inv_001" }),
      ),
      http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    renderBuildChat();
    const user = await summarizeTo("# My Build\nDetails here");
    await waitFor(() => expect(screen.getByTestId("answer-0")).toBeInTheDocument());
    await user.type(screen.getByTestId("answer-0"), "Postgres");
    await user.click(screen.getByTestId("dispatch-build"));
    await screen.findByTestId("dispatch-proposal-confirm");
    expect(screen.getByTestId("dispatch-counter-case-against")).toHaveTextContent(
      COUNTER_CASE.against,
    );
    await user.click(screen.getByTestId("dispatch-proposal-confirm"));
    expect(await screen.findByTestId("home")).toBeInTheDocument();
    expect(created).not.toBeNull();
    const payload = created as unknown as { title: string; description: string };
    expect(payload.title).toBe("My Build");
    expect(payload.description).toContain("## Clarifications");
    expect(payload.description).toContain("Which database?");
    expect(payload.description).toContain("Postgres");
  });

  it("shows an extract error when re-extracting after answering questions fails", async () => {
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
        HttpResponse.json({
          status: "need_more_info",
          questions: ["Why?"],
        }),
      ),
      http.put(`${BUILD_CHAT_PATH}/${SID}/brief`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderBuildChat();
    const user = await summarizeTo("Build the X feature");
    await waitFor(() => expect(screen.getByTestId("answer-0")).toBeInTheDocument());
    await user.type(screen.getByTestId("answer-0"), "because");
    await user.click(screen.getByTestId("dispatch-build"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("500"));
  });

  it("persists an edited brief on blur", async () => {
    const putCalls: string[] = [];
    server.use(
      http.put(`${BUILD_CHAT_PATH}/${SID}/brief`, async ({ request }) => {
        putCalls.push(((await request.json()) as { content: string }).content);
        return HttpResponse.json({ content: "edited", updated_at: "2026-05-01T02:00:00Z" });
      }),
    );
    renderBuildChat();
    const user = await summarizeTo("original brief");
    await user.click(screen.getByTestId("summary-source-toggle"));
    const textarea = screen.getByTestId("summary-textarea");
    await user.clear(textarea);
    await user.type(textarea, "edited");
    await user.tab();
    await waitFor(() => expect(putCalls).toContain("edited"));
  });

  it("does not steal focus back into the textarea when a blur-triggered brief PUT resolves and remounts the tab without the toggle being used", async () => {
    const putCalls: string[] = [];
    server.use(
      http.put(`${BUILD_CHAT_PATH}/${SID}/brief`, async ({ request }) => {
        putCalls.push(((await request.json()) as { content: string }).content);
        return HttpResponse.json({ content: "edited", updated_at: "2026-05-01T02:00:00Z" });
      }),
    );
    renderBuildChat();
    const user = await summarizeTo("original brief");
    await user.click(screen.getByTestId("summary-source-toggle"));
    const textarea = screen.getByTestId("summary-textarea");
    await user.clear(textarea);
    await user.type(textarea, "edited");
    await user.tab();
    await waitFor(() => expect(putCalls).toContain("edited"));
    await waitFor(() => expect(textarea).not.toBeInTheDocument());
    expect(screen.getByTestId("summary-textarea")).not.toHaveFocus();
  });

  it("opens the summary formatted by default, and the Edit/Preview toggle round-trips an in-progress, unblurred edit without losing it", async () => {
    server.use(
      http.put(`${BUILD_CHAT_PATH}/${SID}/brief`, async ({ request }) => {
        const body = (await request.json()) as { content: string };
        return HttpResponse.json({ content: body.content, updated_at: "2026-05-01T02:00:00Z" });
      }),
    );
    renderBuildChat();
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
    renderBuildChat();
    await waitFor(() => expect(screen.getByTestId("tab-summary")).toBeInTheDocument());
    await user.click(screen.getByTestId("tab-summary"));
    expect(screen.getByTestId("summary-source-toggle")).toHaveTextContent("Edit");
    expect(screen.queryByTestId("summary-textarea")).not.toBeInTheDocument();
  });

  it("switching sessions via Clear resets an edit-mode summary back to preview", async () => {
    const NEW_SID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee";
    const NEW_SID_ENGINE_KEY = "acme-org/widgets";
    const confirmSpy = vi.spyOn(globalThis, "confirm").mockReturnValue(true);
    server.use(
      http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () =>
        new HttpResponse(null, { status: 204 }),
      ),
      http.get(`${BUILD_CHAT_PATH}/${NEW_SID}`, () =>
        HttpResponse.json(sessionResp({ session_id: NEW_SID, engine_key: NEW_SID_ENGINE_KEY })),
      ),
    );
    const user = userEvent.setup();
    renderBuildChat();
    await summarizeTo("original brief");
    await user.click(screen.getByTestId("summary-source-toggle"));
    expect(screen.getByTestId("summary-textarea")).toBeInTheDocument();

    await user.click(screen.getByTestId("tab-chat"));
    await waitFor(() => expect(screen.getByTestId("clear-button")).not.toBeDisabled());
    server.use(
      http.post(BUILD_CHAT_PATH, () => HttpResponse.json({ session_id: NEW_SID }, { status: 201 })),
    );
    await user.click(screen.getByTestId("clear-button"));
    expect(confirmSpy).toHaveBeenCalled();
    const header = await screen.findByTestId("build-chat-engine-header");
    expect(within(header).getByTestId("engine-badge-build-chat-header")).toHaveTextContent(
      NEW_SID_ENGINE_KEY,
    );

    await user.click(screen.getByTestId("tab-summary"));
    expect(screen.getByTestId("summary-source-toggle")).toHaveTextContent("Edit");
    expect(screen.queryByTestId("summary-textarea")).not.toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("summarizing again resets an edit-mode summary back to preview", async () => {
    renderBuildChat();
    const user = await summarizeTo("first brief");
    await user.click(screen.getByTestId("summary-source-toggle"));
    expect(screen.getByTestId("summary-textarea")).toBeInTheDocument();

    await user.click(screen.getByTestId("tab-chat"));
    await summarizeTo("second brief");
    expect(screen.getByTestId("summary-source-toggle")).toHaveTextContent("Edit");
    expect(screen.queryByTestId("summary-textarea")).not.toBeInTheDocument();
  });

  it("Stop button interrupts an in-flight turn", async () => {
    const interruptCalls: number[] = [];
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
        HttpResponse.json(sessionResp({ in_flight_turn: { started_at: "t", user_text: "q" } })),
      ),
      http.post(`${BUILD_CHAT_PATH}/${SID}/interrupt`, () => {
        interruptCalls.push(1);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderBuildChat();
    await waitFor(() => expect(screen.getByTestId("stop-button")).toBeInTheDocument());
    await user.click(screen.getByTestId("stop-button"));
    await waitFor(() => expect(interruptCalls.length).toBeGreaterThan(0));
  });

  it("clear button confirms and creates a new session", async () => {
    const confirmSpy = vi.spyOn(globalThis, "confirm").mockReturnValue(true);
    const deleteCalls: string[] = [];
    server.use(
      http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, ({ request }) => {
        deleteCalls.push(request.url);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderBuildChat();
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
      http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, ({ request }) => {
        deleteCalls.push(request.url);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderBuildChat();
    await waitFor(() => expect(screen.getByTestId("clear-button")).not.toBeDisabled());
    await user.click(screen.getByTestId("clear-button"));
    expect(confirmSpy).toHaveBeenCalled();
    expect(deleteCalls).toHaveLength(0);
    confirmSpy.mockRestore();
  });

  it("can switch back to the Chat tab", async () => {
    const user = userEvent.setup();
    renderBuildChat();
    await waitFor(() => expect(screen.getByTestId("tab-summary")).toBeInTheDocument());
    await user.click(screen.getByTestId("tab-summary"));
    await user.click(screen.getByTestId("tab-chat"));
    expect(screen.getByLabelText("Message")).toBeInTheDocument();
  });

  describe("Propose prefetch (2026-07-21 dispatch-flow speed audit F7)", () => {
    async function flushMicrotasks() {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
    }

    it("shows the confirm button instantly with no Preparing… flicker once the prefetch has resolved", async () => {
      let proposeCalls = 0;
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => {
          proposeCalls += 1;
          return HttpResponse.json(readyProposal(COUNTER_CASE, { title: "add-x" }));
        }),
      );
      renderBuildChat();
      await summarizeTo("Build the X feature");
      await waitFor(() => expect(proposeCalls).toBe(1));
      await flushMicrotasks();
      expect(screen.queryByText("Preparing…")).not.toBeInTheDocument();
      expect(screen.getByTestId("dispatch-proposal-confirm")).toBeInTheDocument();
      expect(proposeCalls).toBe(1);
    });

    it("need_more_info from an already-resolved prefetch renders the questions body immediately, with no extra propose call", async () => {
      let proposeCalls = 0;
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => {
          proposeCalls += 1;
          return HttpResponse.json({ status: "need_more_info", questions: ["Which database?"] });
        }),
      );
      renderBuildChat();
      await summarizeTo("Build the X feature");
      await waitFor(() => expect(proposeCalls).toBe(1));
      await flushMicrotasks();
      expect(screen.getByTestId("answer-0")).toBeInTheDocument();
      expect(proposeCalls).toBe(1);
    });

    it("editing the brief after Summarize discards the stale prefetch — repropose dispatches the fresh proposal, never the stale one", async () => {
      let dispatched: Record<string, unknown> | null = null;
      server.use(
        http.put(`${BUILD_CHAT_PATH}/${SID}/brief`, async ({ request }) => {
          const body = (await request.json()) as { content: string };
          return HttpResponse.json({ content: body.content, updated_at: "2026-05-01T02:00:00Z" });
        }),
        http.post(`${BUILD_CHAT_PATH}/${SID}/interrupt`, () => new HttpResponse(null, { status: 204 })),
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(readyProposal(COUNTER_CASE, { title: "stale-title" })),
        ),
        http.post("/api/workspaces", async ({ request }) => {
          dispatched = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] });
        }),
        http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () => new HttpResponse(null, { status: 204 })),
      );
      renderBuildChat();
      const user = await summarizeTo("original brief");
      await screen.findByTestId("dispatch-proposal-confirm");
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(readyProposal(COUNTER_CASE, { title: "fresh-title" })),
        ),
      );
      await user.click(screen.getByTestId("summary-source-toggle"));
      const textarea = screen.getByTestId("summary-textarea");
      await user.clear(textarea);
      await user.type(textarea, "edited brief");
      await user.tab();
      await waitFor(() => expect(screen.getByTestId("summary-textarea")).toHaveValue("edited brief"));
      await user.click(await screen.findByTestId("dispatch-proposal-repropose"));
      await user.click(await screen.findByTestId("dispatch-proposal-confirm"));
      await screen.findByTestId("home");
      expect(dispatched).toMatchObject({ title: "fresh-title", description: "edited brief" });
    });

    it("sending a chat message right after Summarize cancels the still-pending prefetch before sending", async () => {
      let interruptCalls = 0;
      const sentMessages: string[] = [];
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => new Promise(() => undefined)),
        http.post(`${BUILD_CHAT_PATH}/${SID}/interrupt`, () => {
          interruptCalls += 1;
          return new HttpResponse(null, { status: 204 });
        }),
        http.post(`${BUILD_CHAT_PATH}/${SID}/messages`, async ({ request }) => {
          sentMessages.push(((await request.json()) as { content: string }).content);
          return HttpResponse.json({ status: "accepted" }, { status: 202 });
        }),
      );
      renderBuildChat();
      const user = await summarizeTo("Build the X feature");
      await user.click(screen.getByTestId("tab-chat"));
      await user.type(screen.getByLabelText("Message"), "follow-up question");
      await user.click(screen.getByRole("button", { name: /Send/ }));
      await waitFor(() => expect(sentMessages).toContain("follow-up question"), { timeout: 2000 });
      expect(interruptCalls).toBeGreaterThanOrEqual(1);
    });

    it("clicking Summarize a second time cancels the first outstanding prefetch and issues a fresh one", async () => {
      let interruptCalls = 0;
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => new Promise(() => undefined)),
        http.post(`${BUILD_CHAT_PATH}/${SID}/interrupt`, () => {
          interruptCalls += 1;
          return new HttpResponse(null, { status: 204 });
        }),
      );
      renderBuildChat();
      const user = await summarizeTo("first brief");
      expect(screen.getByTestId("summary-preview")).toHaveTextContent("first brief");
      await user.click(screen.getByTestId("tab-chat"));
      await summarizeTo("second brief");
      expect(screen.getByTestId("summary-preview")).toHaveTextContent("second brief");
      expect(interruptCalls).toBeGreaterThanOrEqual(1);
    });

    it("unmounting during an outstanding prefetch triggers a best-effort interrupt", async () => {
      let interruptCalls = 0;
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => new Promise(() => undefined)),
        http.post(`${BUILD_CHAT_PATH}/${SID}/interrupt`, () => {
          interruptCalls += 1;
          return new HttpResponse(null, { status: 204 });
        }),
      );
      const { unmount } = renderBuildChat();
      await summarizeTo("Build the X feature");
      unmount();
      await waitFor(() => expect(interruptCalls).toBeGreaterThanOrEqual(1));
    });

    it("an issued-but-never-consumed prefetch still lands its run-summary attribution once it resolves", async () => {
      server.use(
        http.get(`${BUILD_CHAT_PATH}/${SID}`, () =>
          HttpResponse.json(
            sessionResp({
              run_summary: {
                model: "opus",
                effort: "xhigh",
                system_prompt: "build chat system prompt",
                total_cost_usd: 0.16,
                total_output_tokens: 40,
                total_input_tokens: 60,
                tool_counts: [],
              },
            }),
          ),
        ),
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(readyProposal(COUNTER_CASE)),
        ),
      );
      renderBuildChat();
      await summarizeTo("Build the X feature");
      await waitFor(() =>
        expect(screen.getByTestId("run-summary-cost-conversation")).toHaveTextContent("$0.1600"),
      );
    });
  });

  describe("Prefetch cleanup trigger conditions", () => {
    it("interrupts an outstanding prefetch when the screen unmounts", async () => {
      let interruptCalls = 0;
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => new Promise(() => undefined)),
        http.post(`${BUILD_CHAT_PATH}/${SID}/interrupt`, () => {
          interruptCalls += 1;
          return new HttpResponse(null, { status: 204 });
        }),
      );
      const { unmount } = renderBuildChat();
      await summarizeTo("Build the X feature");
      unmount();
      await waitFor(() => expect(interruptCalls).toBeGreaterThanOrEqual(1));
    });

    it("interrupts the outstanding prefetch for the old session (not the new one) when the session id switches", async () => {
      const NEW_SID = "cccccccc-cccc-cccc-cccc-cccccccccccc";
      const oldInterruptCalls: number[] = [];
      const newInterruptCalls: number[] = [];
      const confirmSpy = vi.spyOn(globalThis, "confirm").mockReturnValue(true);
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => new Promise(() => undefined)),
        http.post(`${BUILD_CHAT_PATH}/${SID}/interrupt`, () => {
          oldInterruptCalls.push(1);
          return new HttpResponse(null, { status: 204 });
        }),
        http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () =>
          new HttpResponse(null, { status: 204 }),
        ),
        http.get(`${BUILD_CHAT_PATH}/${NEW_SID}`, () =>
          HttpResponse.json(sessionResp({ session_id: NEW_SID })),
        ),
        http.post(`${BUILD_CHAT_PATH}/${NEW_SID}/interrupt`, () => {
          newInterruptCalls.push(1);
          return new HttpResponse(null, { status: 204 });
        }),
      );
      renderBuildChat();
      const user = await summarizeTo("Build the X feature");
      await user.click(screen.getByTestId("tab-chat"));
      await waitFor(() => expect(screen.getByTestId("clear-button")).not.toBeDisabled());
      server.use(
        http.post(BUILD_CHAT_PATH, () => HttpResponse.json({ session_id: NEW_SID }, { status: 201 })),
      );
      await user.click(screen.getByTestId("clear-button"));
      expect(confirmSpy).toHaveBeenCalled();
      await waitFor(() => expect(oldInterruptCalls.length).toBeGreaterThanOrEqual(1));
      expect(newInterruptCalls).toHaveLength(0);
      confirmSpy.mockRestore();
    });
  });
});

describe("BuildChat — listed default repository", () => {
  installBuildChatPageSetup();

  const SECOND_REPO_KEY = "acme-org/widgets";

  function captureCreates() {
    const created: { engine_key?: string; project_id?: string }[] = [];
    server.use(
      http.post(BUILD_CHAT_PATH, async ({ request }) => {
        created.push((await request.json()) as { engine_key?: string; project_id?: string });
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
    renderBuildChat();
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
    expect(created).toEqual([{ engine_key: LISTED_DEFAULT_KEY }]);
  });

  it("opens without the chooser and creates the session for a lone registered repository when the list names no default", async () => {
    const created = captureCreates();
    server.use(engineListingHandler({ engines: [makeEngineSummaryFor(SECOND_REPO_KEY)] }));
    renderBuildChat();
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
    expect(created).toEqual([{ engine_key: SECOND_REPO_KEY }]);
  });

  it("offers the listed default and the second repository in the chooser, never the built-in key, and creates nothing until a pick", async () => {
    const created = captureCreates();
    server.use(
      engineListingHandler({
        engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY), makeEngineSummaryFor(SECOND_REPO_KEY)],
        defaultKey: LISTED_DEFAULT_KEY,
      }),
    );
    renderBuildChat();
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
    renderBuildChat();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId(`repo-option-${LISTED_DEFAULT_KEY}`));
    await waitFor(() => expect(created).toEqual([{ engine_key: LISTED_DEFAULT_KEY }]));
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
      renderBuildChat();
      await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
      expect(screen.queryByTestId("repo-chooser-modal")).not.toBeInTheDocument();
      expect(created).toEqual([{ engine_key: STUB_DEFAULT_ENGINE_KEY }]);
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
    renderBuildChat();
    await screen.findByTestId("repo-chooser-modal");
    expect(screen.getByTestId(`repo-option-${STUB_DEFAULT_ENGINE_KEY}`)).toBeInTheDocument();
    expect(screen.getByTestId(`repo-option-${LISTED_DEFAULT_KEY}`)).toBeInTheDocument();
    expect(created).toEqual([]);
  });
});
