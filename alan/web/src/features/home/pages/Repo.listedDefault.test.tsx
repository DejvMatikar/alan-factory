import { act, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import type { EngineSummary, ProjectListEntry, WorkspaceSummary } from "../../../api";
import { useEngines } from "../../../hooks/useEngines";
import { writeProjectFeaturesOn } from "../../../projectFeatures";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  LISTED_DEFAULT_KEY,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
} from "../../../test/engineFixtures";
import { makeProjectListEntry } from "../../../test/projectFixtures";
import { server } from "../../../test/server";
import { makeWorkspaceSummary } from "../../../test/workspaceFixtures";
import Repo from "./Repo";

const WIDGET_KEY = "acme/widget";
const BUILT_IN_IS_DEFAULT = DEFAULT_KEY_STATES.filter(([state]) => state !== "other");
const LISTED_IS_DEFAULT = DEFAULT_KEY_STATES.filter(([state]) => state === "other");

function listed(key: string): EngineSummary {
  return makeEngineSummaryFor(key, { posture: "autonomous" });
}

function mockListing(keys: readonly string[], defaultKey: string | undefined): void {
  server.use(engineListingHandler({ engines: keys.map(listed), defaultKey }));
}

function mockHome(workspaces: readonly WorkspaceSummary[], projects: readonly ProjectListEntry[] = []): void {
  server.use(
    http.get("/api/workspaces", () => HttpResponse.json(workspaces)),
    http.get("/api/projects", () => HttpResponse.json(projects)),
  );
}

function EnginesLoadedProbe() {
  const { loaded } = useEngines();
  return <div data-testid="engines-loaded">{String(loaded)}</div>;
}

function renderAt(initial: string) {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <EnginesLoadedProbe />
      <Routes>
        <Route path="/" element={<div data-testid="redirected-home">home</div>} />
        <Route path="/repos/:owner/:repo" element={<Repo />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function awaitListingLoaded(): Promise<void> {
  await waitFor(() => expect(screen.getByTestId("engines-loaded")).toHaveTextContent("true"));
  await act(async () => {
    await Promise.resolve();
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  writeProjectFeaturesOn(true);
  mockHome([]);
  server.use(
    http.get(/\/api\/engines\/[^/]+\/[^/]+\/onboarding$/, () =>
      HttpResponse.json({ detail: "engine has no forge configured" }, { status: 422 }),
    ),
  );
});

describe("Repo — repository-less items land in the listed default section", () => {
  it.each(DEFAULT_KEY_STATES)(
    "shows a workspace without an engine_key under the listed default repository (%s)",
    async (_state, defaultKey) => {
      mockListing([WIDGET_KEY], defaultKey);
      mockHome([makeWorkspaceSummary({ task_id: "ws-bare", engine_key: null })]);
      renderAt(`/repos/${defaultKey}`);
      expect(await screen.findByTestId("home-row-feature-ws-bare")).toBeInTheDocument();
      expect(screen.getByTestId(`home-row-unassigned-${defaultKey}`)).toBeInTheDocument();
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "keeps a workspace without an engine_key out of another registered repository's section (%s)",
    async (_state, defaultKey) => {
      mockListing([WIDGET_KEY], defaultKey);
      mockHome([makeWorkspaceSummary({ task_id: "ws-bare", engine_key: null })]);
      renderAt(`/repos/${WIDGET_KEY}`);
      await awaitListingLoaded();
      await screen.findByTestId(`repo-empty-${WIDGET_KEY}`);
      expect(screen.queryByTestId("home-row-feature-ws-bare")).not.toBeInTheDocument();
    },
  );

  it("keeps a workspace without an engine_key out of the built-in key's section under a different listed default", async () => {
    mockListing([WIDGET_KEY, STUB_DEFAULT_ENGINE_KEY], LISTED_DEFAULT_KEY);
    mockHome([makeWorkspaceSummary({ task_id: "ws-bare", engine_key: null })]);
    renderAt(`/repos/${STUB_DEFAULT_ENGINE_KEY}`);
    await awaitListingLoaded();
    await screen.findByTestId(`repo-empty-${STUB_DEFAULT_ENGINE_KEY}`);
    expect(screen.queryByTestId("home-row-feature-ws-bare")).not.toBeInTheDocument();
  });

  it.each(DEFAULT_KEY_STATES)(
    "shows a project bound to the default repository's key in that section (%s)",
    async (_state, defaultKey) => {
      mockListing([WIDGET_KEY], defaultKey);
      mockHome([], [makeProjectListEntry({ id: "proj-bound", engine_key: defaultKey })]);
      renderAt(`/repos/${defaultKey}`);
      expect(await screen.findByTestId("home-row-project-proj-bound")).toBeInTheDocument();
    },
  );

  it.each(BUILT_IN_IS_DEFAULT)(
    "places an unbound project intended for the built-in key in the built-in default section (%s)",
    async (_state, defaultKey) => {
      mockListing([WIDGET_KEY], defaultKey);
      mockHome(
        [],
        [makeProjectListEntry({ id: "proj-intended", intended_engine_key: STUB_DEFAULT_ENGINE_KEY })],
      );
      renderAt(`/repos/${STUB_DEFAULT_ENGINE_KEY}`);
      expect(await screen.findByTestId("home-row-project-proj-intended")).toBeInTheDocument();
    },
  );

  it.each(LISTED_IS_DEFAULT)(
    "leaves an unbound project intended for the unregistered built-in key out of the listed section (%s)",
    async (_state, defaultKey) => {
      mockListing([WIDGET_KEY], defaultKey);
      mockHome(
        [],
        [makeProjectListEntry({ id: "proj-intended", intended_engine_key: STUB_DEFAULT_ENGINE_KEY })],
      );
      renderAt(`/repos/${LISTED_DEFAULT_KEY}`);
      await awaitListingLoaded();
      await screen.findByTestId(`repo-empty-${LISTED_DEFAULT_KEY}`);
      expect(screen.queryByTestId("home-row-project-proj-intended")).not.toBeInTheDocument();
    },
  );

  it("places an unbound project intended for the built-in key in the built-in section once that repository is registered under a different listed default", async () => {
    mockListing([WIDGET_KEY, STUB_DEFAULT_ENGINE_KEY], LISTED_DEFAULT_KEY);
    mockHome(
      [],
      [makeProjectListEntry({ id: "proj-intended", intended_engine_key: STUB_DEFAULT_ENGINE_KEY })],
    );
    renderAt(`/repos/${STUB_DEFAULT_ENGINE_KEY}`);
    expect(await screen.findByTestId("home-row-project-proj-intended")).toBeInTheDocument();
  });
});

describe("Repo — the built-in key is an ordinary section under a different listed default", () => {
  it("renders a workspace tagged with the built-in key in its own section when that repository is registered", async () => {
    mockListing([WIDGET_KEY, STUB_DEFAULT_ENGINE_KEY], LISTED_DEFAULT_KEY);
    mockHome([makeWorkspaceSummary({ task_id: "ws-builtin", engine_key: STUB_DEFAULT_ENGINE_KEY })]);
    renderAt(`/repos/${STUB_DEFAULT_ENGINE_KEY}`);
    expect(await screen.findByTestId("home-row-feature-ws-builtin")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Alan" })).toBeInTheDocument();
  });

  it("keeps a workspace tagged with the built-in key out of the listed default's section", async () => {
    mockListing([WIDGET_KEY, STUB_DEFAULT_ENGINE_KEY], LISTED_DEFAULT_KEY);
    mockHome([makeWorkspaceSummary({ task_id: "ws-builtin", engine_key: STUB_DEFAULT_ENGINE_KEY })]);
    renderAt(`/repos/${LISTED_DEFAULT_KEY}`);
    await awaitListingLoaded();
    await screen.findByTestId(`repo-empty-${LISTED_DEFAULT_KEY}`);
    expect(screen.queryByTestId("home-row-feature-ws-builtin")).not.toBeInTheDocument();
  });

  it("renders the always-present listed default section even though it is not a registered engine", async () => {
    mockListing([WIDGET_KEY], LISTED_DEFAULT_KEY);
    renderAt(`/repos/${LISTED_DEFAULT_KEY}`);
    await awaitListingLoaded();
    expect(await screen.findByTestId(`repo-empty-${LISTED_DEFAULT_KEY}`)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Listed Default" })).toBeInTheDocument();
    expect(screen.queryByTestId("redirected-home")).not.toBeInTheDocument();
  });
});

describe("Repo — the Hide control follows the listed default", () => {
  it.each(DEFAULT_KEY_STATES)(
    "disables Hide with the default-repository explanation on the listed default's section (%s)",
    async (_state, defaultKey) => {
      mockListing([WIDGET_KEY], defaultKey);
      renderAt(`/repos/${defaultKey}`);
      const hide = await screen.findByTestId(`repo-action-hide-${defaultKey}`);
      await waitFor(() => expect(hide).toHaveAttribute("aria-disabled", "true"));
      expect(hide).toHaveAccessibleDescription("The default repository can't be hidden.");
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "leaves Hide enabled on another registered repository (%s)",
    async (_state, defaultKey) => {
      mockListing([WIDGET_KEY, defaultKey], defaultKey);
      renderAt(`/repos/${WIDGET_KEY}`);
      await awaitListingLoaded();
      const hide = await screen.findByTestId(`repo-action-hide-${WIDGET_KEY}`);
      expect(hide).toHaveAttribute("aria-disabled", "false");
      expect(hide).not.toHaveAccessibleDescription("The default repository can't be hidden.");
    },
  );

  it.each(BUILT_IN_IS_DEFAULT)(
    "keeps Hide disabled on the built-in key while it is the default (%s)",
    async (_state, defaultKey) => {
      mockListing([WIDGET_KEY, STUB_DEFAULT_ENGINE_KEY], defaultKey);
      renderAt(`/repos/${STUB_DEFAULT_ENGINE_KEY}`);
      await awaitListingLoaded();
      const hide = await screen.findByTestId(`repo-action-hide-${STUB_DEFAULT_ENGINE_KEY}`);
      expect(hide).toHaveAttribute("aria-disabled", "true");
    },
  );

  it.each(LISTED_IS_DEFAULT)(
    "enables Hide on the built-in key once it is an ordinary registered repository (%s)",
    async (_state, defaultKey) => {
      mockListing([WIDGET_KEY, STUB_DEFAULT_ENGINE_KEY], defaultKey);
      renderAt(`/repos/${STUB_DEFAULT_ENGINE_KEY}`);
      const hide = await screen.findByTestId(`repo-action-hide-${STUB_DEFAULT_ENGINE_KEY}`);
      await waitFor(() => expect(hide).toHaveAttribute("aria-disabled", "false"));
      expect(hide).not.toHaveAccessibleDescription("The default repository can't be hidden.");
    },
  );
});

describe("Repo — scope redirect follows the listed default", () => {
  it.each(LISTED_IS_DEFAULT)(
    "redirects home from the unregistered built-in key once the list loads a different default (%s)",
    async (_state, defaultKey) => {
      mockListing([WIDGET_KEY], defaultKey);
      renderAt(`/repos/${STUB_DEFAULT_ENGINE_KEY}`);
      expect(await screen.findByTestId("redirected-home")).toBeInTheDocument();
    },
  );

  it.each(BUILT_IN_IS_DEFAULT)(
    "never redirects home from the built-in key while it is the default (%s)",
    async (_state, defaultKey) => {
      mockListing([WIDGET_KEY], defaultKey);
      renderAt(`/repos/${STUB_DEFAULT_ENGINE_KEY}`);
      await awaitListingLoaded();
      expect(screen.queryByTestId("redirected-home")).not.toBeInTheDocument();
      expect(screen.getByTestId(`repo-section-${STUB_DEFAULT_ENGINE_KEY}`)).toBeInTheDocument();
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "never redirects home from the listed default's own section (%s)",
    async (_state, defaultKey) => {
      mockListing([WIDGET_KEY], defaultKey);
      renderAt(`/repos/${defaultKey}`);
      await awaitListingLoaded();
      expect(screen.queryByTestId("redirected-home")).not.toBeInTheDocument();
      expect(screen.getByTestId(`repo-section-${defaultKey}`)).toBeInTheDocument();
    },
  );

  it("does not redirect before the list loads, then redirects the built-in key once a different default is listed", async () => {
    const gate: { resolve: (() => void) | null } = { resolve: null };
    server.use(
      http.get("/api/engines", async () => {
        await new Promise<void>((resolve) => {
          gate.resolve = resolve;
        });
        return HttpResponse.json({
          engines: [listed(WIDGET_KEY)],
          default_engine_key: LISTED_DEFAULT_KEY,
        });
      }),
    );
    renderAt(`/repos/${STUB_DEFAULT_ENGINE_KEY}`);
    await screen.findByTestId(`repo-section-${STUB_DEFAULT_ENGINE_KEY}`);
    await waitFor(() => expect(gate.resolve).not.toBeNull());
    expect(screen.getByTestId("engines-loaded")).toHaveTextContent("false");
    expect(screen.queryByTestId("redirected-home")).not.toBeInTheDocument();

    gate.resolve?.();
    expect(await screen.findByTestId("redirected-home")).toBeInTheDocument();
  });

  it("keeps the section and leaves Hide disabled when the list load fails, since no default is known", async () => {
    server.use(http.get("/api/engines", () => HttpResponse.error()));
    renderAt(`/repos/${STUB_DEFAULT_ENGINE_KEY}`);
    await screen.findByTestId(`repo-section-${STUB_DEFAULT_ENGINE_KEY}`);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByTestId("redirected-home")).not.toBeInTheDocument();
    expect(screen.getByTestId(`repo-action-hide-${STUB_DEFAULT_ENGINE_KEY}`)).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });
});
