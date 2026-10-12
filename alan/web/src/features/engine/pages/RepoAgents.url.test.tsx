import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { catalogueHandler, makeSampleCatalogue } from "../../../test/agentFixtures";
import { server } from "../../../test/server";
import RepoAgents from "./RepoAgents";

const IMPLEMENTER = "team_role:implementation/implementer";
const JUDGE = "team_role:verification/judge";
const CHAT = "chat:build";

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>;
}

function HistoryControls() {
  const navigate = useNavigate();
  return (
    <>
      <button
        type="button"
        data-testid="history-push-judge"
        onClick={() => {
          void navigate("/repos/acme/widget/agents?q=judge");
        }}
      >
        push
      </button>
      <button
        type="button"
        data-testid="history-back"
        onClick={() => {
          void navigate(-1);
        }}
      >
        back
      </button>
    </>
  );
}

function renderAt(search: string) {
  server.use(catalogueHandler("acme", "widget", makeSampleCatalogue()));
  return render(
    <MemoryRouter initialEntries={[`/repos/acme/widget/agents${search}`]}>
      <LocationProbe />
      <HistoryControls />
      <Routes>
        <Route path="/repos/:owner/:repo/agents" element={<RepoAgents />} />
      </Routes>
    </MemoryRouter>,
  );
}

function currentParams(): URLSearchParams {
  const text = screen.getByTestId("location").textContent ?? "";
  return new URLSearchParams(text.slice(text.indexOf("?")));
}

describe("RepoAgents — URL state", () => {
  it("restores the search term, every filter and the expanded agent from the URL", async () => {
    renderAt(
      `?q=write&model=sonnet&effort=high&kind=team_role&agent=${encodeURIComponent(IMPLEMENTER)}`,
    );
    expect(await screen.findByTestId(`agent-${IMPLEMENTER}-body`)).toBeInTheDocument();
    expect(screen.getByTestId("agent-search-input")).toHaveValue("write");
    expect(screen.getByTestId("agent-filter-model")).toHaveValue("sonnet");
    expect(screen.getByTestId("agent-filter-effort")).toHaveValue("high");
    expect(screen.getByTestId("agent-filter-kind")).toHaveValue("team_role");
    expect(screen.getByTestId("agent-search-count")).toHaveTextContent("Showing 1 of 3 agents");
    expect(screen.getByTestId(`agent-row-${IMPLEMENTER}`)).toHaveAttribute("open");
  });

  it("writes the search term to the URL as q and drops it when emptied", async () => {
    const user = userEvent.setup();
    renderAt("");
    await screen.findByTestId("repo-agents-list");
    await user.type(screen.getByTestId("agent-search-input"), "ab c");
    await waitFor(() => {
      expect(currentParams().get("q")).toBe("ab c");
    });
    await user.clear(screen.getByTestId("agent-search-input"));
    await waitFor(() => {
      expect(currentParams().has("q")).toBe(false);
    });
  });

  it("writes each filter to the URL under model, effort and kind and drops it on All", async () => {
    const user = userEvent.setup();
    renderAt("");
    await screen.findByTestId("repo-agents-list");
    await user.selectOptions(screen.getByTestId("agent-filter-model"), "opus");
    await user.selectOptions(screen.getByTestId("agent-filter-effort"), "CLI default");
    await user.selectOptions(screen.getByTestId("agent-filter-kind"), "team_role");
    await waitFor(() => {
      expect(Object.fromEntries(currentParams())).toEqual({
        model: "opus",
        effort: "CLI default",
        kind: "team_role",
      });
    });
    await user.selectOptions(screen.getByTestId("agent-filter-model"), "All models");
    await waitFor(() => {
      expect(currentParams().has("model")).toBe(false);
    });
  });

  it("writes the expanded agent to the URL as agent and removes it when collapsed", async () => {
    const user = userEvent.setup();
    renderAt("");
    await screen.findByTestId("repo-agents-list");
    await user.click(screen.getByTestId(`agent-row-${CHAT}-summary`));
    await waitFor(() => {
      expect(currentParams().get("agent")).toBe(CHAT);
    });
    await user.click(screen.getByTestId(`agent-row-${CHAT}-summary`));
    await waitFor(() => {
      expect(currentParams().has("agent")).toBe(false);
    });
  });

  it("moves the agent key when another agent is opened", async () => {
    const user = userEvent.setup();
    renderAt(`?agent=${encodeURIComponent(IMPLEMENTER)}`);
    await screen.findByTestId(`agent-${IMPLEMENTER}-body`);
    await user.click(screen.getByTestId(`agent-row-${JUDGE}-summary`));
    await waitFor(() => {
      expect(currentParams().get("agent")).toBe(JUDGE);
    });
  });

  it("clears every key at once from the no-match state", async () => {
    const user = userEvent.setup();
    renderAt(`?q=zzz&model=opus&agent=${encodeURIComponent(JUDGE)}`);
    await screen.findByTestId("repo-agents-no-match");
    await user.click(screen.getByTestId("repo-agents-clear"));
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent(/^\/repos\/acme\/widget\/agents$/);
    });
  });

  it("ignores an unknown kind in the URL instead of filtering everything out", async () => {
    renderAt("?kind=bogus");
    await screen.findByTestId("repo-agents-list");
    expect(screen.getByTestId("agent-filter-kind")).toHaveValue("");
    expect(screen.getByTestId("agent-search-count")).toHaveTextContent("Showing 3 of 3 agents");
  });

  it("follows the q parameter when history navigation changes the URL under a mounted page", async () => {
    const user = userEvent.setup();
    renderAt("?q=write");
    await screen.findByTestId("repo-agents-list");
    expect(screen.getByTestId("agent-search-count")).toHaveTextContent("Showing 2 of 3 agents");
    await user.click(screen.getByTestId("history-push-judge"));
    await waitFor(() => {
      expect(screen.getByTestId("agent-search-input")).toHaveValue("judge");
    });
    expect(screen.getByTestId("agent-search-count")).toHaveTextContent("Showing 1 of 3 agents");
    await user.click(screen.getByTestId("history-back"));
    await waitFor(() => {
      expect(screen.getByTestId("agent-search-input")).toHaveValue("write");
    });
    expect(screen.getByTestId("agent-search-count")).toHaveTextContent("Showing 2 of 3 agents");
  });

  it("keeps what the reader types while the URL catches up with it", async () => {
    const user = userEvent.setup();
    renderAt("");
    await screen.findByTestId("repo-agents-list");
    await user.type(screen.getByTestId("agent-search-input"), "judge");
    expect(screen.getByTestId("agent-search-input")).toHaveValue("judge");
    await waitFor(() => {
      expect(currentParams().get("q")).toBe("judge");
    });
  });

  it("treats empty q, model and effort values as absent", async () => {
    renderAt("?q=&model=&effort=");
    await screen.findByTestId("repo-agents-list");
    expect(screen.getByTestId("agent-search-count")).toHaveTextContent("Showing 3 of 3 agents");
  });
});
