import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { useEngines } from "../../../hooks/useEngines";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  gatedEngineListingHandler,
  LISTED_DEFAULT_KEY,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
  type DefaultKeyState,
} from "../../../test/engineFixtures";
import { mockDoc, mockProject } from "../../../test/projectPageHarness";
import { server } from "../../../test/server";
import Project from "./Project";

const OPS_PANEL_FOR_BUILT_IN_PROJECT: Record<DefaultKeyState, boolean> = {
  equal: false,
  other: true,
};

const OPS_PANEL_FOR_LISTED_DEFAULT_PROJECT: Record<DefaultKeyState, boolean> = {
  equal: true,
  other: false,
};

function ListingProbe() {
  const { loaded } = useEngines();
  return <span data-testid="listing-loaded">{String(loaded)}</span>;
}

function mockEngineEndpoints(engineKey: string): void {
  const [owner, repo] = engineKey.split("/");
  server.use(
    http.get(`/api/engines/${owner}/${repo}`, () =>
      HttpResponse.json({ repo: { owner, repo }, posture: "propose" }),
    ),
    http.get(`/api/engines/${owner}/${repo}/pool`, () =>
      HttpResponse.json({
        slug: engineKey,
        size: 1,
        free_count: 1,
        leased_count: 0,
        warm: true,
        quiescing: false,
        disk_usage_bytes: 0,
      }),
    ),
    http.get(`/api/engines/${owner}/${repo}/preflight`, () => HttpResponse.json(null)),
  );
}

async function renderProjectAfterListing(
  engineKey: string | null,
  defaultKey: string | undefined,
): Promise<void> {
  mockProject({ engine_key: engineKey });
  mockDoc();
  if (engineKey !== null) mockEngineEndpoints(engineKey);
  server.use(
    engineListingHandler({
      engines: [makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY), makeEngineSummaryFor(LISTED_DEFAULT_KEY)],
      defaultKey,
    }),
  );
  render(
    <MemoryRouter initialEntries={["/projects/alpha"]}>
      <ListingProbe />
      <Routes>
        <Route path="/projects/:projectId" element={<Project />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { level: 2, name: "Alpha" });
  await waitFor(() => {
    expect(screen.getByTestId("listing-loaded")).toHaveTextContent("true");
  });
}

describe("Project — engine ops panel against the listed default", () => {
  it.each(DEFAULT_KEY_STATES)(
    "shows the ops panel for a project bound to the built-in key only when the listed default differs from it (%s)",
    async (state, defaultKey) => {
      await renderProjectAfterListing(STUB_DEFAULT_ENGINE_KEY, defaultKey);
      if (OPS_PANEL_FOR_BUILT_IN_PROJECT[state]) {
        expect(await screen.findByTestId("engine-ops-identity")).toHaveTextContent(
          STUB_DEFAULT_ENGINE_KEY,
        );
      }
      expect(screen.queryByTestId("engine-ops-panel") !== null).toBe(
        OPS_PANEL_FOR_BUILT_IN_PROJECT[state],
      );
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "shows the ops panel for a project bound to the listed default's key only when it is not the resolved default (%s)",
    async (state, defaultKey) => {
      await renderProjectAfterListing(LISTED_DEFAULT_KEY, defaultKey);
      if (OPS_PANEL_FOR_LISTED_DEFAULT_PROJECT[state]) {
        expect(await screen.findByTestId("engine-ops-identity")).toHaveTextContent(
          LISTED_DEFAULT_KEY,
        );
      }
      expect(screen.queryByTestId("engine-ops-panel") !== null).toBe(
        OPS_PANEL_FOR_LISTED_DEFAULT_PROJECT[state],
      );
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "never shows the ops panel for a project with no engine key (%s)",
    async (_state, defaultKey) => {
      await renderProjectAfterListing(null, defaultKey);
      expect(screen.queryByTestId("engine-ops-panel")).not.toBeInTheDocument();
    },
  );
});

describe("Project — engine ops panel without a resolved default", () => {
  it("shows the ops panel for any engine-bound project once the list names no default", async () => {
    await renderProjectAfterListing(LISTED_DEFAULT_KEY, undefined);
    expect(await screen.findByTestId("engine-ops-identity")).toHaveTextContent(LISTED_DEFAULT_KEY);
  });

  it("shows no ops panel before the list settles, then shows it for a project bound to another repository", async () => {
    mockProject({ engine_key: LISTED_DEFAULT_KEY });
    mockDoc();
    mockEngineEndpoints(LISTED_DEFAULT_KEY);
    const { handler, release } = gatedEngineListingHandler({
      engines: [makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY), makeEngineSummaryFor(LISTED_DEFAULT_KEY)],
      defaultKey: STUB_DEFAULT_ENGINE_KEY,
    });
    server.use(handler);
    render(
      <MemoryRouter initialEntries={["/projects/alpha"]}>
        <ListingProbe />
        <Routes>
          <Route path="/projects/:projectId" element={<Project />} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    expect(screen.queryByTestId("engine-ops-panel")).not.toBeInTheDocument();
    release();
    expect(await screen.findByTestId("engine-ops-identity")).toHaveTextContent(LISTED_DEFAULT_KEY);
  });

  it("keeps the ops panel hidden for a project bound to the default while the list is still loading", async () => {
    mockProject({ engine_key: STUB_DEFAULT_ENGINE_KEY });
    mockDoc();
    mockEngineEndpoints(STUB_DEFAULT_ENGINE_KEY);
    const { handler, release } = gatedEngineListingHandler({
      engines: [makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY)],
      defaultKey: STUB_DEFAULT_ENGINE_KEY,
    });
    server.use(handler);
    render(
      <MemoryRouter initialEntries={["/projects/alpha"]}>
        <ListingProbe />
        <Routes>
          <Route path="/projects/:projectId" element={<Project />} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    expect(screen.queryByTestId("engine-ops-panel")).not.toBeInTheDocument();
    release();
    await waitFor(() => {
      expect(screen.getByTestId("listing-loaded")).toHaveTextContent("true");
    });
    expect(screen.queryByTestId("engine-ops-panel")).not.toBeInTheDocument();
  });
});
