import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { api, type ServerEventsHandlers } from "./api";
import { writeProjectFeaturesOn } from "./projectFeatures";
import { TOP_BAR_Z_CLASS } from "./shell/stickyOffsets";
import { server } from "./test/server";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  writeProjectFeaturesOn(true);
  server.use(
    http.get("/api/engines", () =>
      HttpResponse.json({
        engines: [
          { repo: { owner: "DejvMatikar", repo: "alan" }, posture: "autonomous" },
          { repo: { owner: "acme", repo: "widget" }, posture: "observe" },
        ],
      }),
    ),
    http.get(/\/api\/engines\/[^/]+\/[^/]+\/onboarding$/, () =>
      HttpResponse.json({ detail: "engine has no forge configured" }, { status: 422 }),
    ),
    http.get("/api/workspaces", () => HttpResponse.json([])),
    http.get("/api/projects", () => HttpResponse.json([])),
  );
});

describe("App shell — top bar", () => {
  it("renders the repo switcher on the repo page, with no brand wordmark link", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    expect(await screen.findByTestId("top-bar-repo-acme/widget")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "alan" })).not.toBeInTheDocument();
  });

  it("the header sits on the bg-surface token, not bg-surface-elevated", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    const banner = screen.getByRole("banner");
    expect(banner.className).toContain("bg-surface");
    expect(banner.className).not.toContain("bg-surface-elevated");
  });

  it("carries no bottom border — the top bar and page share one continuous surface", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    const banner = screen.getByRole("banner");
    expect(banner.className).not.toContain("border-b");
    expect(banner.className).not.toContain("border-border");
  });

  it("locks the header to the viewport top at sm and up, with a stable height for the sticky offset", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    const banner = screen.getByRole("banner");
    expect(banner.className).toContain("sm:sticky");
    expect(banner.className).toContain("sm:top-0");
    const innerRow = banner.querySelector("div");
    expect(innerRow?.className).toContain("sm:h-16");
    expect(innerRow?.className).toContain("sm:flex-nowrap");
  });

  it("uses the shared top-bar z-index layer constant so it stays above every other pinned page region", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    expect(screen.getByRole("banner").className).toContain(TOP_BAR_Z_CLASS);
  });

  it("does not wrap a fixed-height overflow-hidden frame around the shared main content on a non-Home route", async () => {
    window.history.pushState({}, "", "/health");
    render(<App />);
    await screen.findByRole("heading", { name: "System Health" });
    const main = document.querySelector("main");
    expect(main).not.toBeNull();
    expect(main?.className).not.toContain("overflow-hidden");
    expect(main?.className).not.toMatch(/\bh-\[/);
  });

  it("top bar right cluster is ordered: commands, projects toggle, hidden-repos trigger, health, settings", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    const banner = screen.getByRole("banner");
    const testIds = [
      "command-palette-trigger",
      "project-features-toggle",
      "hidden-repo-dialog-trigger",
      "nav-health-badge",
      "settings-gear",
    ];
    const nodes = testIds.map((id) => within(banner).getByTestId(id));
    for (let i = 0; i < nodes.length - 1; i += 1) {
      expect(
        nodes[i]!.compareDocumentPosition(nodes[i + 1]!) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });

  it.each(["/repos/acme/widget", "/health", "/build"])(
    "shows the Commands trigger in the top bar on %s",
    async (path) => {
      window.history.pushState({}, "", path);
      render(<App />);
      expect(await within(screen.getByRole("banner")).findByTestId("command-palette-trigger")).toBeVisible();
    },
  );

  it("Tab from the Commands trigger lands on the projects toggle", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    const user = userEvent.setup();
    screen.getByTestId("command-palette-trigger").focus();
    await user.tab();
    expect(screen.getByTestId("project-features-toggle")).toHaveFocus();
  });

  it("activating the Commands trigger opens the command palette at its root list and Escape returns focus to it", async () => {
    window.history.pushState({}, "", "/health");
    render(<App />);
    await screen.findByRole("heading", { name: "System Health" });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("command-palette-trigger"));
    expect(screen.getByTestId("palette-command-build")).toBeInTheDocument();
    expect(screen.getByTestId("palette-command-explore")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
    expect(screen.getByTestId("command-palette-trigger")).toHaveFocus();
  });

  it("Tab from the projects toggle lands on the hidden-repos trigger", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    const user = userEvent.setup();
    screen.getByTestId("project-features-toggle").focus();
    await user.tab();
    expect(screen.getByTestId("hidden-repo-dialog-trigger")).toHaveFocus();
  });

  it("toggling project features off on a project page redirects home and keeps focus on the toggle", async () => {
    window.history.pushState({}, "", "/projects");
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Projects" });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("project-features-toggle"));
    await screen.findByTestId("repo-page");
    const toggle = screen.getByTestId("project-features-toggle");
    expect(window.location.pathname).not.toBe("/projects");
    expect(toggle).toHaveFocus();
    expect(toggle).toHaveAttribute("aria-pressed", "false");
  });

  it("toggling project features off on a repository page leaves the location unchanged", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("project-features-toggle"));
    expect(screen.getByTestId("project-features-toggle")).toHaveAttribute("aria-pressed", "false");
    expect(window.location.pathname).toBe("/repos/acme/widget");
  });

  it("places the list-adjacent connect-repo CTA immediately after the repository list, outside its nav landmark", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    const nav = await screen.findByTestId("top-bar-repo-switcher");
    const cta = screen.getByTestId("connect-repo-cta");
    expect(within(nav).queryByTestId("connect-repo-cta")).not.toBeInTheDocument();
    expect(nav.compareDocumentPosition(cta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders the Repo page on /repos/:owner/:repo", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    expect(await screen.findByTestId("repo-page")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { level: 1, name: "Widget" })).toBeInTheDocument();
  });

  it("redirects / into the repo-scoped shell", async () => {
    window.history.pushState({}, "", "/");
    render(<App />);
    expect(await screen.findByTestId("repo-page")).toBeInTheDocument();
  });

  it("renders the RepoEngineConfig page on /repos/:owner/:repo/config", async () => {
    server.use(
      http.get("/api/engines/repo-app-co/widget", () =>
        HttpResponse.json({
          repo: { owner: "repo-app-co", repo: "widget" },
          code_quality: null,
          forge: null,
          ci: null,
          roles: {},
          gate_commands: [],
          warmup_commands: [],
          base_tools: [],
          team_tools: {},
          transition_graph: {},
          flow_policy: null,
          posture: "observe",
          pool_size: null,
          preset: null,
          prompt_overrides: {},
        }),
      ),
      http.get("/api/engines/repo-app-co/widget/dependency-sweep", () =>
        HttpResponse.json({
          slug: "repo-app-co/widget",
          enabled: false,
          last_run_at: null,
          next_run_at: null,
        }),
      ),
    );
    window.history.pushState({}, "", "/repos/repo-app-co/widget/config");
    render(<App />);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Engine configuration" }),
    ).toBeInTheDocument();
  });
});

describe("App shell — nav badges and settings", () => {
  it("renders nav-health-badge in the header banner on a repo page", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    expect(within(screen.getByRole("banner")).getByTestId("nav-health-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("health-indicator")).not.toBeInTheDocument();
  });

  it("renders the HealthView on /health", async () => {
    window.history.pushState({}, "", "/health");
    render(<App />);
    await screen.findByRole("heading", { name: "System Health" });
  });

  it("redirects the retired /inbox route to Health via history.replace, leaving no /inbox history entry", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    window.history.pushState({}, "", "/inbox");
    render(<App />);
    await screen.findByRole("heading", { name: "System Health" });
    expect(window.location.pathname).toBe("/health");
    window.history.back();
    await waitFor(() => {
      expect(window.location.pathname).toBe("/repos/acme/widget");
    });
  });

  it("redirects /inbox?engine=x to Health without forwarding the engine param", async () => {
    window.history.pushState({}, "", "/inbox?engine=acme%2Fwidget");
    render(<App />);
    await screen.findByRole("heading", { name: "System Health" });
    expect(window.location.pathname).toBe("/health");
    expect(window.location.search).toBe("");
  });

  it("renders the settings gear in the header with an accessible name and tap-target sizing", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    const gear = screen.getByTestId("settings-gear");
    expect(gear).toHaveAccessibleName("Global settings");
    expect(gear.className).toContain("min-h-11");
    expect(gear.className).toContain("min-w-11");
  });

  it("wires the settings gear's hover-revealed description via aria-describedby for assistive technology", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    const gear = screen.getByTestId("settings-gear");
    const describedBy = gear.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy ?? "")).toHaveTextContent(
      "App-wide preferences: control token, theme, and other global settings.",
    );
  });

  it("opens the settings modal when the gear is clicked, and Escape closes it", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("settings-gear"));
    const modal = screen.getByTestId("settings-modal");
    expect(modal).toBeInTheDocument();

    const container = modal.querySelector("div");
    if (container === null) throw new Error("modal container div not found");
    fireEvent.keyDown(container, { key: "Escape" });
    expect(screen.queryByTestId("settings-modal")).not.toBeInTheDocument();
  });

  it("a child rendered under Shell (the repo dashboard's needs-you host-setup line) can open the Settings modal through the shared context", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    const user = userEvent.setup();
    const hostLine = await screen.findByTestId("needs-you-host-setup");
    await user.click(hostLine);
    expect(screen.getByTestId("settings-modal")).toBeInTheDocument();
  });

  it("renders ConnectWizard on the three-segment /connect/:owner/:repo/:step override route", async () => {
    server.use(
      http.get("/api/engines/acme/widget", () => HttpResponse.json({ detail: "not found" }, { status: 404 })),
      http.get("/api/secrets", () => HttpResponse.json({ secrets: [] })),
    );
    window.history.pushState({}, "", "/connect/acme/widget/dispatchable");
    render(<App />);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Connect a repository" }),
    ).toBeInTheDocument();
    expect(await screen.findByTestId("wizard-rail-step-dispatchable")).toHaveAttribute(
      "aria-current",
      "step",
    );
  });

  it("renders the BuildChat page on /build", async () => {
    server.use(
      http.post("/api/build-chat/sessions", () =>
        HttpResponse.json({ session_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" }, { status: 201 }),
      ),
      http.get("/api/build-chat/sessions/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", () =>
        HttpResponse.json({
          session_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
          created_at: "now",
          messages: [],
          in_flight_turn: null,
          brief: null,
        }),
      ),
      http.get("/api/build-chat/sessions/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/events", () =>
        new HttpResponse("", { headers: { "Content-Type": "text/event-stream" } }),
      ),
    );
    window.history.pushState({}, "", "/build?engine=acme%2Fwidget");
    render(<App />);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Build" }),
    ).toBeInTheDocument();
  });

  it("the command palette opens with Ctrl+K from a route other than Home, and closes on the dialog's cancel event", async () => {
    window.history.pushState({}, "", "/health");
    render(<App />);
    await screen.findByRole("heading", { name: "System Health" });
    fireEvent.keyDown(document, { code: "KeyK", ctrlKey: true });
    const palette = screen.getByTestId("command-palette");
    expect(palette).toBeInTheDocument();
    fireEvent(palette, new Event("cancel"));
    expect(screen.queryByTestId("command-palette")).not.toBeInTheDocument();
  });
});

describe("App shell — dispatch readiness banner", () => {
  const HEALTH = { ok: true, queue_paused: false, paused_until: null, health_state: null };
  const ISSUE = {
    engine: "acme/widget",
    requirement: "reviewer_credential",
    state: "not_ready",
    message: "no reviewer credential is stored",
    fix: "store a reviewer credential for acme/widget",
  };

  function serveHealth(body: Record<string, unknown>) {
    server.use(http.get("/api/health", () => HttpResponse.json({ ...HEALTH, ...body })));
  }

  it("mounts app-wide, outside the header and the routed page, on a repo page", async () => {
    serveHealth({ dispatch_readiness: [ISSUE] });
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    const banner = await screen.findByTestId("dispatch-readiness-banner");
    expect(banner).toHaveTextContent("Dispatch held");
    expect(banner).toHaveTextContent("store a reviewer credential for acme/widget");
    expect(screen.getByRole("banner")).not.toContainElement(banner);
    expect(screen.getByRole("main")).not.toContainElement(banner);
  });

  it("mounts on a non-repo route too", async () => {
    serveHealth({ dispatch_readiness: [{ ...ISSUE, state: "unknown" }] });
    window.history.pushState({}, "", "/health");
    render(<App />);
    expect(await screen.findByTestId("dispatch-readiness-banner")).toHaveTextContent(
      "Dispatch readiness unverified",
    );
  });

  it("sits directly after the queue-paused banner", async () => {
    serveHealth({
      queue_paused: true,
      paused_until: Date.now() / 1000 + 3600,
      dispatch_readiness: [ISSUE],
    });
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    const queueBanner = await screen.findByTestId("queue-paused-banner");
    const readinessBanner = await screen.findByTestId("dispatch-readiness-banner");
    expect(queueBanner.nextElementSibling).toBe(readinessBanner);
  });

  it("renders nothing while the queue banner shows and every engine is ready", async () => {
    serveHealth({
      queue_paused: true,
      paused_until: Date.now() / 1000 + 3600,
      dispatch_readiness: [],
    });
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("queue-paused-banner");
    expect(screen.queryByTestId("dispatch-readiness-banner")).not.toBeInTheDocument();
  });
});

function workspaceDetailHandler(engineKey: string | null = null) {
  return http.get("/api/workspaces/my-task", () =>
    HttpResponse.json({
      task_id: "my-task",
      title: "My Task",
      description: "",
      status: "active",
      revisit_counts: {},
      created_at: "c",
      updated_at: "u",
      history: [],
      latest_handover: null,
      tasks: [],
      scope_audit: [],
      total_cost_usd: null,
      engine_key: engineKey,
    }),
  );
}

const WORKSPACE_DETAIL_HANDLERS = [
  workspaceDetailHandler(),
  http.get("/api/workspaces/my-task/events", () => HttpResponse.error()),
];

function prepChatHandler() {
  return http.get("/api/workspaces/my-task/prep-chat", () =>
    HttpResponse.json({
      session_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      kind: "build",
      consultant_key: null,
      title: "My Task",
      created_at: "c",
      messages: [{ role: "user", content: "build it", ts: "c" }],
    }),
  );
}

it("renders the Workspace detail page on /workspaces/:taskId", async () => {
  server.use(...WORKSPACE_DETAIL_HANDLERS);
  window.history.pushState({}, "", "/workspaces/my-task");
  render(<App />);
  expect(
    await screen.findByRole("heading", { level: 1, name: "My Task" }),
  ).toBeInTheDocument();
});

it("renders the WorkspacePrepChat page on /workspaces/:taskId/prep-chat", async () => {
  server.use(prepChatHandler(), workspaceDetailHandler());
  window.history.pushState({}, "", "/workspaces/my-task/prep-chat");
  render(<App />);
  expect(await screen.findByTestId("prep-chat-kind")).toHaveTextContent("Build conversation");
  expect(screen.getByTestId("prep-chat-status")).toHaveTextContent("Archived — read-only");
});

describe.each([
  {
    name: "repo",
    path: "/repos/acme/widget",
    handlers: [],
    wait: () => screen.findByTestId("top-bar-repo-acme/widget"),
  },
  {
    name: "projects",
    path: "/projects",
    handlers: [],
    wait: () => screen.findByRole("heading", { level: 1, name: "Projects" }),
  },
  {
    name: "build",
    path: "/build",
    handlers: [],
    wait: () => screen.findByRole("heading", { level: 1, name: "Build" }),
  },
  {
    name: "health",
    path: "/health",
    handlers: [],
    wait: () => screen.findByRole("heading", { name: "System Health" }),
  },
  {
    name: "workspace detail",
    path: "/workspaces/my-task",
    handlers: WORKSPACE_DETAIL_HANDLERS,
    wait: () => screen.findByRole("heading", { level: 1, name: "My Task" }),
  },
])("App shell — nav-health-badge parity ($name)", ({ path, handlers, wait }) => {
  it("renders nav-health-badge in the header banner", async () => {
    server.use(...handlers);
    window.history.pushState({}, "", path);
    render(<App />);
    await wait();
    expect(within(screen.getByRole("banner")).getByTestId("nav-health-badge")).toBeInTheDocument();
  });
});

describe("App shell — project routes redirect home while showProjects is off", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it.each(["/projects", "/projects/some-id", "/projects/some-id/engine"])(
    "redirects %s away from the gated route",
    async (path) => {
      window.history.pushState({}, "", path);
      render(<App />);
      await screen.findByTestId("repo-page");
      expect(window.location.pathname).not.toBe(path);
    },
  );

  it("does not redirect /repos/:owner/:repo/config — it sits outside the project-features gate", async () => {
    server.use(
      http.get("/api/engines/repo-app-co/widget", () =>
        HttpResponse.json({
          repo: { owner: "repo-app-co", repo: "widget" },
          code_quality: null,
          forge: null,
          ci: null,
          roles: {},
          gate_commands: [],
          warmup_commands: [],
          base_tools: [],
          team_tools: {},
          transition_graph: {},
          flow_policy: null,
          posture: "observe",
          pool_size: null,
          preset: null,
          prompt_overrides: {},
        }),
      ),
      http.get("/api/engines/repo-app-co/widget/dependency-sweep", () =>
        HttpResponse.json({
          slug: "repo-app-co/widget",
          enabled: false,
          last_run_at: null,
          next_run_at: null,
        }),
      ),
    );
    window.history.pushState({}, "", "/repos/repo-app-co/widget/config");
    render(<App />);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Engine configuration" }),
    ).toBeInTheDocument();
    expect(window.location.pathname).toBe("/repos/repo-app-co/widget/config");
  });
});

describe("App shell — the engine list is one shared read", () => {
  const ENGINES_PAYLOAD = {
    engines: [
      { repo: { owner: "DejvMatikar", repo: "alan" }, posture: "autonomous" },
      { repo: { owner: "acme", repo: "widget" }, posture: "observe" },
    ],
  };
  let handlers: ServerEventsHandlers | null = null;
  const requests = { engines: 0, secrets: 0 };

  async function settle(): Promise<void> {
    await act(async () => {
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 50);
      });
    });
  }

  async function renderRepoPage(): Promise<void> {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    await waitFor(() => {
      expect(requests.secrets).toBeGreaterThan(0);
    });
    await settle();
  }

  beforeEach(() => {
    handlers = null;
    requests.engines = 0;
    requests.secrets = 0;
    vi.spyOn(api, "subscribeEvents").mockImplementation((h: ServerEventsHandlers) => {
      handlers = h;
      return () => undefined;
    });
    server.use(
      http.get("/api/engines", () => {
        requests.engines += 1;
        return HttpResponse.json(ENGINES_PAYLOAD);
      }),
      http.get("/api/secrets", () => {
        requests.secrets += 1;
        return HttpResponse.json({ secrets: [] });
      }),
    );
  });

  it("issues exactly one engine-list request on load with every shell and page consumer mounted", async () => {
    await renderRepoPage();
    expect(requests.engines).toBe(1);
  });

  it("opening the command palette mounts a late consumer without a request or a new connect-state fan-out", async () => {
    await renderRepoPage();
    const before = { ...requests };
    fireEvent.keyDown(document, { code: "KeyK", ctrlKey: true });
    expect(screen.getByTestId("command-palette")).toBeInTheDocument();
    await settle();
    expect(requests).toEqual(before);
  });

  it("an engines event whose payload is byte-identical still re-runs the connect-state fan-out", async () => {
    await renderRepoPage();
    const before = { ...requests };
    act(() => {
      handlers?.onChanged(["engines"]);
    });
    await waitFor(() => {
      expect(requests.secrets).toBeGreaterThan(before.secrets);
    });
    expect(requests.engines).toBe(before.engines + 1);
  });

  it("a workspace-list event refetches neither the engine list nor the fan-out", async () => {
    await renderRepoPage();
    const before = { ...requests };
    act(() => {
      handlers?.onChanged(["workspace_list"]);
    });
    await settle();
    expect(requests).toEqual(before);
  });
});

describe("App shell — Build/Explore shortcuts act on the selected repository", () => {
  it("Ctrl+Shift+X opens the palette's repository chooser for Build with no repository selected", async () => {
    window.history.pushState({}, "", "/health");
    render(<App />);
    await screen.findByRole("heading", { name: "System Health" });
    fireEvent.keyDown(document, { code: "KeyX", ctrlKey: true, shiftKey: true });
    expect(screen.getByTestId("command-palette")).toHaveAccessibleName(
      "Command palette — choose a repository to build",
    );
    expect(window.location.pathname).toBe("/health");
  });

  it("Ctrl+Shift+E opens the palette's consultant chooser for Explore with no repository selected", async () => {
    window.history.pushState({}, "", "/health");
    render(<App />);
    await screen.findByRole("heading", { name: "System Health" });
    fireEvent.keyDown(document, { code: "KeyE", ctrlKey: true, shiftKey: true });
    expect(screen.getByTestId("command-palette")).toHaveAccessibleName(
      "Command palette — choose a consultant",
    );
    expect(screen.queryByTestId("consultant-modal")).not.toBeInTheDocument();
  });

  it("the palette lists Build in and Explore for the repository the page is on", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("command-palette-trigger"));
    expect(await screen.findByTestId("palette-command-build-scoped")).toHaveTextContent("Build in Widget");
    expect(screen.getByTestId("palette-command-explore-scoped")).toHaveTextContent("Explore Widget");
  });

  it("choosing Build in the repository from the palette lands on that repository's build page", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("command-palette-trigger"));
    await user.click(await screen.findByTestId("palette-command-build-scoped"));
    expect(await screen.findByRole("heading", { level: 1, name: "Build" })).toBeInTheDocument();
    expect(new URL(window.location.href).searchParams.get("engine")).toBe("acme/widget");
  });

  it("the palette offers no repository-scoped commands on a page with no repository", async () => {
    window.history.pushState({}, "", "/health");
    render(<App />);
    await screen.findByRole("heading", { name: "System Health" });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("command-palette-trigger"));
    expect(screen.getByTestId("palette-command-build")).toBeInTheDocument();
    expect(screen.queryByTestId("palette-command-build-scoped")).not.toBeInTheDocument();
  });

  it("Ctrl+Shift+X on a workspace's detail page opened directly starts a build in that workspace's repository", async () => {
    server.use(workspaceDetailHandler("acme/widget"), WORKSPACE_DETAIL_HANDLERS[1]!);
    window.history.pushState({}, "", "/workspaces/my-task");
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "My Task" });
    await waitFor(() => {
      expect(screen.getByTestId("top-bar-repo-acme/widget")).toHaveAttribute("aria-current", "true");
    });
    fireEvent.keyDown(document, { code: "KeyX", ctrlKey: true, shiftKey: true });
    expect(await screen.findByRole("heading", { level: 1, name: "Build" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/repos/acme/widget/build");
    expect(new URL(window.location.href).searchParams.get("engine")).toBe("acme/widget");
  });

  it("Ctrl+Shift+E on a workspace's detail page opens Explore for that workspace's repository", async () => {
    server.use(workspaceDetailHandler("acme/widget"), WORKSPACE_DETAIL_HANDLERS[1]!);
    window.history.pushState({}, "", "/workspaces/my-task");
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "My Task" });
    await waitFor(() => {
      expect(screen.getByTestId("top-bar-repo-acme/widget")).toHaveAttribute("aria-current", "true");
    });
    fireEvent.keyDown(document, { code: "KeyE", ctrlKey: true, shiftKey: true });
    expect(screen.getByTestId("consultant-modal")).toBeInTheDocument();
  });

  it("the palette on a workspace's preparation-chat page offers Build in that workspace's repository", async () => {
    server.use(prepChatHandler(), workspaceDetailHandler("acme/widget"));
    window.history.pushState({}, "", "/workspaces/my-task/prep-chat");
    render(<App />);
    await screen.findByTestId("prep-chat-kind");
    await waitFor(() => {
      expect(screen.getByTestId("top-bar-repo-acme/widget")).toHaveAttribute("aria-current", "true");
    });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("command-palette-trigger"));
    expect(screen.getByTestId("palette-command-build-scoped")).toHaveTextContent("Build in Widget");
  });

  it("Ctrl+Shift+X navigates to Build scoped to the selected repository", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    fireEvent.keyDown(document, { code: "KeyX", ctrlKey: true, shiftKey: true });
    expect(await screen.findByRole("heading", { level: 1, name: "Build" })).toBeInTheDocument();
    expect(new URL(window.location.href).searchParams.get("engine")).toBe("acme/widget");
  });

  it("Ctrl+Shift+E opens the Explore modal scoped to the selected repository", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    fireEvent.keyDown(document, { code: "KeyE", ctrlKey: true, shiftKey: true });
    expect(screen.getByTestId("consultant-modal")).toBeInTheDocument();
  });

  it("an unrelated Ctrl+Shift key is ignored even while scoped", async () => {
    window.history.pushState({}, "", "/repos/acme/widget");
    render(<App />);
    await screen.findByTestId("top-bar-repo-acme/widget");
    fireEvent.keyDown(document, { code: "KeyZ", ctrlKey: true, shiftKey: true });
    expect(screen.queryByTestId("consultant-modal")).not.toBeInTheDocument();
    expect(window.location.pathname).toBe("/repos/acme/widget");
  });
});
