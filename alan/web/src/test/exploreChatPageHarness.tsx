import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect } from "vitest";

import { server } from "./server";
import ExploreChat from "../features/chat/pages/ExploreChat";

export const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
export const BASE = "/api/explore-chat/sessions";

export function sessionResp(overrides = {}) {
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

export const COUNTER_CASE = {
  against: "the scope is still fuzzy",
  cheapest_80: "scope the review to one screen first",
  cost_estimate: "insufficient data",
};

export function readyProposal(counterCase: object, overrides: object = {}) {
  return {
    status: "ready",
    proposal: {
      team: "brainstorming",
      rationale: "The scope is clear enough to hand off to brainstorming.",
      instructions: null,
      scope_audit: [],
      title: "explore-thing",
      acceptance_clauses: [],
      counter_case: counterCase,
      ...overrides,
    },
    collision: { state: "green", sentence: "No overlapping work found.", match: null },
  };
}

export function renderExploreChat(consultant = "brainstorming", search = "") {
  return render(
    <MemoryRouter initialEntries={[`/explore/${consultant}${search}`]}>
      <Routes>
        <Route path="/explore/:consultant" element={<ExploreChat />} />
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

export function installExploreChatPageSetup(): void {
  beforeEach(() => {
    localStorage.clear();
    server.use(
      http.get(`${BASE}/${SID}`, () => HttpResponse.json(sessionResp())),
      http.post(`${BASE}/${SID}/propose`, () => HttpResponse.json(readyProposal(COUNTER_CASE))),
    );
  });
}
