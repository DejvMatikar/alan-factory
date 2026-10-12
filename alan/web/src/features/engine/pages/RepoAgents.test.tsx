import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { AgentCatalogue } from "../../../api";
import { catalogueHandler, makeSampleCatalogue } from "../../../test/agentFixtures";
import { server } from "../../../test/server";
import RepoAgents from "./RepoAgents";

const IMPLEMENTER = "team_role:implementation/implementer";
const JUDGE = "team_role:verification/judge";
const CHAT = "chat:build";

function renderAt(search = "") {
  return render(
    <MemoryRouter initialEntries={[`/repos/acme/widget/agents${search}`]}>
      <Routes>
        <Route path="/repos/:owner/:repo/agents" element={<RepoAgents />} />
        <Route path="/repos/:owner/:repo/config" element={<p>config page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

function rowIds(): string[] {
  return screen
    .queryAllByTestId(/^agent-row-[^/]+\/[^-]+$|^agent-row-chat:build$/)
    .filter((el) => el.tagName === "DETAILS")
    .map((el) => el.getAttribute("data-testid")!.replace("agent-row-", ""));
}

describe("RepoAgents — loading, error, params", () => {
  it("stays in a loading state when the route provides no owner or repo", () => {
    render(
      <MemoryRouter initialEntries={["/agents"]}>
        <Routes>
          <Route path="/agents" element={<RepoAgents />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("repo-agents-loading")).toBeInTheDocument();
  });

  it("shows a loading state before the catalogue resolves", () => {
    server.use(http.get("/api/engines/acme/widget/agents", () => new Promise<Response>(() => undefined)));
    renderAt();
    expect(screen.getByTestId("repo-agents-loading")).toBeInTheDocument();
    expect(screen.getByTestId("repo-agents-identity")).toHaveTextContent("acme/widget");
  });

  it("shows an error with a retry that recovers", async () => {
    server.use(
      http.get("/api/engines/acme/widget/agents", () =>
        HttpResponse.json({ detail: "engine not found" }, { status: 404 }),
      ),
    );
    const user = userEvent.setup();
    renderAt();
    const error = await screen.findByTestId("repo-agents-error");
    expect(error).toHaveAttribute("role", "alert");
    server.use(catalogueHandler("acme", "widget", makeSampleCatalogue()));
    await user.click(screen.getByTestId("repo-agents-retry"));
    expect(await screen.findByTestId("repo-agents-list")).toBeInTheDocument();
  });

  it("links back to the engine configuration page", () => {
    server.use(catalogueHandler("acme", "widget", makeSampleCatalogue()));
    renderAt();
    expect(screen.getByTestId("repo-agents-back")).toHaveAttribute("href", "/repos/acme/widget/config");
  });
});

describe("RepoAgents — notes and flags", () => {
  it("states that every prompt is appended to Claude Code's own and names what is inherited", async () => {
    server.use(catalogueHandler("acme", "widget", makeSampleCatalogue()));
    renderAt();
    expect(await screen.findByTestId("repo-agents-appended-note")).toHaveTextContent(
      "appended to Claude Code's own built-in system prompt",
    );
    const inherited = screen.getByTestId("repo-agents-inherited");
    expect(inherited).toHaveTextContent("User-level settings");
    expect(inherited).toHaveTextContent("Repository guidance file");
    expect(screen.getByTestId("repo-agents-notes")).toHaveTextContent("contents are not read or shown");
  });

  it("shows catalogue-wide flags", async () => {
    server.use(catalogueHandler("acme", "widget", makeSampleCatalogue()));
    renderAt();
    expect(await screen.findByTestId("repo-agents-flags-unread_stored_role")).toHaveTextContent(
      "remediator is read by no agent.",
    );
  });

  it("omits the flags section when the catalogue has none", async () => {
    server.use(catalogueHandler("acme", "widget", { ...makeSampleCatalogue(), flags: [] }));
    renderAt();
    await screen.findByTestId("repo-agents-list");
    expect(screen.queryByTestId("repo-agents-flags-section")).not.toBeInTheDocument();
  });

  it("tolerates a catalogue that omits the flags key", async () => {
    const withoutFlags: Partial<AgentCatalogue> = makeSampleCatalogue();
    delete withoutFlags.flags;
    server.use(catalogueHandler("acme", "widget", withoutFlags as AgentCatalogue));
    renderAt();
    await screen.findByTestId("repo-agents-list");
    expect(screen.queryByTestId("repo-agents-flags-section")).not.toBeInTheDocument();
  });
});

describe("RepoAgents — list, search and filters", () => {
  it("lists every agent and the total", async () => {
    server.use(catalogueHandler("acme", "widget", makeSampleCatalogue()));
    renderAt();
    await screen.findByTestId("repo-agents-list");
    expect(rowIds()).toEqual([IMPLEMENTER, JUDGE, CHAT]);
    expect(screen.getByTestId("agent-search-count")).toHaveTextContent("Showing 3 of 3 agents");
  });

  it("searches full prompt text, shows per-prompt counts and updates the live count", async () => {
    server.use(catalogueHandler("acme", "widget", makeSampleCatalogue()));
    const user = userEvent.setup();
    renderAt();
    await screen.findByTestId("repo-agents-list");
    await user.type(screen.getByTestId("agent-search-input"), "write");
    expect(rowIds()).toEqual([IMPLEMENTER, CHAT]);
    expect(screen.getByTestId(`agent-row-${IMPLEMENTER}-summary`)).toHaveTextContent("2 matches in prompt");
    expect(screen.getByTestId(`agent-row-${CHAT}-summary`)).toHaveTextContent("1 match in prompt");
    expect(screen.getByTestId("agent-search-count")).toHaveTextContent("Showing 2 of 3 agents");
  });

  it("searches names, models and efforts too", async () => {
    server.use(catalogueHandler("acme", "widget", makeSampleCatalogue()));
    const user = userEvent.setup();
    renderAt();
    await screen.findByTestId("repo-agents-list");
    await user.type(screen.getByTestId("agent-search-input"), "haiku");
    expect(rowIds()).toEqual([CHAT]);
  });

  it("filters by model, effort and kind", async () => {
    server.use(catalogueHandler("acme", "widget", makeSampleCatalogue()));
    const user = userEvent.setup();
    renderAt();
    await screen.findByTestId("repo-agents-list");
    await user.selectOptions(screen.getByTestId("agent-filter-model"), "opus");
    expect(rowIds()).toEqual([JUDGE]);
    await user.selectOptions(screen.getByTestId("agent-filter-model"), "All models");
    await user.selectOptions(screen.getByTestId("agent-filter-effort"), "CLI default");
    expect(rowIds()).toEqual([JUDGE, CHAT]);
    await user.selectOptions(screen.getByTestId("agent-filter-kind"), "chat");
    expect(rowIds()).toEqual([CHAT]);
  });

  it("shows a no-match state with a clear action that restores the full list", async () => {
    server.use(catalogueHandler("acme", "widget", makeSampleCatalogue()));
    const user = userEvent.setup();
    renderAt();
    await screen.findByTestId("repo-agents-list");
    await user.type(screen.getByTestId("agent-search-input"), "zzz-nothing");
    expect(screen.getByTestId("repo-agents-no-match")).toBeInTheDocument();
    expect(screen.queryByTestId("repo-agents-list")).not.toBeInTheDocument();
    expect(screen.getByTestId("agent-search-count")).toHaveTextContent("Showing 0 of 3 agents");
    await user.click(screen.getByTestId("repo-agents-clear"));
    expect(screen.getByTestId("agent-search-input")).toHaveValue("");
    expect(rowIds()).toEqual([IMPLEMENTER, JUDGE, CHAT]);
  });
});

describe("RepoAgents — expanding an agent", () => {
  it("opens one agent at a time and mounts its body only while open", async () => {
    server.use(catalogueHandler("acme", "widget", makeSampleCatalogue()));
    const user = userEvent.setup();
    renderAt();
    await screen.findByTestId("repo-agents-list");
    expect(screen.queryByTestId(`agent-${IMPLEMENTER}-body`)).not.toBeInTheDocument();
    await user.click(screen.getByTestId(`agent-row-${IMPLEMENTER}-summary`));
    expect(await screen.findByTestId(`agent-${IMPLEMENTER}-body`)).toBeInTheDocument();
    await user.click(screen.getByTestId(`agent-row-${JUDGE}-summary`));
    expect(await screen.findByTestId(`agent-${JUDGE}-body`)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByTestId(`agent-${IMPLEMENTER}-body`)).not.toBeInTheDocument();
    });
    await user.click(screen.getByTestId(`agent-row-${JUDGE}-summary`));
    await waitFor(() => {
      expect(screen.queryByTestId(`agent-${JUDGE}-body`)).not.toBeInTheDocument();
    });
  });

  it("opens and closes by keyboard alone", async () => {
    server.use(catalogueHandler("acme", "widget", makeSampleCatalogue()));
    const user = userEvent.setup();
    renderAt();
    await screen.findByTestId("repo-agents-list");
    screen.getByTestId(`agent-row-${CHAT}-summary`).focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByTestId(`agent-${CHAT}-body`)).toBeInTheDocument();
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(screen.queryByTestId(`agent-${CHAT}-body`)).not.toBeInTheDocument();
    });
  });

  it("highlights the active search term in the expanded prompt with navigation", async () => {
    server.use(catalogueHandler("acme", "widget", makeSampleCatalogue()));
    const user = userEvent.setup();
    renderAt();
    await screen.findByTestId("repo-agents-list");
    await user.type(screen.getByTestId("agent-search-input"), "write");
    await user.click(screen.getByTestId(`agent-row-${IMPLEMENTER}-summary`));
    const prompt = await screen.findByTestId(`agent-${IMPLEMENTER}-prompt`);
    expect(within(prompt).getAllByTestId(`agent-${IMPLEMENTER}-prompt-match`)).toHaveLength(2);
    await user.click(within(prompt).getByTestId(`agent-${IMPLEMENTER}-prompt-next`));
    expect(within(prompt).getByTestId(`agent-${IMPLEMENTER}-prompt-status`)).toHaveTextContent("Match 2 of 2");
  });
});
