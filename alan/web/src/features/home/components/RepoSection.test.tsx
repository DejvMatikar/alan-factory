import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { HealthFinding } from "../../../api";
import { HiddenRepoAnnouncementRegion } from "../../../components/HiddenRepoDialog";
import { HealthContext, type HealthContextValue } from "../../../api/HealthProvider";
import { useEngines } from "../../../hooks/useEngines";
import type { ResolvedAccent } from "../../../lib/repoAccent";
import { STICKY_REGION_CLASS } from "../../../shell/stickyOffsets";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  LISTED_DEFAULT_KEY,
  makeEngineSummaryFor,
  settleDefaultEngineKey,
  STUB_DEFAULT_ENGINE_KEY,
} from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import { makeWorkspaceSummary } from "../../../test/workspaceFixtures";
import RepoSection from "./RepoSection";
import type { RepoHeaderModel, RepoSectionModel } from "../../../lib/fold";

vi.mock("./RepoStatusStrip", () => ({
  default: ({ engineKey }: Readonly<{ engineKey: string }>) => (
    <div data-testid={`repo-status-strip-stub-${engineKey}`}>{engineKey}</div>
  ),
}));

function healthFinding(overrides: Partial<HealthFinding> = {}): HealthFinding {
  return {
    finding_id: "f1",
    kind: "main_ci",
    severity: "medium",
    description: "d",
    workspace_id: null,
    pr_number: null,
    blocker_flags: [],
    action: "deep_link",
    engine_key: null,
    in_flight: false,
    ...overrides,
  };
}

function healthContextValue(overrides: Partial<HealthContextValue> = {}): HealthContextValue {
  return {
    queuePaused: false,
    pausedUntil: null,
    healthState: null,
    blockedCount: 0,
    needsUserCount: 0,
    lastComputedAt: null,
    finisherRate: null,
    qualityMetrics: null,
    sweeper: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

const EMPTY_SECTION: RepoSectionModel = {
  key: "acme/sprocket",
  title: "Sprocket",
  posture: "autonomous",
  projectGroups: [],
  unassigned: [],
  shippedCount: 0,
  archivedCount: 0,
};

interface RenderSectionOpts {
  header: RepoHeaderModel;
  ready: boolean;
  tab: "active" | "archived";
  reload: () => void;
  showProjects: boolean;
  resolvedAccent: ResolvedAccent | null;
  repoChrome: boolean;
  tabs: ReactNode;
  health: HealthContextValue;
  announcementRegion: boolean;
  postureResolved: boolean;
}

function defaultHeaderFor(section: RepoSectionModel): RepoHeaderModel {
  return { key: section.key, title: section.title, posture: section.posture };
}

const DEFAULT_RESOLVED_ACCENT: ResolvedAccent = { color: "iris", intensity: "standard" };

function renderSectionDefaults(section: RepoSectionModel): RenderSectionOpts {
  return {
    header: defaultHeaderFor(section),
    ready: true,
    tab: "active",
    reload: vi.fn(),
    showProjects: true,
    resolvedAccent: DEFAULT_RESOLVED_ACCENT,
    repoChrome: true,
    tabs: undefined,
    health: healthContextValue(),
    announcementRegion: false,
    postureResolved: true,
  };
}

function sectionElement(section: RepoSectionModel, opts: RenderSectionOpts): ReactElement {
  return (
    <MemoryRouter>
      <HealthContext.Provider value={opts.health}>
        {opts.announcementRegion && <HiddenRepoAnnouncementRegion />}
        <RepoSection
          header={opts.header}
          section={section}
          ready={opts.ready}
          tab={opts.tab}
          reload={opts.reload}
          showProjects={opts.showProjects}
          resolvedAccent={opts.resolvedAccent}
          repoChrome={opts.repoChrome}
          tabs={opts.tabs}
          postureResolved={opts.postureResolved}
        />
      </HealthContext.Provider>
    </MemoryRouter>
  );
}

function renderSection(section: RepoSectionModel, opts: Partial<RenderSectionOpts> = {}) {
  const merged: RenderSectionOpts = { ...renderSectionDefaults(section), ...opts };
  return render(sectionElement(section, merged));
}

describe("RepoSection — flat chrome", () => {
  it("renders no rounded panel, tint, or padding classes on the section itself", () => {
    renderSection(EMPTY_SECTION);
    const section = screen.getByTestId("repo-section-acme/sprocket");
    expect(section.className).not.toContain("rounded-lg");
    expect(section.className).not.toContain("bg-surface-elevated");
    expect(section.className).not.toMatch(/(^|\s)p-3(\s|$)/);
  });

  it("renders no rolled-up state chip and no summed active-time figure in the header", () => {
    renderSection(EMPTY_SECTION);
    expect(screen.queryByTestId("aggregate-chip-repo-acme/sprocket")).not.toBeInTheDocument();
    expect(screen.queryByTestId("active-time-repo-acme/sprocket")).not.toBeInTheDocument();
  });

  it("renders the repository name as visible text, not sr-only, coloured by the repository's own accent", () => {
    renderSection(EMPTY_SECTION);
    const heading = screen.getByRole("heading", { level: 1, name: "Sprocket" });
    expect(heading.className).not.toContain("sr-only");
    expect(heading.className).toContain("text-[var(--accent-fg)]");
  });

  it("renders the repository name in the neutral text token when the repository has explicitly chosen no accent", () => {
    renderSection(EMPTY_SECTION, { resolvedAccent: null });
    const heading = screen.getByRole("heading", { level: 1, name: "Sprocket" });
    expect(heading.className).toContain("text-text-primary");
    expect(heading.className).not.toContain("text-[var(--accent-fg)]");
  });

  it("no longer carries its own accent edge — the frame moved to the shell — and never on the status strip either", () => {
    renderSection(EMPTY_SECTION);
    const section = screen.getByTestId("repo-section-acme/sprocket");
    expect(section.style.getPropertyValue("--accent-fg")).toBe("#9e80e7");
    expect(section.className).not.toContain("border-l-2");
    expect(section.className).not.toContain("border-[var(--accent-edge)]");
    expect(screen.getByTestId("repo-status-strip-stub-acme/sprocket").className).not.toContain(
      "accent-edge",
    );
  });
});

function findAncestorWithClass(el: Element, cls: string): Element | null {
  let node: Element | null = el;
  while (node !== null) {
    if (node.className.split(/\s+/).includes(cls)) return node;
    node = node.parentElement;
  }
  return null;
}

describe("RepoSection — sticky header region", () => {
  it("wraps the header row in the shared sticky-region class", () => {
    renderSection(EMPTY_SECTION);
    const heading = screen.getByRole("heading", { level: 1, name: "Sprocket" });
    const stickyWrapper = findAncestorWithClass(heading, STICKY_REGION_CLASS.split(" ")[0]!);
    expect(stickyWrapper).not.toBeNull();
    expect(stickyWrapper?.className).toBe(`${STICKY_REGION_CLASS} space-y-2`);
  });

  it("keeps Maintenance/Hide notices inside the same sticky wrapper as the header", () => {
    renderSection(EMPTY_SECTION);
    const heading = screen.getByRole("heading", { level: 1, name: "Sprocket" });
    const actionRow = screen.getByTestId("repo-action-row-acme/sprocket");
    expect(heading.compareDocumentPosition(actionRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const headingWrapper = findAncestorWithClass(heading, STICKY_REGION_CLASS.split(" ")[0]!);
    const actionRowWrapper = findAncestorWithClass(actionRow, STICKY_REGION_CLASS.split(" ")[0]!);
    expect(headingWrapper).toBe(actionRowWrapper);
  });
});

describe("RepoSection — header layout", () => {
  it("places Maintenance alone at the left of the header, separate from the right-hand cluster", () => {
    renderSection(EMPTY_SECTION);
    const maintenance = screen.getByTestId("repo-action-maintenance-acme/sprocket");
    const health = screen.getByTestId("repo-health-acme/sprocket");
    expect(maintenance.compareDocumentPosition(health) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("orders the right-hand cluster hide, health, settings — mirroring the top bar", () => {
    renderSection(EMPTY_SECTION);
    const order = [
      screen.getByTestId("repo-action-hide-acme/sprocket"),
      screen.getByTestId("repo-health-acme/sprocket"),
      screen.getByTestId("repo-action-settings-acme/sprocket"),
    ];
    for (let i = 0; i < order.length - 1; i += 1) {
      expect(
        order[i]!.compareDocumentPosition(order[i + 1]!) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });

  it("renders Maintenance as a link to the posture setting, not a live dispatch button, when the repository is gated below autonomous posture", () => {
    renderSection(EMPTY_SECTION, { header: { ...defaultHeaderFor(EMPTY_SECTION), posture: "observe" } });
    const maintenance = screen.getByTestId("repo-action-maintenance-acme/sprocket");
    expect(maintenance.tagName).toBe("A");
    expect(maintenance).toHaveAttribute("href", "/connect/acme/sprocket/posture");
  });

  it("renders Maintenance as a real dispatch button, carrying the dimmed-chip weight class, when autonomous", () => {
    renderSection(EMPTY_SECTION);
    const maintenance = screen.getByTestId("repo-action-maintenance-acme/sprocket");
    expect(maintenance.tagName).toBe("BUTTON");
    expect(maintenance.className).toContain("opacity-70");
    expect(maintenance.className).toContain("hover:opacity-100");
  });

  it("derives Maintenance's gating from the header prop, not from the (possibly still-empty) section — the posture-flip fix", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, posture: null };
    renderSection(section, {
      ready: false,
      header: { key: "acme/sprocket", title: "Sprocket", posture: "autonomous" },
    });
    const maintenance = screen.getByTestId("repo-action-maintenance-acme/sprocket");
    expect(maintenance.tagName).toBe("BUTTON");
  });
});

describe("RepoSection — health control", () => {
  it("links health to the health screen filtered to this repository, with a distinguishing accessible name", () => {
    renderSection(EMPTY_SECTION);
    const link = screen.getByTestId("repo-health-acme/sprocket");
    expect(link).toHaveAttribute("href", "/health?engine=acme%2Fsprocket");
    expect(link).toHaveAccessibleName(/Health for acme\/sprocket/);
  });

  it("builds the health figure from the repository-scoped finding count, never the fleet-wide verdict", () => {
    renderSection(EMPTY_SECTION, {
      health: healthContextValue({
        healthState: {
          verdict: "degraded",
          blocked_count: 5,
          in_flight_count: 0,
          findings: [
            healthFinding({ finding_id: "a", engine_key: "acme/sprocket" }),
            healthFinding({ finding_id: "b", engine_key: "acme/sprocket" }),
            healthFinding({ finding_id: "c", engine_key: "other/repo" }),
          ],
          computed_at: "2026-05-01T00:00:00Z",
        },
      }),
    });
    expect(screen.getByTestId("repo-health-acme/sprocket-count")).toHaveTextContent("2");
  });

  it("suppresses the health pill when this repository has zero scoped findings, even under a fleet-wide degraded verdict", () => {
    renderSection(EMPTY_SECTION, {
      health: healthContextValue({
        healthState: {
          verdict: "degraded",
          blocked_count: 1,
          in_flight_count: 0,
          findings: [healthFinding({ finding_id: "a", engine_key: "other/repo" })],
          computed_at: "2026-05-01T00:00:00Z",
        },
      }),
    });
    expect(screen.queryByTestId("repo-health-acme/sprocket-count")).not.toBeInTheDocument();
  });

  it("counts a null-engine finding toward the default repository", () => {
    settleDefaultEngineKey(STUB_DEFAULT_ENGINE_KEY);
    renderSection(
      { ...EMPTY_SECTION, key: "DejvMatikar/alan", title: "Alan" },
      {
        header: { key: "DejvMatikar/alan", title: "Alan", posture: "autonomous" },
        health: healthContextValue({
          healthState: {
            verdict: "degraded",
            blocked_count: 1,
            in_flight_count: 0,
            findings: [healthFinding({ finding_id: "a", engine_key: null })],
            computed_at: "2026-05-01T00:00:00Z",
          },
        }),
      },
    );
    expect(screen.getByTestId("repo-health-DejvMatikar/alan-count")).toHaveTextContent("1");
  });
});

describe("RepoSection — settings accessible name", () => {
  it("gives the card's Settings control a repo-scoped accessible name, distinct from the global settings gear", () => {
    renderSection(EMPTY_SECTION);
    const settings = screen.getByTestId("repo-action-settings-acme/sprocket");
    expect(settings).toHaveAccessibleName("Settings for acme/sprocket");
  });
});

describe("RepoSection — chrome ordering", () => {
  it("renders the repo-scoped action toolbar between the header and the status strip", () => {
    renderSection(EMPTY_SECTION);
    const toolbar = screen.getByTestId("repo-action-toolbar-acme/sprocket");
    const strip = screen.getByTestId("repo-status-strip-stub-acme/sprocket");
    const heading = screen.getByRole("heading", { level: 1, name: "Sprocket" });
    expect(heading.compareDocumentPosition(toolbar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(toolbar.compareDocumentPosition(strip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders the shipped/archived footer link to the projects view", () => {
    renderSection({ ...EMPTY_SECTION, shippedCount: 3, archivedCount: 1 });
    const link = screen.getByRole("link", { name: /3 shipped · 1 archived/ });
    expect(link).toHaveAttribute("href", "/projects");
  });

  it("renders the tabs prop above the workspace table, with no recurring-processes panel on this page", () => {
    renderSection(
      { ...EMPTY_SECTION, unassigned: [] },
      { tabs: <div data-testid="injected-tabs">tabs</div> },
    );
    const tabs = screen.getByTestId("injected-tabs");
    const empty = screen.getByTestId("repo-empty-acme/sprocket");
    expect(tabs.compareDocumentPosition(empty) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByTestId("recurring-processes-panel-acme/sprocket")).not.toBeInTheDocument();
  });

  it("renders no tabs element when the tabs prop is omitted", () => {
    renderSection(EMPTY_SECTION);
    expect(screen.queryByTestId("injected-tabs")).not.toBeInTheDocument();
  });

  it("relabels Hide to Hiding… while a hide request is in flight", async () => {
    let resolveHide!: () => void;
    const hideGate = new Promise<void>((resolve) => {
      resolveHide = resolve;
    });
    server.use(
      http.post("/api/engines/acme/sprocket/hide", async () => {
        await hideGate;
        return HttpResponse.json({
          slug: "acme/sprocket",
          residue_workspace_ids: [],
          workers_interrupted: 0,
          chats_cancelled: 0,
          pool_disposed: true,
          pool_residue: null,
        });
      }),
    );
    settleDefaultEngineKey(STUB_DEFAULT_ENGINE_KEY);
    const user = userEvent.setup();
    renderSection(EMPTY_SECTION, { announcementRegion: true });
    const hide = screen.getByTestId("repo-action-hide-acme/sprocket");
    await user.click(hide);
    expect(screen.getByTestId("repo-action-hide-acme/sprocket")).toHaveTextContent("Hiding…");
    resolveHide();
    await waitFor(() =>
      expect(screen.getByTestId("hidden-repo-announcement")).toHaveTextContent("stopped cleanly"),
    );
    expect(screen.getByTestId("hidden-repo-announcement").className).not.toContain("sr-only");
  });

  it("fails fast instead of emitting a corrupted /repos//config link when the section key has no owner/repo separator", () => {
    const malformed: RepoSectionModel = { ...EMPTY_SECTION, key: "malformed" };
    expect(() =>
      renderSection(malformed, { header: { key: "malformed", title: "Malformed", posture: null } }),
    ).toThrow("invalid repoKey: malformed");
  });
});

describe("RepoSection — repoChrome", () => {
  it("renders the toolbar, status strip, footer, and header action controls by default", () => {
    renderSection(EMPTY_SECTION);
    expect(screen.getByTestId("repo-action-toolbar-acme/sprocket")).toBeInTheDocument();
    expect(screen.getByTestId("repo-status-strip-stub-acme/sprocket")).toBeInTheDocument();
    expect(screen.getByTestId("repo-footer-acme/sprocket")).toBeInTheDocument();
    expect(screen.getByTestId("repo-action-maintenance-acme/sprocket")).toBeInTheDocument();
    expect(screen.getByTestId("repo-action-hide-acme/sprocket")).toBeInTheDocument();
    expect(screen.getByTestId("repo-action-settings-acme/sprocket")).toBeInTheDocument();
    expect(screen.getByTestId("repo-health-acme/sprocket")).toBeInTheDocument();
  });

  it("suppresses the toolbar, status strip, footer, and header action controls when repoChrome is false", () => {
    renderSection(EMPTY_SECTION, { repoChrome: false });
    expect(screen.queryByTestId("repo-action-toolbar-acme/sprocket")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-status-strip-stub-acme/sprocket")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-footer-acme/sprocket")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-action-maintenance-acme/sprocket")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-action-hide-acme/sprocket")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-action-settings-acme/sprocket")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-health-acme/sprocket")).not.toBeInTheDocument();
  });

  it("still renders the sr-only header and the empty message when repoChrome is false", () => {
    renderSection(EMPTY_SECTION, { repoChrome: false });
    const heading = screen.getByRole("heading", { level: 1, name: "Sprocket" });
    expect(heading.className).toContain("sr-only");
    expect(screen.getByTestId("repo-empty-acme/sprocket")).toBeInTheDocument();
  });
});

describe("RepoSection — unresolved posture (maintenance gate)", () => {
  it("renders Maintenance as a non-actionable busy button, never a navigable link, while posture is unresolved", () => {
    renderSection(EMPTY_SECTION, { postureResolved: false });
    const maintenance = screen.getByTestId("repo-action-maintenance-acme/sprocket");
    expect(maintenance.tagName).toBe("BUTTON");
    expect(maintenance).toHaveAttribute("aria-disabled", "true");
    expect(maintenance).toHaveAttribute("aria-busy", "true");
    expect(maintenance).not.toHaveAttribute("href");
  });

  it("shows the checking-posture description while unresolved", () => {
    renderSection(EMPTY_SECTION, { postureResolved: false });
    const maintenance = screen.getByTestId("repo-action-maintenance-acme/sprocket");
    const describedBy = maintenance.getAttribute("aria-describedby");
    expect(document.getElementById(String(describedBy))).toHaveTextContent(
      "Checking this repository's posture",
    );
  });

  it("dispatches no maintenance run and shows no notice when clicked while unresolved", async () => {
    const user = userEvent.setup();
    renderSection(EMPTY_SECTION, { postureResolved: false });
    await user.click(screen.getByTestId("repo-action-maintenance-acme/sprocket"));
    expect(screen.queryByTestId("repo-maintenance-notice-acme/sprocket")).not.toBeInTheDocument();
  });

  it("reuses the same host node across an unresolved-to-permitted transition, so keyboard focus survives it", () => {
    const { rerender } = renderSection(EMPTY_SECTION, { postureResolved: false });
    const before = screen.getByTestId("repo-action-maintenance-acme/sprocket");
    const permittedOpts: RenderSectionOpts = { ...renderSectionDefaults(EMPTY_SECTION), postureResolved: true };
    rerender(sectionElement(EMPTY_SECTION, permittedOpts));
    const after = screen.getByTestId("repo-action-maintenance-acme/sprocket");
    expect(after).toBe(before);
    expect(after.tagName).toBe("BUTTON");
  });

  it("swaps to a new host node across an unresolved-to-gated transition, since the not-permitted form is a real link", () => {
    const { rerender } = renderSection(EMPTY_SECTION, { postureResolved: false });
    const before = screen.getByTestId("repo-action-maintenance-acme/sprocket");
    const gatedOpts: RenderSectionOpts = {
      ...renderSectionDefaults(EMPTY_SECTION),
      postureResolved: true,
      header: { ...defaultHeaderFor(EMPTY_SECTION), posture: "observe" },
    };
    rerender(sectionElement(EMPTY_SECTION, gatedOpts));
    const after = screen.getByTestId("repo-action-maintenance-acme/sprocket");
    expect(after).not.toBe(before);
    expect(after.tagName).toBe("A");
  });
});

describe("RepoSection — table region delegation", () => {
  it("shows the loading register in the table region while not ready, with the header and chrome still present", () => {
    renderSection(EMPTY_SECTION, { ready: false });
    expect(screen.getByTestId("loading")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Sprocket" })).toBeInTheDocument();
    expect(screen.getByTestId("repo-status-strip-stub-acme/sprocket")).toBeInTheDocument();
  });

  it("shows the table once ready with unassigned workspaces present, threading reload into the row action", async () => {
    const reload = vi.fn();
    server.use(
      http.post(/\/api\/workspaces\/[^/]+\/stop$/, () => new HttpResponse(null, { status: 204 })),
    );
    renderSection(
      {
        ...EMPTY_SECTION,
        unassigned: [
          makeWorkspaceSummary({ task_id: "w1", title: "w1", project_id: null }),
        ],
      },
      { reload },
    );
    expect(within(screen.getByRole("table")).getByTestId("home-row-feature-w1")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("workspace-row-action-w1"));
    await screen.findByTestId("workspace-row-action-w1");
    expect(reload).toHaveBeenCalled();
  });
});

const DEFAULT_HIDE_DESCRIPTION = "The default repository can't be hidden.";
const ORDINARY_KEY = "acme/widget";

function sectionFor(key: string): RepoSectionModel {
  return { ...EMPTY_SECTION, key, title: key };
}

function EnginesListProbe() {
  const { loaded } = useEngines();
  return <div data-testid="engines-loaded">{String(loaded)}</div>;
}

function renderListedSection(key: string, defaultKey: string | undefined, opts: Partial<RenderSectionOpts> = {}) {
  server.use(
    engineListingHandler({
      engines: [makeEngineSummaryFor(ORDINARY_KEY), makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY)],
      defaultKey,
    }),
  );
  const section = sectionFor(key);
  const merged: RenderSectionOpts = { ...renderSectionDefaults(section), ...opts };
  return render(
    <>
      <EnginesListProbe />
      {sectionElement(section, merged)}
    </>,
  );
}

async function awaitListingLoaded(): Promise<void> {
  await waitFor(() => expect(screen.getByTestId("engines-loaded")).toHaveTextContent("true"));
}

function healthWithFindings(engineKeys: readonly (string | null)[]): HealthContextValue {
  return healthContextValue({
    healthState: {
      verdict: "degraded",
      blocked_count: 1,
      in_flight_count: 0,
      findings: engineKeys.map((engine_key, index) =>
        healthFinding({ finding_id: `f${String(index)}`, engine_key }),
      ),
      computed_at: "2026-05-01T00:00:00Z",
    },
  });
}

describe("RepoSection — Hide follows the listed default repository", () => {
  it.each(DEFAULT_KEY_STATES)(
    "disables Hide and explains why on the listed default's section (%s)",
    async (_state, defaultKey) => {
      renderListedSection(defaultKey, defaultKey);
      await awaitListingLoaded();
      const hide = screen.getByTestId(`repo-action-hide-${defaultKey}`);
      expect(hide).toHaveAttribute("aria-disabled", "true");
      expect(hide).toHaveAccessibleDescription(DEFAULT_HIDE_DESCRIPTION);
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "leaves Hide enabled on an ordinary repository (%s)",
    async (_state, defaultKey) => {
      renderListedSection(ORDINARY_KEY, defaultKey);
      await awaitListingLoaded();
      const hide = screen.getByTestId(`repo-action-hide-${ORDINARY_KEY}`);
      expect(hide).toHaveAttribute("aria-disabled", "false");
      expect(hide).not.toHaveAccessibleDescription(DEFAULT_HIDE_DESCRIPTION);
    },
  );

  it("enables Hide on the built-in key once the list names a different default", async () => {
    renderListedSection(STUB_DEFAULT_ENGINE_KEY, LISTED_DEFAULT_KEY);
    const hide = screen.getByTestId(`repo-action-hide-${STUB_DEFAULT_ENGINE_KEY}`);
    expect(hide).toHaveAttribute("aria-disabled", "true");
    await waitFor(() => expect(hide).toHaveAttribute("aria-disabled", "false"));
    expect(hide).not.toHaveAccessibleDescription(DEFAULT_HIDE_DESCRIPTION);
  });

  it("leaves Hide enabled on every repository once the list names no default", async () => {
    renderListedSection(STUB_DEFAULT_ENGINE_KEY, undefined);
    await awaitListingLoaded();
    const hide = screen.getByTestId(`repo-action-hide-${STUB_DEFAULT_ENGINE_KEY}`);
    expect(hide).toHaveAttribute("aria-disabled", "false");
    expect(hide).not.toHaveAccessibleDescription(DEFAULT_HIDE_DESCRIPTION);
  });

  it("leaves Hide disabled while it is not yet known whether the repository is the default", async () => {
    renderListedSection(ORDINARY_KEY, LISTED_DEFAULT_KEY);
    const hide = screen.getByTestId(`repo-action-hide-${ORDINARY_KEY}`);
    expect(hide).toHaveAttribute("aria-disabled", "true");
    expect(hide).not.toHaveAccessibleDescription(DEFAULT_HIDE_DESCRIPTION);
    await awaitListingLoaded();
  });

  it("leaves Hide disabled when the list never arrives, since no default is known", async () => {
    server.use(http.get("/api/engines", () => HttpResponse.error()));
    const section = sectionFor(STUB_DEFAULT_ENGINE_KEY);
    render(
      <>
        <EnginesListProbe />
        {sectionElement(section, renderSectionDefaults(section))}
      </>,
    );
    await awaitListingLoaded();
    expect(screen.getByTestId(`repo-action-hide-${STUB_DEFAULT_ENGINE_KEY}`)).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });
});

describe("RepoSection — health finding attribution follows the listed default repository", () => {
  it.each(DEFAULT_KEY_STATES)(
    "counts findings without an engine_key, plus its own, toward the listed default's section (%s)",
    async (_state, defaultKey) => {
      const own = defaultKey;
      renderListedSection(own, defaultKey, { health: healthWithFindings([null, own, ORDINARY_KEY]) });
      await awaitListingLoaded();
      await waitFor(() =>
        expect(screen.getByTestId(`repo-health-${own}-count`)).toHaveTextContent("2"),
      );
    },
  );

  it.each(DEFAULT_KEY_STATES)(
    "does not count findings without an engine_key toward a repository that is not the default (%s)",
    async (_state, defaultKey) => {
      renderListedSection(ORDINARY_KEY, defaultKey, {
        health: healthWithFindings([null, ORDINARY_KEY]),
      });
      await awaitListingLoaded();
      expect(screen.getByTestId(`repo-health-${ORDINARY_KEY}-count`)).toHaveTextContent("1");
    },
  );

  it("attributes findings without an engine_key to the built-in key only while the list names it the default", async () => {
    renderListedSection(STUB_DEFAULT_ENGINE_KEY, LISTED_DEFAULT_KEY, {
      health: healthWithFindings([null, STUB_DEFAULT_ENGINE_KEY]),
    });
    expect(screen.getByTestId(`repo-health-${STUB_DEFAULT_ENGINE_KEY}-count`)).toHaveTextContent("1");
    await awaitListingLoaded();
    expect(screen.getByTestId(`repo-health-${STUB_DEFAULT_ENGINE_KEY}-count`)).toHaveTextContent("1");
  });

  it("attributes no finding without an engine_key to any repository once the list names no default", async () => {
    renderListedSection(STUB_DEFAULT_ENGINE_KEY, undefined, {
      health: healthWithFindings([null, STUB_DEFAULT_ENGINE_KEY]),
    });
    await awaitListingLoaded();
    expect(screen.getByTestId(`repo-health-${STUB_DEFAULT_ENGINE_KEY}-count`)).toHaveTextContent("1");
  });

  it("attributes findings without an engine_key to a listed default that has no findings of its own", async () => {
    renderListedSection(LISTED_DEFAULT_KEY, LISTED_DEFAULT_KEY, {
      health: healthWithFindings([null, null]),
    });
    expect(screen.queryByTestId(`repo-health-${LISTED_DEFAULT_KEY}-count`)).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId(`repo-health-${LISTED_DEFAULT_KEY}-count`)).toHaveTextContent("2"),
    );
  });
});
