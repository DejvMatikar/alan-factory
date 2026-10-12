import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import ExploreChat from "./ExploreChat";

const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const BASE = "/api/explore-chat/sessions";

function sessionResp(overrides = {}) {
  return {
    session_id: SID,
    consultant_key: "brainstorming",
    project_id: null,
    repo_provision: "not_needed",
    created_at: "2026-06-01T00:00:00Z",
    messages: [],
    in_flight_turn: null,
    brief: null,
    ...overrides,
  };
}

const COUNTER_CASE = {
  against: "the scope is still fuzzy",
  cheapest_80: "scope the review to one screen first",
  cost_estimate: "insufficient data",
};

function readyProposal(overrides: object = {}) {
  return {
    status: "ready",
    proposal: {
      team: "brainstorming",
      rationale: "The scope is clear enough to hand off to brainstorming.",
      instructions: null,
      scope_audit: [],
      title: "explore-thing",
      acceptance_clauses: [],
      counter_case: COUNTER_CASE,
      ...overrides,
    },
    collision: { state: "green", sentence: "No overlapping work found.", match: null },
  };
}

function renderExploreChat() {
  return render(
    <MemoryRouter initialEntries={["/explore/brainstorming"]}>
      <Routes>
        <Route path="/explore/:consultant" element={<ExploreChat />} />
        <Route path="/" element={<div data-testid="home">home</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function summarizeTo(content: string) {
  server.use(
    http.post(`${BASE}/${SID}/summarize`, () =>
      HttpResponse.json({ content, updated_at: "2026-06-01T01:00:00Z" }),
    ),
  );
  const user = userEvent.setup();
  await waitFor(() => expect(screen.getByTestId("summarize-button")).not.toBeDisabled());
  await user.click(screen.getByTestId("summarize-button"));
  await waitFor(() =>
    expect(screen.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true"),
  );
  await waitFor(() => expect(screen.queryByTestId("summary-empty")).not.toBeInTheDocument(), {
    timeout: 3000,
  });
  return user;
}

async function flushMicrotasks() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe("ExploreChat propose prefetch parity", () => {
  beforeEach(() => {
    localStorage.clear();
    server.use(http.get(`${BASE}/${SID}`, () => HttpResponse.json(sessionResp())));
  });

  it("issues exactly one propose call right after summarize settles, before Dispatch is ever clicked", async () => {
    let proposeCalls = 0;
    server.use(
      http.post(`${BASE}/${SID}/propose`, () => {
        proposeCalls += 1;
        return HttpResponse.json(readyProposal());
      }),
    );
    renderExploreChat();
    await summarizeTo("Scope: the whole system");
    await waitFor(() => expect(proposeCalls).toBe(1));
  });

  it("the settled prefetch renders the inline proposal panel automatically — no click, no second propose call", async () => {
    let proposeCalls = 0;
    server.use(
      http.post(`${BASE}/${SID}/propose`, () => {
        proposeCalls += 1;
        return HttpResponse.json(readyProposal({ title: "prefetched" }));
      }),
    );
    renderExploreChat();
    await summarizeTo("Scope: the whole system");
    await waitFor(() => expect(proposeCalls).toBe(1));
    expect(await screen.findByTestId("dispatch-proposal-confirm")).toBeInTheDocument();
    expect(proposeCalls).toBe(1);
  });

  it("editing the brief after summarize goes stale — repropose (not Dispatch) reproposes and never dispatches the stale title", async () => {
    let dispatched: Record<string, unknown> | null = null;
    server.use(
      http.put(`${BASE}/${SID}/brief`, async ({ request }) => {
        const body = (await request.json()) as { content: string };
        return HttpResponse.json({ content: body.content, updated_at: "2026-06-01T02:00:00Z" });
      }),
      http.post(`${BASE}/${SID}/interrupt`, () => new HttpResponse(null, { status: 204 })),
      http.post(`${BASE}/${SID}/propose`, () => HttpResponse.json(readyProposal({ title: "stale-title" }))),
      http.post("/api/workspaces", async ({ request }) => {
        dispatched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] });
      }),
      http.delete(/\/api\/explore-chat\/sessions\/[^/]+$/, () => new HttpResponse(null, { status: 204 })),
    );
    renderExploreChat();
    const user = await summarizeTo("original brief");
    await screen.findByTestId("dispatch-proposal-confirm");
    server.use(
      http.post(`${BASE}/${SID}/propose`, () => HttpResponse.json(readyProposal({ title: "fresh-title" }))),
    );
    await user.click(screen.getByTestId("summary-source-toggle"));
    const textarea = screen.getByTestId("summary-textarea");
    await user.clear(textarea);
    await user.type(textarea, "edited brief");
    await user.tab();
    await waitFor(() => expect(screen.getByTestId("summary-textarea")).toHaveValue("edited brief"));
    await screen.findByTestId("dispatch-proposal-repropose");
    expect(screen.queryByTestId("dispatch-proposal-confirm")).toBeNull();
    await user.click(screen.getByTestId("dispatch-proposal-repropose"));
    await screen.findByTestId("dispatch-proposal-confirm");
    await user.click(screen.getByTestId("dispatch-proposal-confirm"));
    await screen.findByTestId("home");
    expect(dispatched).toMatchObject({ title: "fresh-title", description: "edited brief" });
  });

  it("unmounting during an outstanding prefetch triggers a best-effort interrupt against the explore-chat session", async () => {
    let interruptCalls = 0;
    server.use(
      http.post(`${BASE}/${SID}/propose`, () => new Promise(() => undefined)),
      http.post(`${BASE}/${SID}/interrupt`, () => {
        interruptCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { unmount } = renderExploreChat();
    await summarizeTo("Scope: the whole system");
    unmount();
    await waitFor(() => expect(interruptCalls).toBeGreaterThanOrEqual(1));
  });

  it("need_more_info from the auto-triggered propose renders inline on the Summary tab, with no click and no extra propose call", async () => {
    let proposeCalls = 0;
    server.use(
      http.post(`${BASE}/${SID}/propose`, () => {
        proposeCalls += 1;
        return HttpResponse.json({ status: "need_more_info", questions: ["Which surface?"] });
      }),
    );
    renderExploreChat();
    await summarizeTo("Scope: the whole system");
    await waitFor(() => expect(proposeCalls).toBe(1));
    await flushMicrotasks();
    expect(screen.getByTestId("answer-0")).toBeInTheDocument();
    expect(proposeCalls).toBe(1);
  });
});
