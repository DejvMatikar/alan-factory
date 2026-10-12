import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { useEngines } from "../../../hooks/useEngines";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  gatedEngineListingHandler,
  LISTED_DEFAULT_KEY,
  makeConfiguredEngineSummaryFor,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
  type DefaultKeyState,
} from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import { makeWorkspaceDetail } from "../../../test/workspaceFixtures";
import { useWorkspaceLifecycle } from "../hooks/useWorkspaceLifecycle";
import WorkspaceHeader from "./WorkspaceHeader";

const SPROCKET_KEY = "acme/sprocket";

const BADGE_SHOWN_WHEN_ONLY_BUILT_IN_REGISTERED: Record<DefaultKeyState, boolean> = {
  equal: false,
  other: true,
};

const BADGE_SHOWN_WHEN_ONLY_LISTED_DEFAULT_REGISTERED: Record<DefaultKeyState, boolean> = {
  equal: true,
  other: false,
};

const BUILT_IN_BADGE_SHOWN_ALONGSIDE_LISTED_DEFAULT: Record<DefaultKeyState, boolean> = {
  equal: false,
  other: true,
};

type WorkspaceFixture = ReturnType<typeof makeWorkspaceDetail>;

function Harness({ ws }: Readonly<{ ws: WorkspaceFixture }>) {
  const lifecycle = useWorkspaceLifecycle("t1", ws, vi.fn());
  return <WorkspaceHeader ws={ws} taskId="t1" lifecycle={lifecycle} />;
}

function ListingProbe() {
  const { loaded } = useEngines();
  return <span data-testid="listing-loaded">{String(loaded)}</span>;
}

function renderHeaderWithProbe(ws: WorkspaceFixture): void {
  render(
    <MemoryRouter initialEntries={["/workspaces/t1"]}>
      <ListingProbe />
      <Harness ws={ws} />
    </MemoryRouter>,
  );
}

async function listingLoaded(): Promise<void> {
  await waitFor(() => {
    expect(screen.getByTestId("listing-loaded")).toHaveTextContent("true");
  });
}

async function renderHeaderAfterListing(ws: WorkspaceFixture): Promise<void> {
  renderHeaderWithProbe(ws);
  await listingLoaded();
}

function stubListing(
  keys: readonly string[],
  defaultKey: string | undefined,
  makeSummary: typeof makeEngineSummaryFor = makeEngineSummaryFor,
): void {
  server.use(
    engineListingHandler({ engines: keys.map((key) => makeSummary(key)), defaultKey }),
  );
}

describe("WorkspaceHeader — back link before and after the engine list loads", () => {
  it("shows no back link before the list loads, then adds the one for the listed default", async () => {
    const { handler, release } = gatedEngineListingHandler({
      engines: [makeEngineSummaryFor(SPROCKET_KEY)],
      defaultKey: LISTED_DEFAULT_KEY,
    });
    server.use(handler);
    renderHeaderWithProbe(makeWorkspaceDetail({ engine_key: null }));
    expect(screen.queryByTestId("workspace-back-link")).not.toBeInTheDocument();

    release();
    const back = await screen.findByTestId("workspace-back-link");
    expect(back).toHaveAttribute("href", `/repos/${LISTED_DEFAULT_KEY}`);
    expect(back).toHaveTextContent(`← ${LISTED_DEFAULT_KEY}`);
  });

  it("shows no back link for a workspace with no engine key once the list names no default", async () => {
    stubListing([SPROCKET_KEY], undefined);
    await renderHeaderAfterListing(makeWorkspaceDetail({ engine_key: null }));
    expect(screen.queryByTestId("workspace-back-link")).not.toBeInTheDocument();
  });

  it("never changes the back link of a workspace that carries its own engine key when the list loads", async () => {
    const { handler, release } = gatedEngineListingHandler({
      engines: [makeEngineSummaryFor(SPROCKET_KEY)],
      defaultKey: LISTED_DEFAULT_KEY,
    });
    server.use(handler);
    renderHeaderWithProbe(makeWorkspaceDetail({ engine_key: SPROCKET_KEY }));
    const back = screen.getByTestId("workspace-back-link");
    expect(back).toHaveAttribute("href", `/repos/${SPROCKET_KEY}`);

    release();
    await listingLoaded();
    expect(screen.getByTestId("workspace-back-link")).toBe(back);
    expect(back).toHaveAttribute("href", `/repos/${SPROCKET_KEY}`);
    expect(back).toHaveTextContent(`← ${SPROCKET_KEY}`);
  });

  it.each(DEFAULT_KEY_STATES)(
    "points the back link of a workspace with no engine key at the resolved default once the list loads (%s)",
    async (_state, defaultKey) => {
      const resolvedDefault = defaultKey;
      stubListing([SPROCKET_KEY], defaultKey);
      await renderHeaderAfterListing(makeWorkspaceDetail({ engine_key: null }));
      const back = screen.getByTestId("workspace-back-link");
      expect(back).toHaveAttribute("href", `/repos/${resolvedDefault}`);
      expect(back).toHaveTextContent(`← ${resolvedDefault}`);
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "keeps a workspace's own engine key in the back link once the list loads (%s)",
    async (_state, defaultKey) => {
      stubListing([SPROCKET_KEY], defaultKey);
      await renderHeaderAfterListing(makeWorkspaceDetail({ engine_key: SPROCKET_KEY }));
      expect(screen.getByTestId("workspace-back-link")).toHaveAttribute(
        "href",
        `/repos/${SPROCKET_KEY}`,
      );
    },
  );
});

describe("WorkspaceHeader — engine badge against the listed default", () => {
  it.each(DEFAULT_KEY_STATES)(
    "shows the badge only when a repository other than the resolved default is registered, with only the built-in key registered (%s)",
    async (state, defaultKey) => {
      stubListing([STUB_DEFAULT_ENGINE_KEY], defaultKey);
      await renderHeaderAfterListing(makeWorkspaceDetail({ engine_key: SPROCKET_KEY }));
      expect(screen.queryByTestId("engine-badge-t1") !== null).toBe(
        BADGE_SHOWN_WHEN_ONLY_BUILT_IN_REGISTERED[state],
      );
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "shows the badge only when a repository other than the resolved default is registered, with only the listed default registered (%s)",
    async (state, defaultKey) => {
      stubListing([LISTED_DEFAULT_KEY], defaultKey);
      await renderHeaderAfterListing(makeWorkspaceDetail({ engine_key: SPROCKET_KEY }));
      expect(screen.queryByTestId("engine-badge-t1") !== null).toBe(
        BADGE_SHOWN_WHEN_ONLY_LISTED_DEFAULT_REGISTERED[state],
      );
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "shows the built-in key's badge next to a registered listed default only when the listed default differs from the built-in key (%s)",
    async (state, defaultKey) => {
      stubListing([STUB_DEFAULT_ENGINE_KEY, LISTED_DEFAULT_KEY], defaultKey);
      await renderHeaderAfterListing(
        makeWorkspaceDetail({ engine_key: STUB_DEFAULT_ENGINE_KEY }),
      );
      expect(screen.queryByTestId("engine-badge-t1") !== null).toBe(
        BUILT_IN_BADGE_SHOWN_ALONGSIDE_LISTED_DEFAULT[state],
      );
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "shows no badge for a workspace of a lone configured default, whether or not it names its engine (%s)",
    async (_state, defaultKey) => {
      const own = defaultKey;
      stubListing([own], defaultKey, makeConfiguredEngineSummaryFor);
      await renderHeaderAfterListing(makeWorkspaceDetail({ engine_key: null }));
      expect(screen.queryByTestId("engine-badge-t1")).not.toBeInTheDocument();
      expect(screen.getByTestId("workspace-back-link")).toHaveAttribute("href", `/repos/${own}`);
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "shows no badge for a workspace explicitly naming a lone configured default (%s)",
    async (_state, defaultKey) => {
      const own = defaultKey;
      stubListing([own], defaultKey, makeConfiguredEngineSummaryFor);
      await renderHeaderAfterListing(makeWorkspaceDetail({ engine_key: own }));
      expect(screen.queryByTestId("engine-badge-t1")).not.toBeInTheDocument();
    },
  );

  it("renders the listed default's own badge nowhere once the list loads", async () => {
    stubListing([LISTED_DEFAULT_KEY, SPROCKET_KEY], LISTED_DEFAULT_KEY);
    await renderHeaderAfterListing(makeWorkspaceDetail({ engine_key: LISTED_DEFAULT_KEY }));
    expect(screen.queryByTestId("engine-badge-t1")).not.toBeInTheDocument();
  });
});
