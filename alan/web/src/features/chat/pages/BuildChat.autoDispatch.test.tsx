import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import BuildChat from "./BuildChat";

const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const BUILD_CHAT_PATH = "/api/build-chat/sessions";

const COUNTER_CASE = { against: "a", cheapest_80: "b", cost_estimate: "c" };

function sessionResp(overrides = {}) {
  return {
    session_id: SID,
    created_at: "2026-05-01T00:00:00Z",
    messages: [],
    in_flight_turn: null,
    brief: null,
    repo_provision: "not_needed",
    ...overrides,
  };
}

function readyProposal(overrides: object = {}) {
  return {
    status: "ready",
    proposal: {
      team: "implementation_plan",
      rationale: "ready to dispatch",
      instructions: null,
      scope_audit: [],
      title: "auto-dispatch-title",
      acceptance_clauses: [],
      counter_case: COUNTER_CASE,
      ...overrides,
    },
    collision: { state: "green", sentence: "No overlapping work found.", match: null },
  };
}

function renderBuildChat() {
  return render(
    <MemoryRouter initialEntries={["/build"]}>
      <Routes>
        <Route path="/build" element={<BuildChat />} />
        <Route path="/" element={<div data-testid="home">home</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function greenCollision() {
  server.use(
    http.post("/api/collision-check", () =>
      HttpResponse.json({ state: "green", sentence: "No overlapping work found.", match: null }),
    ),
  );
}

async function waitForAutoDispatchReady() {
  await waitFor(() => expect(screen.getByTestId("auto-dispatch-button")).not.toBeDisabled());
}

describe("BuildChat — one-click auto dispatch", () => {
  beforeEach(() => {
    localStorage.clear();
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())),
      http.post(`${BUILD_CHAT_PATH}/${SID}/summarize`, () =>
        HttpResponse.json({ content: "the brief", updated_at: "2026-05-01T01:00:00Z" }),
      ),
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => HttpResponse.json(readyProposal())),
      http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () => new HttpResponse(null, { status: 204 })),
    );
    greenCollision();
  });

  it("runs the full chain and dispatches a workspace, producing the same title/counter-case as a manual confirm", async () => {
    let dispatched: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        dispatched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ task_id: "auto-dispatch-title", root_task_ids: ["auto-dispatch-title"] });
      }),
    );
    const user = userEvent.setup();
    renderBuildChat();
    await waitForAutoDispatchReady();
    await user.click(screen.getByTestId("auto-dispatch-button"));
    expect(await screen.findByTestId("home")).toBeInTheDocument();
    expect(dispatched).toMatchObject({
      team: "implementation_plan",
      title: "auto-dispatch-title",
      counter_case: COUNTER_CASE,
    });
  });

  it("stops without dispatching when the overlap check is not clear", async () => {
    server.use(
      http.post("/api/collision-check", () =>
        HttpResponse.json({ state: "red", sentence: "overlap!", match: null }),
      ),
    );
    let workspaceCalls = 0;
    server.use(http.post("/api/workspaces", () => { workspaceCalls += 1; return HttpResponse.json({}); }));
    const user = userEvent.setup();
    renderBuildChat();
    await waitForAutoDispatchReady();
    await user.click(screen.getByTestId("auto-dispatch-button"));
    await waitFor(() =>
      expect(screen.getByTestId("auto-dispatch-status")).toHaveTextContent(/overlap check/),
    );
    expect(workspaceCalls).toBe(0);
    expect(screen.queryByTestId("home")).not.toBeInTheDocument();
  });

  it("stops without dispatching when the proposal still has open questions", async () => {
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
        HttpResponse.json({ status: "need_more_info", questions: ["Which database?"] }),
      ),
    );
    let workspaceCalls = 0;
    server.use(http.post("/api/workspaces", () => { workspaceCalls += 1; return HttpResponse.json({}); }));
    const user = userEvent.setup();
    renderBuildChat();
    await waitForAutoDispatchReady();
    await user.click(screen.getByTestId("auto-dispatch-button"));
    await waitFor(() =>
      expect(screen.getByTestId("auto-dispatch-status")).toHaveTextContent(/open questions/),
    );
    expect(workspaceCalls).toBe(0);
  });

  it("stops without dispatching when summarizing fails", async () => {
    server.use(
      http.post(`${BUILD_CHAT_PATH}/${SID}/summarize`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const user = userEvent.setup();
    renderBuildChat();
    await waitForAutoDispatchReady();
    await user.click(screen.getByTestId("auto-dispatch-button"));
    await waitFor(() =>
      expect(screen.getByTestId("auto-dispatch-status")).toHaveTextContent(/could not be summarized/),
    );
  });

  it("can be abandoned with the explicit Stop control before it dispatches", async () => {
    let resolvePropose!: (v: Response) => void;
    server.use(
      http.post(
        `${BUILD_CHAT_PATH}/${SID}/propose`,
        () => new Promise<Response>((resolve) => { resolvePropose = resolve; }),
      ),
    );
    let workspaceCalls = 0;
    server.use(http.post("/api/workspaces", () => { workspaceCalls += 1; return HttpResponse.json({}); }));
    const user = userEvent.setup();
    renderBuildChat();
    await waitForAutoDispatchReady();
    await user.click(screen.getByTestId("auto-dispatch-button"));
    await screen.findByTestId("auto-dispatch-stop");
    await user.click(screen.getByTestId("auto-dispatch-stop"));
    expect(screen.getByTestId("auto-dispatch-status")).toHaveTextContent(/cancelled/);
    resolvePropose(HttpResponse.json(readyProposal()));
    await new Promise((r) => setTimeout(r, 10));
    expect(workspaceCalls).toBe(0);
  });

  it("does not cancel an in-flight run when Enter is pressed on an empty draft", async () => {
    let resolvePropose!: (v: Response) => void;
    server.use(
      http.post(
        `${BUILD_CHAT_PATH}/${SID}/propose`,
        () => new Promise<Response>((resolve) => { resolvePropose = resolve; }),
      ),
    );
    let dispatched: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/workspaces", async ({ request }) => {
        dispatched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ task_id: "auto-dispatch-title", root_task_ids: ["auto-dispatch-title"] });
      }),
    );
    const user = userEvent.setup();
    renderBuildChat();
    await waitForAutoDispatchReady();
    await user.click(screen.getByTestId("auto-dispatch-button"));
    await screen.findByTestId("auto-dispatch-stop");

    await user.click(screen.getByTestId("tab-chat"));
    const composer = screen.getByLabelText("Message");
    fireEvent.keyDown(composer, { key: "Enter", ctrlKey: true });
    expect(screen.getByTestId("auto-dispatch-stop")).toBeInTheDocument();

    resolvePropose(HttpResponse.json(readyProposal()));
    expect(await screen.findByTestId("home")).toBeInTheDocument();
    expect(dispatched).toMatchObject({ team: "implementation_plan" });
  });

  it("stops rather than dispatching a proposal generated against different text when the summary is edited mid-run", async () => {
    let resolvePropose!: (v: Response) => void;
    server.use(
      http.post(
        `${BUILD_CHAT_PATH}/${SID}/propose`,
        () => new Promise<Response>((resolve) => { resolvePropose = resolve; }),
      ),
    );
    let workspaceCalls = 0;
    server.use(http.post("/api/workspaces", () => { workspaceCalls += 1; return HttpResponse.json({}); }));
    const user = userEvent.setup();
    renderBuildChat();
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
