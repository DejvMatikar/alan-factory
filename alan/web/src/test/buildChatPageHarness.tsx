import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect } from "vitest";

import { server } from "./server";
import BuildChat from "../features/chat/pages/BuildChat";

export const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
export const BUILD_CHAT_PATH = "/api/build-chat/sessions";

export function sessionResp(overrides = {}) {
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

export function readyProposal(counterCase: object, overrides: object = {}) {
  return {
    status: "ready",
    proposal: {
      team: "implementation_plan",
      rationale: "Build brief ready to dispatch to implementation planning.",
      instructions: null,
      scope_audit: [],
      title: "t",
      acceptance_clauses: [],
      counter_case: counterCase,
      ...overrides,
    },
    collision: { state: "green", sentence: "No overlapping work found.", match: null },
  };
}

export function renderBuildChat(initialPath = "/build") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/build" element={<BuildChat />} />
        <Route path="/" element={<div data-testid="home">home</div>} />
        <Route
          path="/workspaces/:taskId/prep-chat"
          element={<div data-testid="prep-chat-page">prep-chat</div>}
        />
        <Route path="/workspaces/:taskId" element={<div data-testid="workspace-page">ws</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

export async function summarizeTo(content: string) {
  server.use(
    http.post(`${BUILD_CHAT_PATH}/${SID}/summarize`, () =>
      HttpResponse.json({ content, updated_at: "2026-05-01T01:00:00Z" }),
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

export const DEFAULT_COUNTER_CASE = { against: "n/a", cheapest_80: "n/a", cost_estimate: "n/a" };

export function defaultReadyProposal() {
  return {
    status: "ready",
    proposal: {
      team: "implementation_plan",
      rationale: "default prefetch stub",
      instructions: null,
      scope_audit: [],
      title: "default-title",
      acceptance_clauses: [],
      counter_case: DEFAULT_COUNTER_CASE,
    },
    collision: { state: "green", sentence: "No overlapping work found.", match: null },
  };
}

export function installBuildChatPageSetup(): void {
  beforeEach(() => {
    localStorage.clear();
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())),
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => HttpResponse.json(defaultReadyProposal())),
    );
  });
}
