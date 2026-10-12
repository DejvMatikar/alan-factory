import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import ExploreChat from "./ExploreChat";

const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const BASE = "/api/explore-chat/sessions";
const COUNTER_CASE = { against: "a", cheapest_80: "b", cost_estimate: "c" };

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

function readyProposal(overrides: object = {}) {
  return {
    status: "ready",
    proposal: {
      team: "brainstorming",
      rationale: "ready to dispatch",
      instructions: null,
      scope_audit: [],
      title: "auto-dispatch-explore",
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

async function waitForAutoDispatchReady() {
  await waitFor(() => expect(screen.getByTestId("auto-dispatch-button")).not.toBeDisabled());
}

describe("ExploreChat — one-click auto dispatch", () => {
  beforeEach(() => {
    localStorage.clear();
    server.use(
      http.get(`${BASE}/${SID}`, () => HttpResponse.json(sessionResp())),
      http.post(`${BASE}/${SID}/summarize`, () =>
        HttpResponse.json({ content: "the brief", updated_at: "2026-06-01T01:00:00Z" }),
      ),
      http.post(`${BASE}/${SID}/propose`, () => HttpResponse.json(readyProposal())),
      http.delete(/\/api\/explore-chat\/sessions\/[^/]+$/, () => new HttpResponse(null, { status: 204 })),
      http.post("/api/collision-check", () =>
        HttpResponse.json({ state: "green", sentence: "No overlapping work found.", match: null }),
      ),
    );
  });

  it("runs the full chain and dispatches a workspace, producing the same title/counter-case as a manual confirm", async () => {
    let dispatched: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        dispatched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ task_id: "auto-dispatch-explore", root_task_ids: ["auto-dispatch-explore"] });
      }),
    );
    const user = userEvent.setup();
    renderExploreChat();
    await waitForAutoDispatchReady();
    await user.click(screen.getByTestId("auto-dispatch-button"));
    expect(await screen.findByTestId("home")).toBeInTheDocument();
    expect(dispatched).toMatchObject({
      team: "brainstorming",
      title: "auto-dispatch-explore",
      counter_case: COUNTER_CASE,
    });
  });

  it("stops without dispatching when the overlap check is not clear", async () => {
    server.use(
      http.post("/api/collision-check", () =>
        HttpResponse.json({ state: "yellow", sentence: "similar work", match: null }),
      ),
    );
    let workspaceCalls = 0;
    server.use(http.post("/api/workspaces", () => { workspaceCalls += 1; return HttpResponse.json({}); }));
    const user = userEvent.setup();
    renderExploreChat();
    await waitForAutoDispatchReady();
    await user.click(screen.getByTestId("auto-dispatch-button"));
    await waitFor(() =>
      expect(screen.getByTestId("auto-dispatch-status")).toHaveTextContent(/overlap check/),
    );
    expect(workspaceCalls).toBe(0);
  });

  it("stops without dispatching when the proposal still has open questions", async () => {
    server.use(
      http.post(`${BASE}/${SID}/propose`, () =>
        HttpResponse.json({ status: "need_more_info", questions: ["Which surface?"] }),
      ),
    );
    let workspaceCalls = 0;
    server.use(http.post("/api/workspaces", () => { workspaceCalls += 1; return HttpResponse.json({}); }));
    const user = userEvent.setup();
    renderExploreChat();
    await waitForAutoDispatchReady();
    await user.click(screen.getByTestId("auto-dispatch-button"));
    await waitFor(() =>
      expect(screen.getByTestId("auto-dispatch-status")).toHaveTextContent(/open questions/),
    );
    expect(workspaceCalls).toBe(0);
  });

  it("can be abandoned with the explicit Stop control before it dispatches", async () => {
    let resolvePropose!: (v: Response) => void;
    server.use(
      http.post(
        `${BASE}/${SID}/propose`,
        () => new Promise<Response>((resolve) => { resolvePropose = resolve; }),
      ),
    );
    let workspaceCalls = 0;
    server.use(http.post("/api/workspaces", () => { workspaceCalls += 1; return HttpResponse.json({}); }));
    const user = userEvent.setup();
    renderExploreChat();
    await waitForAutoDispatchReady();
    await user.click(screen.getByTestId("auto-dispatch-button"));
    await screen.findByTestId("auto-dispatch-stop");
    await user.click(screen.getByTestId("auto-dispatch-stop"));
    expect(screen.getByTestId("auto-dispatch-status")).toHaveTextContent(/cancelled/);
    resolvePropose(HttpResponse.json(readyProposal()));
    await new Promise((r) => setTimeout(r, 10));
    expect(workspaceCalls).toBe(0);
  });

  it("stops rather than dispatching a proposal generated against different text when the summary is edited mid-run", async () => {
    let resolvePropose!: (v: Response) => void;
    server.use(
      http.post(
        `${BASE}/${SID}/propose`,
        () => new Promise<Response>((resolve) => { resolvePropose = resolve; }),
      ),
    );
    let workspaceCalls = 0;
    server.use(http.post("/api/workspaces", () => { workspaceCalls += 1; return HttpResponse.json({}); }));
    const user = userEvent.setup();
    renderExploreChat();
    await waitForAutoDispatchReady();
    await user.click(screen.getByTestId("auto-dispatch-button"));
    await waitFor(() =>
      expect(screen.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true"),
    );
    await user.click(await screen.findByTestId("summary-source-toggle"));
    const textarea = await screen.findByTestId("summary-textarea");
    await user.type(textarea, "!");
    await waitFor(() =>
      expect(screen.getByTestId("auto-dispatch-status")).toHaveTextContent(/summary changed/),
    );
    resolvePropose(HttpResponse.json(readyProposal()));
    await new Promise((r) => setTimeout(r, 10));
    expect(workspaceCalls).toBe(0);
  });
});
