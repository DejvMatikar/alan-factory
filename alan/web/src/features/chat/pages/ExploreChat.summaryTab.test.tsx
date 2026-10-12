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

describe("ExploreChat Summary tab — inline dispatch proposal panel", () => {
  beforeEach(() => {
    localStorage.clear();
    server.use(http.get(`${BASE}/${SID}`, () => HttpResponse.json(sessionResp())));
  });

  it("answers open questions inline on the Summary tab, with no tab switch, then dispatches the re-proposed ready proposal", async () => {
    let proposeCalls = 0;
    let created: { title: string; description: string } | null = null;
    server.use(
      http.post(`${BASE}/${SID}/propose`, () => {
        proposeCalls += 1;
        if (proposeCalls === 1) {
          return HttpResponse.json({
            status: "need_more_info",
            questions: ["Which surface should this cover?"],
          });
        }
        return HttpResponse.json(readyProposal({ title: "answered-title" }));
      }),
      http.put(`${BASE}/${SID}/brief`, async ({ request }) => {
        const body = (await request.json()) as { content: string };
        return HttpResponse.json({ content: body.content, updated_at: "2026-06-01T02:00:00Z" });
      }),
      http.post("/api/workspaces", async ({ request }) => {
        created = (await request.json()) as { title: string; description: string };
        return HttpResponse.json({ task_id: "explore-task", root_task_ids: ["explore-task"] });
      }),
      http.delete(/\/api\/explore-chat\/sessions\/[^/]+$/, () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    renderExploreChat();
    const user = await summarizeTo("Scope: the whole system");
    await waitFor(() => expect(screen.getByTestId("answer-0")).toBeInTheDocument());
    expect(screen.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByTestId("tab-questions")).not.toBeInTheDocument();
    expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent(
      "More information is needed before dispatching.",
    );

    await user.type(screen.getByTestId("answer-0"), "The home screen only");
    await user.click(screen.getByTestId("dispatch-explore-questions"));

    await screen.findByTestId("dispatch-proposal-confirm");
    expect(screen.queryByTestId("dispatch-explore-questions")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("dispatch-proposal-confirm"));
    expect(await screen.findByTestId("home")).toBeInTheDocument();

    expect(created).not.toBeNull();
    const payload = created as unknown as { title: string; description: string };
    expect(payload.title).toBe("answered-title");
    expect(payload.description).toContain("## Clarifications");
    expect(payload.description).toContain("Which surface should this cover?");
    expect(payload.description).toContain("The home screen only");
  });

  it("shows the pending state immediately after Summarize, auto-triggered with no click needed", async () => {
    let resolvePropose!: (v: Response) => void;
    server.use(
      http.post(`${BASE}/${SID}/propose`, () =>
        new Promise<Response>((res) => {
          resolvePropose = res;
        }),
      ),
    );
    renderExploreChat();
    await summarizeTo("Scope: the whole system");

    expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent("Preparing…");
    expect(screen.getByTestId("proposal-panel-pending")).toHaveTextContent("Preparing…");
    const button = screen.getByTestId("dispatch-explore");
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Preparing…");

    resolvePropose(HttpResponse.json(readyProposal()));
    await screen.findByTestId("dispatch-proposal-confirm");
  });

  it("shows the pending state again while re-proposing after answering questions", async () => {
    let proposeCalls = 0;
    let resolveSecondPropose!: (v: Response) => void;
    server.use(
      http.post(`${BASE}/${SID}/propose`, () => {
        proposeCalls += 1;
        if (proposeCalls === 1) {
          return HttpResponse.json({ status: "need_more_info", questions: ["Which surface?"] });
        }
        return new Promise<Response>((res) => {
          resolveSecondPropose = res;
        });
      }),
      http.put(`${BASE}/${SID}/brief`, async ({ request }) => {
        const body = (await request.json()) as { content: string };
        return HttpResponse.json({ content: body.content, updated_at: "2026-06-01T02:00:00Z" });
      }),
    );
    renderExploreChat();
    const user = await summarizeTo("Scope: the whole system");
    await waitFor(() => expect(screen.getByTestId("answer-0")).toBeInTheDocument());

    await user.type(screen.getByTestId("answer-0"), "The home screen only");
    await user.click(screen.getByTestId("dispatch-explore-questions"));

    await waitFor(() => expect(screen.getByTestId("dispatch-explore-questions")).toBeDisabled());
    expect(screen.getByTestId("dispatch-explore-questions")).toHaveTextContent("Dispatching…");
    expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent(
      "More information is needed before dispatching.",
    );

    resolveSecondPropose(HttpResponse.json(readyProposal({ title: "answered-title" })));
    await screen.findByTestId("dispatch-proposal-confirm");
  });

  it("renders a propose failure inline as an alert, and retries via the dispatch-explore button", async () => {
    server.use(
      http.post(`${BASE}/${SID}/propose`, () =>
        HttpResponse.json({ detail: "propose failed" }, { status: 502 }),
      ),
    );
    renderExploreChat();
    const user = await summarizeTo("Scope: the whole system");

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("502"));
    expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent(
      "Could not prepare the dispatch proposal.",
    );
    const retryButton = screen.getByTestId("dispatch-explore");
    expect(retryButton).not.toBeDisabled();
    expect(retryButton).toHaveTextContent("Dispatch consultant");

    server.use(
      http.post(`${BASE}/${SID}/propose`, () => HttpResponse.json(readyProposal())),
    );
    await user.click(retryButton);
    await screen.findByTestId("dispatch-proposal-confirm");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("editing the brief after a ready proposal settles goes stale, and reproposing + dispatching sends the fresh (not stale) title/description", async () => {
    let dispatched: Record<string, unknown> | null = null;
    server.use(
      http.put(`${BASE}/${SID}/brief`, async ({ request }) => {
        const body = (await request.json()) as { content: string };
        return HttpResponse.json({ content: body.content, updated_at: "2026-06-01T02:00:00Z" });
      }),
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
    expect(screen.getByTestId("dispatch-proposal-title")).toHaveTextContent("stale-title");

    server.use(
      http.post(`${BASE}/${SID}/propose`, () => HttpResponse.json(readyProposal({ title: "fresh-title" }))),
    );
    await user.click(screen.getByTestId("summary-source-toggle"));
    const textarea = screen.getByTestId("summary-textarea");
    await user.clear(textarea);
    await user.type(textarea, "edited brief");
    await user.tab();
    await waitFor(() => expect(screen.getByTestId("summary-textarea")).toHaveValue("edited brief"));
    await flushMicrotasks();

    expect(screen.queryByTestId("dispatch-proposal-confirm")).not.toBeInTheDocument();
    const reproposeButton = screen.getByTestId("dispatch-proposal-repropose");
    expect(reproposeButton).toBeInTheDocument();
    expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent(
      "Brief changed since this proposal — re-propose to see the refreshed one.",
    );

    await user.click(reproposeButton);
    await waitFor(() => expect(screen.getByTestId("dispatch-proposal-confirm")).toBeInTheDocument());
    expect(screen.queryByTestId("dispatch-proposal-repropose")).not.toBeInTheDocument();
    expect(screen.getByTestId("dispatch-proposal-title")).toHaveTextContent("fresh-title");

    await user.click(screen.getByTestId("dispatch-proposal-confirm"));
    await screen.findByTestId("home");
    expect(dispatched).toMatchObject({ title: "fresh-title", description: "edited brief" });
  });

  it("forwards the optional Instructions field into the create-and-dispatch call", async () => {
    let dispatched: Record<string, unknown> | null = null;
    server.use(
      http.post(`${BASE}/${SID}/propose`, () => HttpResponse.json(readyProposal())),
      http.post("/api/workspaces", async ({ request }) => {
        dispatched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] });
      }),
      http.delete(/\/api\/explore-chat\/sessions\/[^/]+$/, () => new HttpResponse(null, { status: 204 })),
    );
    renderExploreChat();
    const user = await summarizeTo("Scope: the whole system");
    await screen.findByTestId("dispatch-proposal-confirm");

    expect(screen.queryByTestId("proposal-panel-instructions")).not.toBeVisible();
    await user.click(screen.getByText("Instructions (optional)"));
    await user.type(screen.getByTestId("proposal-panel-instructions"), "focus on the login flow");
    await user.click(screen.getByTestId("dispatch-proposal-confirm"));

    await screen.findByTestId("home");
    expect(dispatched).toMatchObject({ instructions: "focus on the login flow" });
  });
});
