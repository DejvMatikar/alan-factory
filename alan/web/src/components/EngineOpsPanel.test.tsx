import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { HealthState } from "../api";
import { HealthContext, type HealthContextValue } from "../api/HealthProvider";
import { ServerEventsContext, type ServerEventsValue } from "../api/ServerEventsProvider";
import { server } from "../test/server";
import EngineOpsPanel, { DependencySweepTile, PostureTile } from "./EngineOpsPanel";

const ENGINE_KEY = "acme/widget";

const DEFAULT_POSTURE = { repo: { owner: "acme", repo: "widget" }, posture: "propose" as const };
const DEFAULT_POOL = {
  slug: ENGINE_KEY,
  size: 3,
  free_count: 2,
  leased_count: 1,
  warm: true,
  quiescing: false,
  disk_usage_bytes: 1024,
  warmup_seconds: null,
};
const DEFAULT_DEP_SWEEP = {
  slug: ENGINE_KEY,
  enabled: false,
  last_run_at: null,
  next_run_at: null,
};
const DEFAULT_ONBOARDING = {
  state: {
    slug: ENGINE_KEY,
    stack: null,
    scaffold_pr_number: null,
    scaffold_pr_url: null,
    authoritative_checks: [],
    protection_applied_keys: [],
    protection_deferred: [],
  },
  checklist: [
    {
      check: "push_repository",
      status: "pass",
      message: "repository reachable with a HEAD commit",
      remedy: "",
      remedy_url: "",
    },
  ],
  overall: "pass",
};

function mockEngineEndpoints(
  overrides: {
    posture?: () => Response | Promise<Response>;
    pool?: () => Response | Promise<Response>;
    preflight?: () => Response | Promise<Response>;
    depSweep?: () => Response | Promise<Response>;
    onboarding?: () => Response | Promise<Response>;
  } = {},
) {
  server.use(
    http.get(
      "/api/engines/acme/widget",
      overrides.posture ?? (() => HttpResponse.json(DEFAULT_POSTURE)),
    ),
    http.get(
      "/api/engines/acme/widget/pool",
      overrides.pool ?? (() => HttpResponse.json(DEFAULT_POOL)),
    ),
    http.get(
      "/api/engines/acme/widget/preflight",
      overrides.preflight ?? (() => HttpResponse.json(null)),
    ),
    http.get(
      "/api/engines/acme/widget/dependency-sweep",
      overrides.depSweep ?? (() => HttpResponse.json(DEFAULT_DEP_SWEEP)),
    ),
    http.get(
      "/api/engines/acme/widget/onboarding",
      overrides.onboarding ?? (() => HttpResponse.json(DEFAULT_ONBOARDING)),
    ),
  );
}

function eventsValue(engines: number): ServerEventsValue {
  return {
    counters: { workspace_list: 0, health: 0, engines },
    connected: true,
  };
}

function renderWithEvents(engines: number) {
  return render(
    <ServerEventsContext.Provider value={eventsValue(engines)}>
      <EngineOpsPanel engineKey={ENGINE_KEY} />
    </ServerEventsContext.Provider>,
    { wrapper: MemoryRouter },
  );
}

const _EMPTY_HEALTH_CONTEXT: HealthContextValue = {
  queuePaused: false,
  pausedUntil: null,
  healthState: null,
  blockedCount: 0,
  needsUserCount: 0,
  lastComputedAt: null,
  finisherRate: null,
  qualityMetrics: null,
  sweeper: null,
  refetch: () => undefined,
};

function healthWrapper(healthState: HealthState | null, lastComputedAt: string | null = null) {
  return function Wrapper({ children }: Readonly<{ children: ReactNode }>) {
    return (
      <MemoryRouter>
        <HealthContext.Provider
          value={{ ..._EMPTY_HEALTH_CONTEXT, healthState, lastComputedAt }}
        >
          {children}
        </HealthContext.Provider>
      </MemoryRouter>
    );
  };
}

describe("EngineOpsPanel", () => {
  it("links the Configure engine button to this repository's engine config screen", async () => {
    mockEngineEndpoints();
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, {
      wrapper: MemoryRouter,
    });
    const button = await screen.findByTestId("engine-ops-configure-button");
    expect(button).toHaveAttribute("href", "/repos/acme/widget/config");
  });

  it("renders identity and posture once the posture fetch resolves", async () => {
    mockEngineEndpoints();
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-identity")).toHaveTextContent("acme/widget");
    expect(screen.getByTestId("engine-ops-posture-pill")).toHaveTextContent("Propose");
  });

  it("renders pool warmth and capacity counts", async () => {
    mockEngineEndpoints();
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-pool-warm")).toHaveTextContent("Warm");
    expect(screen.getByTestId("engine-ops-pool-free-count")).toHaveTextContent("2 free");
    expect(screen.getByTestId("engine-ops-pool-leased-count")).toHaveTextContent("1 leased");
  });

  it("renders a cold pool distinctly from a warm one", async () => {
    mockEngineEndpoints({
      pool: () => HttpResponse.json({ ...DEFAULT_POOL, warm: false }),
    });
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-pool-warm")).toHaveTextContent("Cold");
  });

  it("renders a never-measured empty state for warm-up duration by default", async () => {
    mockEngineEndpoints();
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-pool-warmup")).toHaveTextContent(
      "Not measured yet",
    );
  });

  it("renders the measured warm-up duration once present", async () => {
    mockEngineEndpoints({
      pool: () => HttpResponse.json({ ...DEFAULT_POOL, warmup_seconds: 63.4 }),
    });
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-pool-warmup")).toHaveTextContent(
      "Warm-up 1m 03s",
    );
  });

  it("renders a never-run empty state when preflight is null", async () => {
    mockEngineEndpoints();
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-preflight-never-run")).toHaveTextContent(
      "Never run",
    );
  });

  it("renders the last preflight outcome and timing when present", async () => {
    mockEngineEndpoints({
      preflight: () =>
        HttpResponse.json({
          slug: ENGINE_KEY,
          checked_at: "2026-07-20T00:00:00Z",
          overall: "fail",
          findings: [{ check: "repo_clonable", status: "fail", message: "nope", remedy: "fix it" }],
        }),
    });
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    const outcome = await screen.findByTestId("engine-ops-preflight-outcome");
    expect(outcome).toHaveTextContent("fail");
    expect(screen.getByTestId("engine-ops-preflight-timing")).toBeInTheDocument();
  });

  describe("preflight readiness row", () => {
    function preflightWith(findings: unknown[]) {
      return () =>
        HttpResponse.json({
          slug: ENGINE_KEY,
          checked_at: "2026-07-20T00:00:00Z",
          overall: "pass",
          findings,
        });
    }

    const READINESS = (overrides: Record<string, unknown>) => ({
      check: "dispatch_readiness",
      status: "pass",
      message: "dispatch requirements are satisfied",
      remedy: "",
      remedy_url: "",
      ...overrides,
    });

    it("shows a ready row beside the overall result when dispatch requirements are satisfied", async () => {
      mockEngineEndpoints({ preflight: preflightWith([READINESS({})]) });
      render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
      const outcome = await screen.findByTestId("engine-ops-preflight-outcome");
      expect(outcome).toHaveTextContent("pass");
      expect(screen.getByTestId("engine-ops-preflight-readiness-status")).toHaveTextContent(
        "Dispatch ready",
      );
      expect(screen.queryByTestId("engine-ops-preflight-readiness-message")).not.toBeInTheDocument();
      expect(outcome.parentElement).toHaveClass("flex");
      expect(outcome.parentElement).toContainElement(
        screen.getByTestId("engine-ops-preflight-readiness"),
      );
    });

    it("keeps the readiness message inside the readiness cell next to the overall result", async () => {
      mockEngineEndpoints({
        preflight: preflightWith([
          READINESS({ status: "fail", message: "code_quality: not set up", remedy: "./setup.sh" }),
        ]),
      });
      render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
      const outcome = await screen.findByTestId("engine-ops-preflight-outcome");
      const readiness = screen.getByTestId("engine-ops-preflight-readiness");
      expect(outcome.parentElement).toContainElement(readiness);
      expect(readiness).toContainElement(
        screen.getByTestId("engine-ops-preflight-readiness-message"),
      );
    });

    it("shows a held row with the message and the fix when a requirement is not ready", async () => {
      mockEngineEndpoints({
        preflight: preflightWith([
          READINESS({
            status: "fail",
            message: "reviewer_credential: no reviewer credential is stored",
            remedy: "store a reviewer credential for acme/widget",
          }),
        ]),
      });
      render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
      expect(await screen.findByTestId("engine-ops-preflight-readiness-status")).toHaveTextContent(
        "Dispatch held",
      );
      const message = screen.getByTestId("engine-ops-preflight-readiness-message");
      expect(message).toHaveTextContent("reviewer_credential: no reviewer credential is stored");
      expect(message).toHaveTextContent("Fix: store a reviewer credential for acme/widget");
    });

    it("shows an unverified row with the message and no fix when only unknowns remain", async () => {
      mockEngineEndpoints({
        preflight: preflightWith([
          READINESS({
            status: "unknown",
            message: "code_quality: could not be evaluated (OSError)",
          }),
        ]),
      });
      render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
      expect(await screen.findByTestId("engine-ops-preflight-readiness-status")).toHaveTextContent(
        "Dispatch readiness unverified",
      );
      const message = screen.getByTestId("engine-ops-preflight-readiness-message");
      expect(message).toHaveTextContent("code_quality: could not be evaluated (OSError)");
      expect(message).not.toHaveTextContent("Fix:");
    });

    it("shows the message and fix for a warning-only pass state, unlike a plain ready pass", async () => {
      mockEngineEndpoints({
        preflight: preflightWith([
          READINESS({
            message: "reviewer_credential: the reviewer credential expires on 2031-02-03",
            remedy: "store a reviewer credential for acme/widget",
          }),
        ]),
      });
      render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
      expect(await screen.findByTestId("engine-ops-preflight-readiness-status")).toHaveTextContent(
        "Dispatch ready",
      );
      const message = screen.getByTestId("engine-ops-preflight-readiness-message");
      expect(message).toHaveTextContent("expires on 2031-02-03");
      expect(message).toHaveTextContent("Fix: store a reviewer credential for acme/widget");
    });

    it("shows no readiness row when the saved preflight carries no readiness finding", async () => {
      mockEngineEndpoints({
        preflight: preflightWith([
          { check: "repo_clonable", status: "pass", message: "ok", remedy: "", remedy_url: "" },
        ]),
      });
      render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
      expect(await screen.findByTestId("engine-ops-preflight-outcome")).toBeInTheDocument();
      expect(screen.queryByTestId("engine-ops-preflight-readiness")).not.toBeInTheDocument();
    });

    it("shows no readiness row before a preflight has run", async () => {
      mockEngineEndpoints();
      render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
      expect(await screen.findByTestId("engine-ops-preflight-never-run")).toBeInTheDocument();
      expect(screen.queryByTestId("engine-ops-preflight-readiness")).not.toBeInTheDocument();
    });
  });

  it("renders the opted-out, never-run empty state for the dependency sweep tile by default", async () => {
    mockEngineEndpoints();
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-dep-sweep-status")).toHaveTextContent(
      "Opted out",
    );
    expect(screen.getByTestId("engine-ops-dep-sweep-last-run")).toHaveTextContent("Never run");
    expect(screen.getByTestId("engine-ops-dep-sweep-next-run")).toHaveTextContent(
      "Not scheduled",
    );
    expect(screen.getByTestId("engine-ops-dep-sweep-toggle-button")).toHaveTextContent("Opt in");
  });

  it("renders opted-in state with last and next run timing when present", async () => {
    mockEngineEndpoints({
      depSweep: () =>
        HttpResponse.json({
          slug: ENGINE_KEY,
          enabled: true,
          last_run_at: "2026-07-20T00:00:00Z",
          next_run_at: "2026-07-27T09:00:00Z",
        }),
    });
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-dep-sweep-status")).toHaveTextContent(
      "Opted in",
    );
    expect(screen.getByTestId("engine-ops-dep-sweep-last-run")).not.toHaveTextContent(
      "Never run",
    );
    expect(screen.getByTestId("engine-ops-dep-sweep-next-run")).not.toHaveTextContent(
      "Not scheduled",
    );
    expect(screen.getByTestId("engine-ops-dep-sweep-toggle-button")).toHaveTextContent(
      "Opt out",
    );
  });

  it("degrades the dependency sweep tile inline on fetch failure without blanking the other tiles", async () => {
    mockEngineEndpoints({
      depSweep: () => HttpResponse.json({ detail: "boom" }, { status: 500 }),
    });
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-dep-sweep-error")).toHaveTextContent("500");
    expect(await screen.findByTestId("engine-ops-identity")).toHaveTextContent("acme/widget");
    expect(await screen.findByTestId("engine-ops-pool-warm")).toHaveTextContent("Warm");
  });

  it("toggling the dependency sweep opt-in posts to the toggle endpoint and disables the button while in flight", async () => {
    mockEngineEndpoints();
    let resolvePost!: () => void;
    const postPromise = new Promise<void>((resolve) => {
      resolvePost = resolve;
    });
    server.use(
      http.post("/api/engines/acme/widget/dependency-sweep", async () => {
        await postPromise;
        return HttpResponse.json({
          repo: { owner: "acme", repo: "widget" },
          dependency_sweep: { enabled: true, cadence_seconds: 604800, enable_python_vuln_scan: false },
        });
      }),
    );
    const user = userEvent.setup();
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    const button = await screen.findByTestId("engine-ops-dep-sweep-toggle-button");
    await user.click(button);
    expect(button).toBeDisabled();
    resolvePost();
    await waitFor(() => expect(button).not.toBeDisabled());
  });

  it("surfaces the actionable Finding message and remedy when the toggle is refused with a 422", async () => {
    mockEngineEndpoints();
    server.use(
      http.post("/api/engines/acme/widget/dependency-sweep", () =>
        HttpResponse.json(
          {
            detail: {
              check: "dependabot_config_present",
              status: "fail",
              message: ".github/dependabot.yml not found in acme/widget",
              remedy: "add a .github/dependabot.yml file to the repository before enabling",
              remedy_url: "",
            },
          },
          { status: 422 },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    await user.click(await screen.findByTestId("engine-ops-dep-sweep-toggle-button"));
    expect(await screen.findByTestId("engine-ops-dep-sweep-finding-message")).toHaveTextContent(
      "dependabot.yml not found",
    );
    expect(screen.getByTestId("engine-ops-dep-sweep-finding-remedy")).toHaveTextContent(
      "add a .github/dependabot.yml file",
    );
    expect(screen.getByTestId("engine-ops-dep-sweep-finding")).toHaveAttribute("role", "alert");
  });

  it("surfaces a generic error when the toggle fails for a non-Finding reason", async () => {
    mockEngineEndpoints();
    server.use(
      http.post("/api/engines/acme/widget/dependency-sweep", () =>
        HttpResponse.json({ detail: "control token mismatch" }, { status: 403 }),
      ),
    );
    const user = userEvent.setup();
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    await user.click(await screen.findByTestId("engine-ops-dep-sweep-toggle-button"));
    expect(await screen.findByTestId("engine-ops-dep-sweep-toggle-error")).toHaveTextContent(
      "control token mismatch",
    );
    expect(screen.queryByTestId("engine-ops-dep-sweep-finding")).not.toBeInTheDocument();
  });

  it("degrades the posture tile inline on fetch failure without blanking the other tiles", async () => {
    mockEngineEndpoints({
      posture: () => HttpResponse.json({ detail: "boom" }, { status: 500 }),
    });
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-posture-error")).toHaveTextContent("500");
    expect(await screen.findByTestId("engine-ops-pool-warm")).toHaveTextContent("Warm");
    expect(await screen.findByTestId("engine-ops-preflight-never-run")).toBeInTheDocument();
  });

  it("degrades the preflight tile inline on fetch failure without blanking the other tiles", async () => {
    mockEngineEndpoints({
      preflight: () => HttpResponse.json({ detail: "boom" }, { status: 500 }),
    });
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-preflight-error")).toHaveTextContent("500");
    expect(await screen.findByTestId("engine-ops-identity")).toHaveTextContent("acme/widget");
    expect(await screen.findByTestId("engine-ops-pool-warm")).toHaveTextContent("Warm");
  });

  it("degrades the pool tile inline when there is no active pool, independent of the other tiles", async () => {
    mockEngineEndpoints({
      pool: () => HttpResponse.json({ detail: "no pool active for this engine" }, { status: 404 }),
    });
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-pool-error")).toHaveTextContent(
      "no pool active",
    );
    expect(await screen.findByTestId("engine-ops-identity")).toHaveTextContent("acme/widget");
  });

  it("opens the posture confirm modal from the Change posture button", async () => {
    mockEngineEndpoints();
    const user = userEvent.setup();
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    await user.click(await screen.findByTestId("engine-ops-posture-change-button"));
    expect(screen.getByTestId("posture-confirm-dialog")).toBeInTheDocument();
  });

  it("closes the posture confirm modal on Cancel", async () => {
    mockEngineEndpoints();
    const user = userEvent.setup();
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    await user.click(await screen.findByTestId("engine-ops-posture-change-button"));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("posture-confirm-dialog")).not.toBeInTheDocument();
  });

  it("shows no CI/deploy issues by default when health state is unavailable", async () => {
    mockEngineEndpoints();
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-ci-deploy-healthy")).toBeInTheDocument();
    expect(screen.queryByTestId("engine-ops-ci-deploy-as-of")).not.toBeInTheDocument();
  });

  it("shows matching CI/deploy findings scoped to this engine, labelled with the as-of sweep time", async () => {
    mockEngineEndpoints();
    const healthState: HealthState = {
      verdict: "degraded",
      blocked_count: 1,
      in_flight_count: 0,
      findings: [
        {
          finding_id: "main-ci-1",
          kind: "main_ci",
          severity: "high",
          description: "Main CI is red",
          workspace_id: null,
          pr_number: null,
          blocker_flags: [],
          action: "deep_link",
          engine_key: ENGINE_KEY,
          in_flight: false,
        },
        {
          finding_id: "other-engine",
          kind: "main_ci",
          severity: "high",
          description: "Different engine's CI is red",
          workspace_id: null,
          pr_number: null,
          blocker_flags: [],
          action: "deep_link",
          engine_key: "other/engine",
          in_flight: false,
        },
        {
          finding_id: "unrelated-kind",
          kind: "build_task",
          severity: "critical",
          description: "Not a CI/deploy finding",
          workspace_id: null,
          pr_number: null,
          blocker_flags: [],
          action: "deep_link",
          engine_key: ENGINE_KEY,
          in_flight: false,
        },
      ],
      computed_at: "2026-07-22T00:00:00Z",
    };
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, {
      wrapper: healthWrapper(healthState, "2026-07-22T00:00:00Z"),
    });
    expect(await screen.findByTestId("engine-ops-ci-deploy-finding-main-ci-1")).toHaveTextContent(
      "Main CI is red",
    );
    expect(screen.queryByTestId("engine-ops-ci-deploy-finding-other-engine")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("engine-ops-ci-deploy-finding-unrelated-kind"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("engine-ops-ci-deploy-as-of")).toBeInTheDocument();
    expect(screen.queryByTestId("engine-ops-ci-deploy-healthy")).not.toBeInTheDocument();
  });

  it("renders the overall readiness badge and an Open setup link to the shared step-3 route once the onboarding fetch resolves", async () => {
    mockEngineEndpoints();
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-onboarding-overall")).toHaveTextContent(
      "Ready to dispatch",
    );
    expect(await screen.findByTestId("engine-ops-onboarding-setup-link")).toHaveAttribute(
      "href",
      "/connect/acme/widget/dispatchable",
    );
  });

  it("fails fast instead of emitting a corrupted /connect link when engineKey has no owner/repo separator", () => {
    expect(() =>
      render(<EngineOpsPanel engineKey="malformed" />, { wrapper: MemoryRouter }),
    ).toThrow("invalid engineKey: malformed");
  });

  it("shows 'Blocked' for a fail overall verdict", async () => {
    mockEngineEndpoints({
      onboarding: () =>
        HttpResponse.json({ state: DEFAULT_ONBOARDING.state, checklist: [], overall: "fail" }),
    });
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-onboarding-overall")).toHaveTextContent("Blocked");
  });

  it("shows 'Partially ready' for an unknown overall verdict", async () => {
    mockEngineEndpoints({
      onboarding: () =>
        HttpResponse.json({ state: DEFAULT_ONBOARDING.state, checklist: [], overall: "unknown" }),
    });
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-onboarding-overall")).toHaveTextContent(
      "Partially ready",
    );
  });

  it("degrades the onboarding tile inline on fetch failure without blanking the other tiles", async () => {
    mockEngineEndpoints({
      onboarding: () =>
        HttpResponse.json({ detail: "engine has no forge configured" }, { status: 422 }),
    });
    render(<EngineOpsPanel engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-onboarding-error")).toHaveTextContent("422");
    expect(await screen.findByTestId("engine-ops-identity")).toHaveTextContent("acme/widget");
    expect(await screen.findByTestId("engine-ops-pool-warm")).toHaveTextContent("Warm");
  });

  it("stays rendered with an aria-busy tile and a muted Refreshing… label instead of dropping to Loading… when the engines bucket bumps", async () => {
    mockEngineEndpoints();
    let resolveSecond!: () => void;
    const secondGate = new Promise<void>((resolve) => {
      resolveSecond = resolve;
    });
    let callCount = 0;
    server.use(
      http.get("/api/engines/acme/widget", async () => {
        callCount += 1;
        if (callCount === 2) await secondGate;
        return HttpResponse.json(DEFAULT_POSTURE);
      }),
    );
    const view = renderWithEvents(0);
    expect(await screen.findByTestId("engine-ops-posture-pill")).toHaveTextContent("Propose");

    view.rerender(
      <ServerEventsContext.Provider value={eventsValue(1)}>
        <EngineOpsPanel engineKey={ENGINE_KEY} />
      </ServerEventsContext.Provider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("engine-ops-posture-tile")).toHaveAttribute("aria-busy", "true");
    });
    expect(screen.getByTestId("engine-ops-posture-tile-refreshing")).toBeInTheDocument();
    expect(screen.getByTestId("engine-ops-posture-pill")).toHaveTextContent("Propose");
    expect(screen.queryByTestId("engine-ops-posture-loading")).not.toBeInTheDocument();

    resolveSecond();
    await waitFor(() => {
      expect(screen.getByTestId("engine-ops-posture-tile")).toHaveAttribute("aria-busy", "false");
    });
    expect(screen.queryByTestId("engine-ops-posture-tile-refreshing")).not.toBeInTheDocument();
  });

  it("keeps the previously rendered posture pill and shows an inline muted refresh-failed line, never text-btn-destroy or the full-panel error, when only the refetch fails", async () => {
    mockEngineEndpoints();
    let callCount = 0;
    server.use(
      http.get("/api/engines/acme/widget", () => {
        callCount += 1;
        if (callCount === 1) return HttpResponse.json(DEFAULT_POSTURE);
        return HttpResponse.json({ detail: "refresh boom" }, { status: 500 });
      }),
    );
    const view = renderWithEvents(0);
    expect(await screen.findByTestId("engine-ops-posture-pill")).toHaveTextContent("Propose");

    view.rerender(
      <ServerEventsContext.Provider value={eventsValue(1)}>
        <EngineOpsPanel engineKey={ENGINE_KEY} />
      </ServerEventsContext.Provider>,
    );

    const refreshErrorLine = await screen.findByTestId("engine-ops-posture-tile-refresh-error");
    expect(refreshErrorLine).toHaveTextContent("500");
    expect(refreshErrorLine.className).not.toContain("text-btn-destroy");
    expect(screen.getByTestId("engine-ops-posture-pill")).toHaveTextContent("Propose");
    expect(screen.queryByTestId("engine-ops-posture-error")).not.toBeInTheDocument();
  });

  it("snapshots the posture the modal opened on so a background posture refresh cannot mutate an open confirmation", async () => {
    mockEngineEndpoints();
    let callCount = 0;
    server.use(
      http.get("/api/engines/acme/widget", () => {
        callCount += 1;
        return HttpResponse.json({
          ...DEFAULT_POSTURE,
          posture: callCount === 1 ? "propose" : "autonomous",
        });
      }),
    );
    const user = userEvent.setup();
    const view = renderWithEvents(0);
    await user.click(await screen.findByTestId("engine-ops-posture-change-button"));
    expect(screen.getByTestId("posture-confirm-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("posture-confirm-submit")).toBeDisabled();

    view.rerender(
      <ServerEventsContext.Provider value={eventsValue(1)}>
        <EngineOpsPanel engineKey={ENGINE_KEY} />
      </ServerEventsContext.Provider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("engine-ops-posture-pill")).toHaveTextContent("Autonomous");
    });
    expect(screen.getByTestId("posture-confirm-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("posture-confirm-submit")).toBeDisabled();
  });
});

describe("PostureTile — standalone", () => {
  it("renders with only engineKey, independent of any project", async () => {
    mockEngineEndpoints();
    render(<PostureTile engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-posture-pill")).toHaveTextContent("Propose");
  });
});

describe("DependencySweepTile — standalone", () => {
  it("renders with only engineKey, independent of any project", async () => {
    mockEngineEndpoints();
    render(<DependencySweepTile engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    expect(await screen.findByTestId("engine-ops-dep-sweep-status")).toHaveTextContent(
      "Opted out",
    );
  });

  it("keeps the DependencySweepBlockedError branch reachable standalone", async () => {
    mockEngineEndpoints();
    server.use(
      http.post("/api/engines/acme/widget/dependency-sweep", () =>
        HttpResponse.json(
          {
            detail: {
              check: "dependabot_config_present",
              status: "fail",
              message: ".github/dependabot.yml not found in acme/widget",
              remedy: "add a .github/dependabot.yml file to the repository before enabling",
              remedy_url: "",
            },
          },
          { status: 422 },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<DependencySweepTile engineKey={ENGINE_KEY} />, { wrapper: MemoryRouter });
    await user.click(await screen.findByTestId("engine-ops-dep-sweep-toggle-button"));
    expect(await screen.findByTestId("engine-ops-dep-sweep-finding-message")).toHaveTextContent(
      "dependabot.yml not found",
    );
    expect(screen.getByTestId("engine-ops-dep-sweep-finding")).toHaveAttribute("role", "alert");
  });
});
