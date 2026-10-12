import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChangeKind, EngineQuiesceReport, EnginePosture } from "../../../api";
import { HIDDEN_REPO_DISCLOSURE_SUMMARY_ID, HiddenRepoAnnouncementRegion } from "../../../components/HiddenRepoDialog";
import { writeProjectFeaturesOn } from "../../../projectFeatures";
import { accentEdgeHex, resolveAccents } from "../../../lib/repoAccent";
import { ServerEventsContext } from "../../../api/ServerEventsProvider";
import { engineListingHandler, makeEngineSummary } from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import { useEnginesRefresh } from "../../../hooks/useEngines";
import Repo from "./Repo";

function zeroCounters(): Record<ChangeKind, number> {
  return { workspace_list: 0, health: 0, engines: 0 };
}

function ControlledServerEvents({
  children,
  bumpRef,
}: {
  readonly children: ReactNode;
  readonly bumpRef: { current: (() => void) | null };
}) {
  const [counters, setCounters] = useState<Record<ChangeKind, number>>(zeroCounters);
  useEffect(() => {
    bumpRef.current = () => {
      setCounters((prev) => ({ ...prev, engines: prev.engines + 1 }));
    };
  }, [bumpRef]);
  const value = useMemo(() => ({ counters, connected: true }), [counters]);
  return <ServerEventsContext.Provider value={value}>{children}</ServerEventsContext.Provider>;
}

function EnginesRefreshProbe() {
  useEnginesRefresh();
  return null;
}

function mockEngines(repos: { owner: string; repo: string; posture?: EnginePosture }[]): void {
  server.use(
    http.get("/api/engines", () =>
      HttpResponse.json({
        engines: repos.map(({ owner, repo, posture }) => ({
          repo: { owner, repo },
          posture: posture ?? "autonomous",
        })),
      }),
    ),
  );
}

function mockWorkspacesAndProjects(): void {
  server.use(
    http.get("/api/workspaces", () => HttpResponse.json([])),
    http.get("/api/projects", () => HttpResponse.json([])),
  );
}

function renderAt(initial: string) {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <button type="button" id={HIDDEN_REPO_DISCLOSURE_SUMMARY_ID} data-testid="hidden-trigger">
        Hidden repositories
      </button>
      <button type="button" data-testid="decoy-focus-target">
        Decoy
      </button>
      <HiddenRepoAnnouncementRegion />
      <EnginesRefreshProbe />
      <Routes>
        <Route path="/" element={<div data-testid="redirected-home">home</div>} />
        <Route path="/repos/:owner/:repo" element={<Repo />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  writeProjectFeaturesOn(true);
  mockWorkspacesAndProjects();
  server.use(
    http.get(/\/api\/engines\/[^/]+\/[^/]+\/onboarding$/, () =>
      HttpResponse.json({ detail: "engine has no forge configured" }, { status: 422 }),
    ),
  );
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Repo — no matching route params", () => {
  it("renders nothing when mounted off a route that carries no owner/repo params", () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<Repo />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("home-toolbar")).not.toBeInTheDocument();
  });
});

describe("Repo — restores the stored tab", () => {
  it("restores the Archived tab from sessionStorage on mount", async () => {
    sessionStorage.setItem("workspace-list-tab", "archived");
    mockEngines([{ owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    await screen.findByTestId("repo-empty-acme/widget");
    expect(screen.getByRole("tab", { name: "Archived" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("Repo — needs-you slot", () => {
  it("renders the needs-you slot with the host-setup line when no control token is set", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    await screen.findByTestId("repo-empty-acme/widget");
    const slot = screen.getByTestId("home-needs-you-slot");
    expect(within(slot).getByTestId("needs-you-host-setup")).toBeInTheDocument();
  });
});

describe("Repo — needs-you slot excludes hidden repos' resume lines", () => {
  it("never fetches onboarding for a hidden co-tenant, so its resume line cannot reach the visible dashboard", async () => {
    let widgetOnboardingFetched = false;
    let shadowOnboardingFetched = false;
    server.use(
      http.get("/api/engines", () =>
        HttpResponse.json({
          engines: [
            { repo: { owner: "acme", repo: "widget" }, posture: "autonomous" },
            { repo: { owner: "acme", repo: "shadow" }, posture: "autonomous", hidden: true },
          ],
        }),
      ),
      http.get("/api/engines/acme/widget/onboarding", () => {
        widgetOnboardingFetched = true;
        return HttpResponse.json({ detail: "engine has no forge configured" }, { status: 422 });
      }),
      http.get("/api/engines/acme/shadow/onboarding", () => {
        shadowOnboardingFetched = true;
        return HttpResponse.json({
          state: {
            slug: "acme/shadow",
            stack: null,
            scaffold_pr_number: 1,
            scaffold_pr_url: null,
            authoritative_checks: [],
            protection_applied_keys: [],
            protection_deferred: [],
          },
          checklist: [
            {
              check: "review_and_merge_scaffold_pr",
              status: "fail",
              message: "m",
              remedy: "",
              remedy_url: "",
            },
          ],
          overall: "pass",
        });
      }),
      http.get("/api/secrets", () => HttpResponse.json({ secrets: [] })),
    );
    renderAt("/repos/acme/widget");
    await screen.findByTestId("repo-empty-acme/widget");
    await waitFor(() => expect(widgetOnboardingFetched).toBe(true));
    expect(shadowOnboardingFetched).toBe(false);
    const slot = screen.getByTestId("home-needs-you-slot");
    expect(within(slot).queryByTestId("needs-you-resume-acme-shadow")).not.toBeInTheDocument();
  });
});

describe("Repo — renders the scoped section", () => {
  it("renders the scoped repo's heading and empty state", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    await screen.findByTestId("repo-empty-acme/widget");
    expect(screen.getByRole("heading", { level: 1, name: "Widget" })).toBeInTheDocument();
    expect(screen.getByTestId("repo-empty-acme/widget")).toHaveTextContent("No active workspaces yet.");
  });

  it("renders the default repo's section even though DejvMatikar/alan is never a literally registered engine", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    renderAt("/repos/DejvMatikar/alan");
    await screen.findByTestId("repo-empty-DejvMatikar/alan");
    expect(screen.getByRole("heading", { level: 1, name: "Alan" })).toBeInTheDocument();
  });
});

describe("Repo — accent resolves from the unfiltered engine list", () => {
  it("keeps the dashboard heading's accent consistent with the shell frame's allEngines-based resolution, even when a hidden co-tenant claims the colour this repo would otherwise auto-pick alone", async () => {
    const visible = makeEngineSummary({ posture: "autonomous" });
    const aloneAccent = resolveAccents([visible]).get("acme/widget");
    if (aloneAccent == null) throw new Error("expected an auto-assigned accent");
    const hiddenCotenant = makeEngineSummary({
      repo: { owner: "acme", repo: "shadow" },
      posture: "autonomous",
      hidden: true,
      accent: { color: aloneAccent.color, intensity: "standard" },
    });
    const allEngines = [visible, hiddenCotenant];
    const expected = resolveAccents(allEngines).get("acme/widget");
    if (expected == null) throw new Error("expected an auto-assigned accent");
    expect(expected.color).not.toBe(aloneAccent.color);

    server.use(http.get("/api/engines", () => HttpResponse.json({ engines: allEngines })));
    renderAt("/repos/acme/widget");
    await screen.findByTestId("repo-empty-acme/widget");
    const section = screen.getByTestId("repo-section-acme/widget");
    expect(section.style.getPropertyValue("--accent-edge")).toBe(
      accentEdgeHex(expected.color, expected.intensity),
    );
  });
});

describe("Repo — unknown repository", () => {
  it("renders an empty repo page before engines settle, not an immediate redirect", async () => {
    let resolveEngines: (() => void) | null = null;
    server.use(
      http.get("/api/engines", async () => {
        await new Promise<void>((resolve) => {
          resolveEngines = resolve;
        });
        return HttpResponse.json({ engines: [] });
      }),
    );
    renderAt("/repos/never-registered-co/ghost");
    await screen.findByTestId("repo-empty-never-registered-co/ghost");
    expect(screen.getByRole("heading", { level: 1, name: "Ghost" })).toBeInTheDocument();
    expect(screen.queryByTestId("redirected-home")).not.toBeInTheDocument();

    await act(async () => {
      resolveEngines?.();
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByTestId("redirected-home")).toBeInTheDocument());
  });
});

async function renderWidgetOnceListedAutonomous(): Promise<void> {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  server.use(engineListingHandler({ engines: [makeEngineSummary({ posture: "autonomous" })] }));
  renderAt("/repos/acme/widget");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  await screen.findByRole("heading", { level: 1, name: "Widget" });
  await waitFor(() =>
    expect(screen.getByTestId("repo-action-maintenance-acme/widget")).toHaveAttribute(
      "aria-disabled",
      "false",
    ),
  );
}

async function pollUntilWidgetDisappears(): Promise<void> {
  server.use(engineListingHandler({ engines: [] }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15000);
  });
}

describe("Repo — DoD-7: scope leaving the visible set navigates home", () => {
  it("navigates to / once the scoped repo disappears from the visible engine list, and focuses the hidden-repos trigger when nothing else has focus", async () => {
    await renderWidgetOnceListedAutonomous();
    expect(screen.queryByTestId("redirected-home")).not.toBeInTheDocument();

    await pollUntilWidgetDisappears();
    expect(screen.getByTestId("redirected-home")).toBeInTheDocument();
    expect(screen.getByTestId("hidden-trigger")).toHaveFocus();
  });

  it("does not steal focus already claimed elsewhere by the time the redirect fires", async () => {
    await renderWidgetOnceListedAutonomous();
    screen.getByTestId("decoy-focus-target").focus();

    await pollUntilWidgetDisappears();
    expect(screen.getByTestId("redirected-home")).toBeInTheDocument();
    expect(screen.getByTestId("hidden-trigger")).not.toHaveFocus();
    expect(screen.getByTestId("decoy-focus-target")).toHaveFocus();
  });

  it("stays put while the scoped repo remains visible after a poll", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockEngines([{ owner: "acme", repo: "widget" }]);
    renderAt("/repos/acme/widget");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await screen.findByRole("heading", { level: 1, name: "Widget" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(screen.queryByTestId("redirected-home")).not.toBeInTheDocument();
  });
});

describe("Repo — maintenance affordance threads useEngines().loaded, not ready", () => {
  it("renders Maintenance as a non-navigable, aria-disabled button while engines are unsettled, then an enabled dispatch button once they resolve autonomous", async () => {
    let resolveEngines: (() => void) | null = null;
    server.use(
      http.get("/api/engines", async () => {
        await new Promise<void>((resolve) => {
          resolveEngines = resolve;
        });
        return HttpResponse.json({
          engines: [{ repo: { owner: "acme", repo: "widget" }, posture: "autonomous" }],
        });
      }),
    );
    renderAt("/repos/acme/widget");
    const maintenance = await screen.findByTestId("repo-action-maintenance-acme/widget");
    expect(maintenance.tagName).toBe("BUTTON");
    expect(maintenance).toHaveAttribute("aria-disabled", "true");
    expect(maintenance).not.toHaveAttribute("href");

    await act(async () => {
      resolveEngines?.();
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(screen.getByTestId("repo-action-maintenance-acme/widget")).toHaveAttribute(
        "aria-disabled",
        "false",
      ),
    );
  });
});

describe("Repo — loading state", () => {
  it("shows the loading register while workspaces/projects have not resolved, then the section once they do", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    const gate: { resolve: (() => void) | null } = { resolve: null };
    server.use(
      http.get("/api/workspaces", async () => {
        await new Promise<void>((resolve) => {
          gate.resolve = resolve;
        });
        return HttpResponse.json([]);
      }),
    );
    renderAt("/repos/acme/widget");
    expect(await screen.findByTestId("loading")).toBeInTheDocument();
    await waitFor(() => expect(gate.resolve).not.toBeNull());
    gate.resolve?.();
    await screen.findByTestId("repo-empty-acme/widget");
  });
});

describe("Repo — chrome stability across a tab switch", () => {
  function project(overrides: {
    id: string;
    status: "completed" | "archived";
  }): Record<string, unknown> {
    return {
      id: overrides.id,
      title: overrides.id,
      status: overrides.status,
      doc_ref: null,
      autonomy: "supervised",
      budget_usd: null,
      created_at: "2026-07-01T00:00:00Z",
      updated_at: "2026-07-01T00:00:00Z",
      workspace_count: 0,
      total_spend_usd: 0,
      engine_key: "acme/widget",
    };
  }

  it("keeps the shipped/archived footer counts stable while the archived tab's workspaces are still loading", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    const gate: { resolve: (() => void) | null } = { resolve: null };
    server.use(
      http.get("/api/projects", () =>
        HttpResponse.json([
          project({ id: "shipped-1", status: "completed" }),
          project({ id: "archived-1", status: "archived" }),
        ]),
      ),
      http.get("/api/workspaces", async ({ request }) => {
        const bucket = new URL(request.url).searchParams.get("bucket");
        if (bucket !== "archived") return HttpResponse.json([]);
        await new Promise<void>((resolve) => {
          gate.resolve = resolve;
        });
        return HttpResponse.json([]);
      }),
    );
    renderAt("/repos/acme/widget");
    const footer = await screen.findByTestId("repo-footer-acme/widget");
    expect(footer).toHaveTextContent("1 shipped · 1 archived");

    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Archived" }));

    expect(screen.getByTestId("loading")).toBeInTheDocument();
    expect(screen.getByTestId("repo-footer-acme/widget")).toHaveTextContent("1 shipped · 1 archived");

    await act(async () => {
      gate.resolve?.();
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.queryByTestId("loading")).not.toBeInTheDocument());
    expect(screen.getByTestId("repo-footer-acme/widget")).toHaveTextContent("1 shipped · 1 archived");
  });
});

describe("Repo — tabs and row actions", () => {
  it("switching to the Archived tab persists it to sessionStorage and shows archived workspaces", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    server.use(
      http.get("/api/workspaces", ({ request }) => {
        const bucket = new URL(request.url).searchParams.get("bucket");
        if (bucket === "archived") {
          return HttpResponse.json([
            {
              task_id: "arch-1",
              title: "arch-1",
              status: "archived",
              team_runs_total: 0,
              invocations_total: 0,
              total_cost_usd: null,
              updated_at: "2026-07-01T00:00:00Z",
              created_at: "2026-07-01T00:00:00Z",
              latest_team: null,
              latest_invocation_status: null,
              project_id: null,
              engine_key: "acme/widget",
            },
          ]);
        }
        return HttpResponse.json([]);
      }),
    );
    renderAt("/repos/acme/widget");
    const user = userEvent.setup();
    await screen.findByTestId("repo-empty-acme/widget");
    await user.click(screen.getByRole("tab", { name: "Archived" }));
    await screen.findByTestId("home-row-feature-arch-1");
    expect(sessionStorage.getItem("workspace-list-tab")).toBe("archived");
  });

  it("running the row action on a workspace hits the archive route and reloads the active-tab list", async () => {
    mockEngines([{ owner: "acme", repo: "widget" }]);
    let workspacesFetchCount = 0;
    let archiveHit = false;
    server.use(
      http.get("/api/workspaces", ({ request }) => {
        const bucket = new URL(request.url).searchParams.get("bucket");
        if (bucket === "archived") return HttpResponse.json([]);
        workspacesFetchCount += 1;
        return HttpResponse.json([
          {
            task_id: "w1",
            title: "w1",
            status: "completed",
            team_runs_total: 0,
            invocations_total: 0,
            total_cost_usd: null,
            updated_at: "2026-07-01T00:00:00Z",
            created_at: "2026-07-01T00:00:00Z",
            latest_team: null,
            latest_invocation_status: null,
            project_id: null,
            engine_key: "acme/widget",
          },
        ]);
      }),
      http.post(/\/api\/workspaces\/[^/]+\/archive$/, () => {
        archiveHit = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderAt("/repos/acme/widget");
    const user = userEvent.setup();
    await screen.findByTestId("home-row-feature-w1");
    const fetchCountBeforeClick = workspacesFetchCount;
    await user.click(screen.getByTestId("workspace-row-action-w1"));
    await waitFor(() => expect(archiveHit).toBe(true));
    await waitFor(() => expect(workspacesFetchCount).toBeGreaterThan(fetchCountBeforeClick));
  });
});

describe("Repo — DoD: Maintenance never presents as a navigation control while posture is unknown", () => {
  it("renders an aria-disabled button with no href while engine data is still loading, then a real dispatch button once posture resolves permitting", async () => {
    let resolveEngines: (() => void) | null = null;
    server.use(
      http.get("/api/engines", async () => {
        await new Promise<void>((resolve) => {
          resolveEngines = resolve;
        });
        return HttpResponse.json({
          engines: [{ repo: { owner: "acme", repo: "widget" }, posture: "autonomous" as EnginePosture }],
        });
      }),
    );
    let dispatchCalled = false;
    server.use(
      http.post("/api/workspaces", () => {
        dispatchCalled = true;
        return HttpResponse.json({ task_id: "mnt-1", root_task_ids: ["mnt-1"] }, { status: 201 });
      }),
    );

    renderAt("/repos/acme/widget");
    const maintenance = await screen.findByTestId("repo-action-maintenance-acme/widget");
    expect(maintenance.tagName).toBe("BUTTON");
    expect(maintenance).toHaveAttribute("aria-disabled", "true");
    expect(maintenance).not.toHaveAttribute("href");

    await act(async () => {
      resolveEngines?.();
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(screen.getByTestId("repo-action-maintenance-acme/widget")).not.toHaveAttribute(
        "aria-disabled",
        "true",
      );
    });
    const enabled = screen.getByTestId("repo-action-maintenance-acme/widget");
    expect(enabled.tagName).toBe("BUTTON");

    const user = userEvent.setup();
    await user.click(enabled);
    await waitFor(() => {
      expect(dispatchCalled).toBe(true);
    });
  });
});

describe("Repo — DoD: a hide outcome is reported through the durable announcement channel even when the section unmounts first", () => {
  it("still reports the hide outcome once the request resolves, after a racing engines refetch has already redirected and unmounted the section", async () => {
    const apiModule = await import("../../../api");
    const originalHideEngine = apiModule.api.hideEngine;
    server.use(engineListingHandler({ engines: [makeEngineSummary({ posture: "autonomous" })] }));
    let resolveHide: ((report: EngineQuiesceReport) => void) | null = null;
    apiModule.api.hideEngine = () =>
      new Promise<EngineQuiesceReport>((resolve) => {
        resolveHide = resolve;
      });
    const bumpRef: { current: (() => void) | null } = { current: null };
    try {
      render(
        <MemoryRouter initialEntries={["/repos/acme/widget"]}>
          <button type="button" id={HIDDEN_REPO_DISCLOSURE_SUMMARY_ID} data-testid="hidden-trigger">
            Hidden repositories
          </button>
          <HiddenRepoAnnouncementRegion />
          <ControlledServerEvents bumpRef={bumpRef}>
            <EnginesRefreshProbe />
            <Routes>
              <Route path="/" element={<div data-testid="redirected-home">home</div>} />
              <Route path="/repos/:owner/:repo" element={<Repo />} />
            </Routes>
          </ControlledServerEvents>
        </MemoryRouter>,
      );

      const hide = await screen.findByTestId("repo-action-hide-acme/widget");
      await waitFor(() =>
        expect(screen.getByTestId("repo-action-maintenance-acme/widget")).toHaveAttribute(
          "aria-disabled",
          "false",
        ),
      );
      server.use(
        engineListingHandler({ engines: [makeEngineSummary({ posture: "autonomous", hidden: true })] }),
      );

      act(() => {
        hide.click();
      });

      await act(async () => {
        bumpRef.current?.();
        await Promise.resolve();
      });

      await waitFor(() => expect(screen.getByTestId("redirected-home")).toBeInTheDocument());
      expect(screen.queryByTestId("repo-section-acme/widget")).not.toBeInTheDocument();

      await act(async () => {
        resolveHide?.({
          slug: "acme/widget",
          residue_workspace_ids: [],
          workers_interrupted: 0,
          chats_cancelled: 0,
          pool_disposed: true,
          pool_residue: null,
        });
        await Promise.resolve();
      });

      const announcement = await screen.findByTestId("hidden-repo-announcement");
      expect(announcement).toHaveTextContent("stopped cleanly");
      expect(announcement.className).not.toContain("sr-only");
    } finally {
      apiModule.api.hideEngine = originalHideEngine;
    }
  });
});
