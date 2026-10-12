import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { makeSummary, summaryHandler } from "../../../test/agentFixtures";
import { server } from "../../../test/server";
import AgentCatalogueTile from "./AgentCatalogueTile";

function renderTile() {
  return render(
    <MemoryRouter>
      <AgentCatalogueTile engineKey="acme/widget" />
    </MemoryRouter>,
  );
}

describe("AgentCatalogueTile", () => {
  it("links to the repository's agent catalogue even while loading", () => {
    server.use(http.get("/api/engines/acme/widget/agents/summary", () => new Promise<Response>(() => undefined)));
    renderTile();
    expect(screen.getByTestId("agent-catalogue-tile-loading")).toBeInTheDocument();
    expect(screen.getByTestId("agent-catalogue-tile-link")).toHaveAttribute("href", "/repos/acme/widget/agents");
  });

  it("summarises how many agents use which models", async () => {
    server.use(summaryHandler("acme", "widget", makeSummary()));
    renderTile();
    expect(await screen.findByTestId("agent-catalogue-tile-models")).toHaveTextContent(
      "3 agents: 1 on opus, 2 on sonnet",
    );
  });

  it("flags warnings, notes and errors in the summary", async () => {
    server.use(summaryHandler("acme", "widget", makeSummary({ warnings: 2, infos: 1, errors: 1 })));
    renderTile();
    expect(await screen.findByTestId("agent-catalogue-tile-flags")).toHaveTextContent(
      "Flagged: 2 warnings, 1 note, 1 agent could not be resolved",
    );
  });

  it("counts only the agents that resolve and says how many could not", async () => {
    server.use(
      summaryHandler(
        "acme",
        "widget",
        makeSummary({ total: 3, errors: 2, warnings: 0, by_model: [{ name: "sonnet", count: 1 }] }),
      ),
    );
    renderTile();
    expect(await screen.findByTestId("agent-catalogue-tile-models")).toHaveTextContent(/^1 agent: 1 on sonnet$/);
    expect(screen.getByTestId("agent-catalogue-tile-flags")).toHaveTextContent(
      "Flagged: 2 agents could not be resolved",
    );
  });

  it("says nothing about flags when there are none and pluralises one agent", async () => {
    server.use(
      summaryHandler(
        "acme",
        "widget",
        makeSummary({ total: 1, by_model: [{ name: "sonnet", count: 1 }], warnings: 0 }),
      ),
    );
    renderTile();
    expect(await screen.findByTestId("agent-catalogue-tile-models")).toHaveTextContent("1 agent: 1 on sonnet");
    expect(screen.queryByTestId("agent-catalogue-tile-flags")).not.toBeInTheDocument();
  });

  it("shows only the total when no model counts are reported", async () => {
    server.use(summaryHandler("acme", "widget", makeSummary({ total: 0, by_model: [], warnings: 0 })));
    renderTile();
    expect(await screen.findByTestId("agent-catalogue-tile-models")).toHaveTextContent(/^0 agents$/);
  });

  it("shows the failure with a retry that recovers", async () => {
    server.use(
      http.get("/api/engines/acme/widget/agents/summary", () =>
        HttpResponse.json({ detail: "engine not found" }, { status: 404 }),
      ),
    );
    const user = userEvent.setup();
    renderTile();
    expect(await screen.findByTestId("agent-catalogue-tile-error")).toBeInTheDocument();
    server.use(summaryHandler("acme", "widget", makeSummary()));
    await user.click(screen.getByTestId("agent-catalogue-tile-retry"));
    expect(await screen.findByTestId("agent-catalogue-tile-models")).toBeInTheDocument();
  });
});
