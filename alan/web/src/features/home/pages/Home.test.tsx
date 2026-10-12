import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import type { EngineSummary } from "../../../api";
import { useEngines } from "../../../hooks/useEngines";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  gatedEngineListingHandler,
  LISTED_DEFAULT_KEY,
  makeConfiguredEngineSummaryFor,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
} from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import Home from "./Home";

const WIDGET_KEY = "acme/widget";

function RepoTarget() {
  const { owner, repo } = useParams<{ owner: string; repo: string }>();
  return <div data-testid="repo-target">{`${owner ?? ""}/${repo ?? ""}`}</div>;
}

function PathProbe() {
  const { pathname } = useLocation();
  return <div data-testid="path">{pathname}</div>;
}

function mockEngines(engines: readonly (string | EngineSummary)[], defaultKey?: string): void {
  server.use(
    engineListingHandler({
      engines: engines.map((engine) => (typeof engine === "string" ? makeEngineSummaryFor(engine) : engine)),
      defaultKey,
    }),
  );
}

function renderAt(initial = "/", extraRoutes: ReactNode = null) {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <PathProbe />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/repos/:owner/:repo" element={<RepoTarget />} />
        {extraRoutes}
      </Routes>
    </MemoryRouter>,
  );
}

function WorkspacePage() {
  const navigate = useNavigate();
  const { loaded } = useEngines();
  return (
    <button
      type="button"
      data-testid="leave-workspace"
      disabled={!loaded}
      onClick={() => {
        void navigate("/");
      }}
    >
      leave
    </button>
  );
}

beforeEach(() => {
  sessionStorage.clear();
});

describe("Home — blank slate", () => {
  it("renders the blank slate when engines are settled and no engine is registered", async () => {
    mockEngines([]);
    renderAt();
    await screen.findByTestId("home-blank-slate");
    expect(screen.queryByTestId("repo-target")).not.toBeInTheDocument();
  });

  it.each(DEFAULT_KEY_STATES)(
    "renders the blank slate when only an unconfigured listed default (no forge target) is registered (%s)",
    async (_state, defaultKey) => {
      mockEngines([defaultKey], defaultKey);
      renderAt();
      await screen.findByTestId("home-blank-slate");
      expect(screen.queryByTestId("repo-target")).not.toBeInTheDocument();
      expect(screen.getByTestId("path")).toHaveTextContent("/");
    },
  );

  it("renders nothing, neither the blank slate nor a repository, when the initial engines fetch fails and no repository is known", async () => {
    let requested = 0;
    server.use(
      http.get("/api/engines", () => {
        requested += 1;
        return HttpResponse.error();
      }),
    );
    renderAt();
    await waitFor(() => expect(requested).toBeGreaterThan(0));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByTestId("repo-target")).not.toBeInTheDocument();
    expect(screen.queryByTestId("home-blank-slate")).not.toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/");
  });
});

describe("Home — redirect", () => {
  it.each(DEFAULT_KEY_STATES)(
    "redirects to the listed default repository, listed first, once a non-default engine is registered (%s)",
    async (_state, defaultKey) => {
      mockEngines([WIDGET_KEY, defaultKey], defaultKey);
      renderAt();
      expect(await screen.findByTestId("repo-target")).toHaveTextContent(defaultKey);
      expect(screen.getByTestId("path")).toHaveTextContent(`/repos/${defaultKey}`);
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "redirects using the current cross-repo scope when one is still visible (%s)",
    async (_state, defaultKey) => {
      sessionStorage.setItem("repo-scope", WIDGET_KEY);
      mockEngines([defaultKey, WIDGET_KEY], defaultKey);
      renderAt();
      expect(await screen.findByTestId("repo-target")).toHaveTextContent(WIDGET_KEY);
    },
  );

  it("redirects to the listed default when the mirrored scope is the unregistered built-in key", async () => {
    sessionStorage.setItem("repo-scope", STUB_DEFAULT_ENGINE_KEY);
    mockEngines([LISTED_DEFAULT_KEY, WIDGET_KEY], LISTED_DEFAULT_KEY);
    renderAt();
    expect(await screen.findByTestId("repo-target")).toHaveTextContent(LISTED_DEFAULT_KEY);
  });

  it("redirects to the first registered repository when the list names no default", async () => {
    mockEngines([WIDGET_KEY]);
    renderAt();
    expect(await screen.findByTestId("repo-target")).toHaveTextContent(WIDGET_KEY);
    expect(screen.queryByTestId("home-blank-slate")).not.toBeInTheDocument();
  });

  it("redirects to the built-in key as an ordinary repository under a different listed default", async () => {
    mockEngines([STUB_DEFAULT_ENGINE_KEY], LISTED_DEFAULT_KEY);
    renderAt();
    expect(await screen.findByTestId("repo-target")).toHaveTextContent(STUB_DEFAULT_ENGINE_KEY);
    expect(screen.queryByTestId("home-blank-slate")).not.toBeInTheDocument();
  });

  it("puts the listed default ahead of an ordinary built-in repository in the redirect", async () => {
    mockEngines([STUB_DEFAULT_ENGINE_KEY, LISTED_DEFAULT_KEY], LISTED_DEFAULT_KEY);
    renderAt();
    expect(await screen.findByTestId("repo-target")).toHaveTextContent(LISTED_DEFAULT_KEY);
  });

  it("renders nothing while the engines load has not yet settled", async () => {
    let requested = false;
    server.use(
      http.get("/api/engines", () => {
        requested = true;
        return new Promise(() => undefined);
      }),
    );
    renderAt();
    await waitFor(() => expect(requested).toBe(true));
    expect(screen.queryByTestId("home-blank-slate")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-target")).not.toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/");
  });

  it("does not redirect before the listing loads, then redirects straight to the listed default", async () => {
    const gate: { resolve: (() => void) | null } = { resolve: null };
    server.use(
      http.get("/api/engines", async () => {
        await new Promise<void>((resolve) => {
          gate.resolve = resolve;
        });
        return HttpResponse.json({
          engines: [makeEngineSummaryFor(WIDGET_KEY), makeEngineSummaryFor(LISTED_DEFAULT_KEY)],
          default_engine_key: LISTED_DEFAULT_KEY,
        });
      }),
    );
    renderAt();
    await waitFor(() => expect(gate.resolve).not.toBeNull());
    expect(screen.queryByTestId("repo-target")).not.toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/");
    gate.resolve?.();
    expect(await screen.findByTestId("repo-target")).toHaveTextContent(LISTED_DEFAULT_KEY);
  });
});

describe("Home — single-repo copy", () => {
  it.each(DEFAULT_KEY_STATES)(
    "replaces the path with the lone configured default's dashboard and never shows the blank slate (%s)",
    async (_state, defaultKey) => {
      mockEngines([makeConfiguredEngineSummaryFor(defaultKey)], defaultKey);
      renderAt();
      expect(await screen.findByTestId("repo-target")).toHaveTextContent(defaultKey);
      expect(screen.getByTestId("path")).toHaveTextContent(`/repos/${defaultKey}`);
      expect(screen.queryByTestId("home-blank-slate")).not.toBeInTheDocument();
    },
  );

  it("shows neither the blank slate nor a redirect before the engine list settles, then lands on the dashboard", async () => {
    const { handler, release } = gatedEngineListingHandler({
      engines: [makeConfiguredEngineSummaryFor(LISTED_DEFAULT_KEY)],
      defaultKey: LISTED_DEFAULT_KEY,
    });
    server.use(handler);
    renderAt();
    expect(screen.queryByTestId("home-blank-slate")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-target")).not.toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/");
    release();
    expect(await screen.findByTestId("repo-target")).toHaveTextContent(LISTED_DEFAULT_KEY);
    expect(screen.queryByTestId("home-blank-slate")).not.toBeInTheDocument();
  });

  it("never mounts the blank slate at any point of a cold load whose list names a configured default", async () => {
    const { handler, release } = gatedEngineListingHandler({
      engines: [makeConfiguredEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY)],
      defaultKey: STUB_DEFAULT_ENGINE_KEY,
    });
    server.use(handler);
    const selector = '[data-testid="home-blank-slate"]';
    let mounted = false;
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (node instanceof Element && (node.matches(selector) || node.querySelector(selector) !== null)) {
            mounted = true;
          }
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    renderAt();
    release();
    expect(await screen.findByTestId("repo-target")).toHaveTextContent(STUB_DEFAULT_ENGINE_KEY);
    observer.disconnect();
    expect(mounted).toBe(false);
  });

  it("lands on the dashboard when a workspace page returns to the home path, as after deleting a workspace", async () => {
    mockEngines([makeConfiguredEngineSummaryFor(LISTED_DEFAULT_KEY)], LISTED_DEFAULT_KEY);
    const user = userEvent.setup();
    renderAt("/workspaces/t1", <Route path="/workspaces/:taskId" element={<WorkspacePage />} />);
    const leave = await screen.findByTestId("leave-workspace");
    await waitFor(() => {
      expect(leave).toBeEnabled();
    });
    await user.click(leave);
    expect(await screen.findByTestId("repo-target")).toHaveTextContent(LISTED_DEFAULT_KEY);
    expect(screen.getByTestId("path")).toHaveTextContent(`/repos/${LISTED_DEFAULT_KEY}`);
    expect(screen.queryByTestId("home-blank-slate")).not.toBeInTheDocument();
  });

  it("redirects to the configured default when the only other repository is hidden", async () => {
    mockEngines(
      [makeConfiguredEngineSummaryFor(LISTED_DEFAULT_KEY), makeEngineSummaryFor(WIDGET_KEY, { hidden: true })],
      LISTED_DEFAULT_KEY,
    );
    renderAt();
    expect(await screen.findByTestId("repo-target")).toHaveTextContent(LISTED_DEFAULT_KEY);
    expect(screen.queryByTestId("home-blank-slate")).not.toBeInTheDocument();
  });

  it("keeps the blank slate for an unconfigured default whose forge target is explicitly null", async () => {
    mockEngines([makeEngineSummaryFor(LISTED_DEFAULT_KEY, { forge: null })], LISTED_DEFAULT_KEY);
    renderAt();
    await screen.findByTestId("home-blank-slate");
    expect(screen.queryByTestId("repo-target")).not.toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/");
  });
});
