import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import type { EngineSummary, HealthFinding, HealthState } from "../../../api";
import { HealthProvider } from "../../../api/HealthProvider";
import { useEngines } from "../../../hooks/useEngines";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  LISTED_DEFAULT_KEY,
  makeConfiguredEngineSummaryFor,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
} from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import HealthView from "./HealthView";

const WIDGET_KEY = "acme/widget";
const BUILT_IN_IS_DEFAULT = DEFAULT_KEY_STATES.filter(([state]) => state !== "other");
const LISTED_IS_DEFAULT = DEFAULT_KEY_STATES.filter(([state]) => state === "other");

const FINDING_ID_BY_KEY: Readonly<Record<string, string>> = {
  [STUB_DEFAULT_ENGINE_KEY]: "f-builtin",
  [LISTED_DEFAULT_KEY]: "f-listed",
  [WIDGET_KEY]: "f-widget",
};

function findingFor(findingId: string, engineKey: string | undefined): HealthFinding {
  return {
    finding_id: findingId,
    kind: "build_task",
    severity: "medium",
    description: `Finding ${findingId}`,
    workspace_id: findingId,
    pr_number: null,
    blocker_flags: [],
    action: "deep_link",
    in_flight: false,
    ...(engineKey === undefined ? {} : { engine_key: engineKey }),
  };
}

function fourFindingState(): HealthState {
  return {
    verdict: "degraded",
    blocked_count: 4,
    in_flight_count: 0,
    findings: [
      findingFor("f-bare", undefined),
      findingFor("f-builtin", STUB_DEFAULT_ENGINE_KEY),
      findingFor("f-listed", LISTED_DEFAULT_KEY),
      findingFor("f-widget", WIDGET_KEY),
    ],
    computed_at: new Date().toISOString(),
  };
}

function mockHealthAndListing(
  engines: readonly EngineSummary[],
  defaultKey: string | undefined,
): void {
  server.use(
    engineListingHandler({ engines, defaultKey }),
    http.get("/api/health", () =>
      HttpResponse.json({
        ok: true,
        queue_paused: false,
        paused_until: null,
        health_state: fourFindingState(),
      }),
    ),
  );
}

function EnginesLoadedProbe() {
  const { loaded } = useEngines();
  return <div data-testid="engines-loaded">{String(loaded)}</div>;
}

function renderView(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <EnginesLoadedProbe />
      <HealthProvider>
        <HealthView />
      </HealthProvider>
    </MemoryRouter>,
  );
}

async function awaitListingLoaded(): Promise<void> {
  await waitFor(() => {
    expect(screen.getByTestId("engines-loaded")).toHaveTextContent("true");
  });
}

function scopedPath(engineKey: string): string {
  return `/health?engine=${encodeURIComponent(engineKey)}`;
}

function visibleFindingIds(): string[] {
  return screen
    .queryAllByTestId(/^finding-row-/)
    .map((row) => (row.getAttribute("data-testid") ?? "").replace("finding-row-", ""))
    .sort();
}

function badgedFindingIds(): string[] {
  return screen
    .queryAllByTestId(/^engine-badge-/)
    .map((badge) => (badge.getAttribute("data-testid") ?? "").replace("engine-badge-", ""))
    .sort();
}

beforeEach(() => {
  server.use(
    http.get("/api/health/unblocker-activity", ({ request }) => {
      const engine = new URL(request.url).searchParams.get("engine");
      const shared = {
        window_days: 30,
        events_total: 0,
        workspaces_touched: 0,
        waited_workspaces: 0,
        diagnosed_workspaces: 0,
        diagnosable_workspaces: 0,
        aggregates: [],
      };
      return HttpResponse.json(
        engine === null
          ? { scope: "fleet", ...shared, ever_recorded: true, unattributed_events: 0 }
          : { scope: "repo", repo: engine, ...shared, fleet_ever_recorded: true, events: [] },
      );
    }),
  );
});

describe("HealthView — ?engine= scope follows the listed default repository", () => {
  it.each(DEFAULT_KEY_STATES)(
    "attributes findings without an engine_key to the listed default's scope, alongside its own (%s)",
    async (_state, defaultKey) => {
      const own = defaultKey;
      mockHealthAndListing([makeEngineSummaryFor(WIDGET_KEY)], defaultKey);
      renderView(scopedPath(own));
      await awaitListingLoaded();
      await waitFor(() => {
        expect(visibleFindingIds()).toEqual(["f-bare", FINDING_ID_BY_KEY[own]].sort());
      });
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "hides findings without an engine_key under another repository's scope (%s)",
    async (_state, defaultKey) => {
      mockHealthAndListing([makeEngineSummaryFor(WIDGET_KEY)], defaultKey);
      renderView(scopedPath(WIDGET_KEY));
      await awaitListingLoaded();
      await waitFor(() => {
        expect(visibleFindingIds()).toEqual(["f-widget"]);
      });
      expect(screen.queryByTestId("finding-row-f-bare")).not.toBeInTheDocument();
    },
  );

  it.each(BUILT_IN_IS_DEFAULT)(
    "attributes findings without an engine_key to the built-in key's scope while it is the default (%s)",
    async (_state, defaultKey) => {
      mockHealthAndListing([makeEngineSummaryFor(WIDGET_KEY)], defaultKey);
      renderView(scopedPath(STUB_DEFAULT_ENGINE_KEY));
      await awaitListingLoaded();
      await waitFor(() => {
        expect(visibleFindingIds()).toEqual(["f-bare", "f-builtin"]);
      });
    },
  );

  it.each(LISTED_IS_DEFAULT)(
    "treats the built-in key as an ordinary scope, dropping findings without an engine_key once the list arrives (%s)",
    async (_state, defaultKey) => {
      mockHealthAndListing([makeEngineSummaryFor(WIDGET_KEY)], defaultKey);
      renderView(scopedPath(STUB_DEFAULT_ENGINE_KEY));
      await awaitListingLoaded();
      await waitFor(() => {
        expect(visibleFindingIds()).toEqual(["f-builtin"]);
      });
    },
  );

  it.each(LISTED_IS_DEFAULT)(
    "does not attribute the built-in key's own finding to the listed default's scope (%s)",
    async (_state, defaultKey) => {
      mockHealthAndListing(
        [makeEngineSummaryFor(WIDGET_KEY), makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY)],
        defaultKey,
      );
      renderView(scopedPath(LISTED_DEFAULT_KEY));
      await awaitListingLoaded();
      await waitFor(() => {
        expect(visibleFindingIds()).toEqual(["f-bare", "f-listed"]);
      });
      expect(screen.queryByTestId("finding-row-f-builtin")).not.toBeInTheDocument();
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "shows every finding in the unscoped view regardless of the default (%s)",
    async (_state, defaultKey) => {
      mockHealthAndListing([makeEngineSummaryFor(WIDGET_KEY)], defaultKey);
      renderView("/health");
      await awaitListingLoaded();
      await waitFor(() => {
        expect(visibleFindingIds()).toEqual(["f-bare", "f-builtin", "f-listed", "f-widget"]);
      });
    },
  );

  it("keeps a finding without an engine_key when only the built-in repository is hidden under a different listed default", async () => {
    mockHealthAndListing(
      [
        makeEngineSummaryFor(WIDGET_KEY),
        makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY, { hidden: true }),
      ],
      LISTED_DEFAULT_KEY,
    );
    renderView("/health");
    await awaitListingLoaded();
    await waitFor(() => {
      expect(visibleFindingIds()).toEqual(["f-bare", "f-listed", "f-widget"]);
    });
  });
});

describe("HealthView — engine badges follow the listed default repository", () => {
  it.each(DEFAULT_KEY_STATES)(
    "badges every keyed finding except the listed default's own once another engine is registered (%s)",
    async (_state, defaultKey) => {
      const own = defaultKey;
      mockHealthAndListing([makeEngineSummaryFor(WIDGET_KEY)], defaultKey);
      renderView("/health");
      await awaitListingLoaded();
      const expected = Object.entries(FINDING_ID_BY_KEY)
        .filter(([key]) => key !== own)
        .map(([, findingId]) => findingId)
        .sort();
      await waitFor(() => {
        expect(badgedFindingIds()).toEqual(expected);
      });
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "shows no engine badges when only the listed default is registered (%s)",
    async (_state, defaultKey) => {
      mockHealthAndListing([makeEngineSummaryFor(defaultKey)], defaultKey);
      renderView("/health");
      await awaitListingLoaded();
      await waitFor(() => {
        expect(visibleFindingIds()).toHaveLength(4);
      });
      expect(badgedFindingIds()).toEqual([]);
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "shows no engine badges when only a configured listed default is registered (%s)",
    async (_state, defaultKey) => {
      mockHealthAndListing([makeConfiguredEngineSummaryFor(defaultKey)], defaultKey);
      renderView("/health");
      await awaitListingLoaded();
      await waitFor(() => {
        expect(visibleFindingIds()).toHaveLength(4);
      });
      expect(badgedFindingIds()).toEqual([]);
    },
  );

  it("badges the built-in key's finding once that repository is registered under a different listed default", async () => {
    mockHealthAndListing([makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY)], LISTED_DEFAULT_KEY);
    renderView("/health");
    await awaitListingLoaded();
    await waitFor(() => {
      expect(badgedFindingIds()).toEqual(["f-builtin", "f-widget"]);
    });
  });
});
