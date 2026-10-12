import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { EngineConfig, EngineSource } from "../../../api";
import { makeSummary, summaryHandler } from "../../../test/agentFixtures";
import { deferredFetch } from "../../../test/docReadHarness";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  makeEngineConfig,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
} from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import RepoEngineConfig from "./RepoEngineConfig";

const OWNER = "acme";
const REPO = "widget";
const ENGINE_KEY = `${OWNER}/${REPO}`;

const CONFIG = makeEngineConfig({
  roles: { implementer: { model: "sonnet", effort: "xhigh" } },
  gate_commands: [["uv", "run", "pytest"]],
  base_tools: ["Read"],
  transition_graph: { research: ["architecture"] },
  posture: "propose",
});

function renderAt(owner = OWNER, repo = REPO) {
  return render(
    <MemoryRouter initialEntries={[`/repos/${owner}/${repo}/config`]}>
      <Routes>
        <Route path="/repos/:owner/:repo/config" element={<RepoEngineConfig />} />
      </Routes>
    </MemoryRouter>,
  );
}

function mockConfig(
  overrides: Partial<EngineConfig> = {},
  opts: { owner?: string; repo?: string; status?: number } = {},
) {
  const owner = opts.owner ?? OWNER;
  const repo = opts.repo ?? REPO;
  server.use(
    http.get(`/api/engines/${owner}/${repo}`, () =>
      opts.status !== undefined
        ? HttpResponse.json({ detail: "boom" }, { status: opts.status })
        : HttpResponse.json({ ...CONFIG, repo: { owner, repo }, ...overrides }),
    ),
  );
}

function mockDepSweep(owner = OWNER, repo = REPO) {
  server.use(
    http.get(`/api/engines/${owner}/${repo}/dependency-sweep`, () =>
      HttpResponse.json({ slug: `${owner}/${repo}`, enabled: false, last_run_at: null, next_run_at: null }),
    ),
  );
}

function mockSources(sources: EngineSource[]) {
  server.use(http.get("/api/engines/sources", () => HttpResponse.json({ sources })));
}

function mockEngineList(
  engines: { repo: { owner: string; repo: string }; posture: string; hidden?: boolean }[],
) {
  server.use(http.get("/api/engines", () => HttpResponse.json({ engines })));
}

describe("RepoEngineConfig — loading, error, empty", () => {
  it("stays in a loading state and never crashes when the route provides no owner/repo params", () => {
    render(
      <MemoryRouter initialEntries={["/repos-standalone"]}>
        <Routes>
          <Route path="/repos-standalone" element={<RepoEngineConfig />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("repo-engine-config-loading")).toBeInTheDocument();
  });

  it("shows a loading state before the config doc resolves", () => {
    mockDepSweep();
    server.use(http.get(`/api/engines/${OWNER}/${REPO}`, () => new Promise<Response>(() => undefined)));
    renderAt();
    expect(screen.getByTestId("repo-engine-config-loading")).toBeInTheDocument();
  });

  it("shows an error state with retry when the config fetch fails", async () => {
    mockDepSweep();
    mockConfig({}, { status: 500 });
    renderAt();
    expect(await screen.findByTestId("repo-engine-config-error")).toBeInTheDocument();
    expect(screen.getByTestId("repo-engine-config-retry")).toBeInTheDocument();
  });

  it("retry re-issues the doc fetch and can recover", async () => {
    mockDepSweep();
    mockConfig({}, { status: 500 });
    const user = userEvent.setup();
    renderAt();
    await screen.findByTestId("repo-engine-config-error");

    mockConfig();
    mockSources([]);
    await user.click(screen.getByTestId("repo-engine-config-retry"));
    await waitFor(() => {
      expect(screen.getByTestId("engine-config-readonly")).toBeInTheDocument();
    });
  });

  it("shows an empty state with a connect CTA when the engine has never been registered", async () => {
    mockDepSweep();
    mockConfig({}, { status: 404 });
    renderAt();
    expect(await screen.findByTestId("repo-engine-config-empty")).toBeInTheDocument();
    expect(screen.getByTestId("repo-engine-config-connect-cta")).toHaveAttribute(
      "href",
      `/connect/${OWNER}/${REPO}`,
    );
  });

  it("hides the live controls (posture, dependency sweep) when the engine has never been registered", async () => {
    mockDepSweep();
    mockConfig({}, { status: 404 });
    renderAt();
    await screen.findByTestId("repo-engine-config-empty");
    expect(screen.queryByTestId("repo-engine-config-live-controls")).not.toBeInTheDocument();
  });
});

describe("RepoEngineConfig — hidden repo notice", () => {
  it("shows the hidden-repo notice when this repo is currently hidden", async () => {
    mockConfig();
    mockDepSweep();
    mockSources([]);
    mockEngineList([{ repo: { owner: OWNER, repo: REPO }, posture: "observe", hidden: true }]);
    renderAt();
    expect(await screen.findByTestId("repo-engine-config-hidden-notice")).toBeInTheDocument();
  });

  it("shows no hidden-repo notice when this repo is not hidden", async () => {
    mockConfig();
    mockDepSweep();
    mockSources([]);
    mockEngineList([{ repo: { owner: OWNER, repo: REPO }, posture: "observe", hidden: false }]);
    renderAt();
    await screen.findByTestId("repo-engine-config-identity");
    expect(screen.queryByTestId("repo-engine-config-hidden-notice")).not.toBeInTheDocument();
  });
});

describe("RepoEngineConfig — default engine gets the full page", () => {
  it("renders the same full page for the default engine slug — no reduced variant", async () => {
    mockConfig({}, { owner: "DejvMatikar", repo: "alan" });
    mockDepSweep("DejvMatikar", "alan");
    mockSources([]);
    renderAt("DejvMatikar", "alan");
    expect(await screen.findByTestId("engine-config-readonly")).toBeInTheDocument();
    expect(screen.getByTestId("repo-engine-config-live-controls")).toBeInTheDocument();
    expect(screen.getByTestId("agent-catalogue-tile")).toBeInTheDocument();
  });
});

describe("RepoEngineConfig — hierarchy", () => {
  it("renders the agent catalogue tile with no <details> ancestor and no roles or prompt-overrides section", async () => {
    mockConfig();
    mockDepSweep();
    mockSources([]);
    renderAt();
    const tile = await screen.findByTestId("agent-catalogue-tile");
    expect(tile.closest("details")).toBeNull();
    expect(screen.queryByTestId("engine-config-readonly-roles")).not.toBeInTheDocument();
    expect(screen.queryByTestId("engine-config-prompt-overrides-disclosure")).not.toBeInTheDocument();
    expect(screen.queryByText("Prompt overrides")).not.toBeInTheDocument();
  });

  it("summarises the repository's agents by model and links to its catalogue", async () => {
    mockConfig();
    mockDepSweep();
    mockSources([]);
    server.use(summaryHandler(OWNER, REPO, makeSummary()));
    renderAt();
    expect(await screen.findByTestId("agent-catalogue-tile-models")).toHaveTextContent(
      "3 agents: 1 on opus, 2 on sonnet",
    );
    expect(screen.getByTestId("agent-catalogue-tile-link")).toHaveAttribute(
      "href",
      `/repos/${OWNER}/${REPO}/agents`,
    );
  });

  it("collapses forge/CI/code-quality and other plumbing into closed <details> groups", async () => {
    mockConfig();
    mockDepSweep();
    mockSources([]);
    renderAt();
    await screen.findByTestId("engine-config-readonly");
    for (const testId of [
      "engine-config-readonly-pool-preset-disclosure",
      "engine-config-readonly-commands-disclosure",
      "engine-config-readonly-tools-disclosure",
      "engine-config-readonly-roster-disclosure",
      "engine-config-readonly-forge-disclosure",
    ]) {
      const details = screen.getByTestId(testId);
      expect(details.tagName).toBe("DETAILS");
      expect(details).not.toHaveAttribute("open");
    }
  });
});

describe("RepoEngineConfig — live dials", () => {
  it("renders the live posture and dependency-sweep dials with no project bound", async () => {
    mockConfig();
    mockDepSweep();
    mockSources([]);
    renderAt();
    expect(await screen.findByTestId("engine-ops-posture-pill")).toBeInTheDocument();
    expect(screen.getByTestId("engine-ops-dep-sweep-status")).toBeInTheDocument();
  });

  it("serves the posture tile and the accent picker from one shared config read beside the document's own", async () => {
    mockDepSweep();
    mockSources([]);
    let getCalls = 0;
    server.use(
      http.get(`/api/engines/${OWNER}/${REPO}`, () => {
        getCalls += 1;
        return HttpResponse.json(CONFIG);
      }),
    );
    renderAt();
    await screen.findByTestId("engine-ops-posture-pill");
    await screen.findByTestId("accent-picker-swatches");
    await act(async () => {
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 25);
      });
    });
    expect(getCalls).toBe(2);
  });
});

describe("RepoEngineConfig — accent picker", () => {
  it("renders the accent picker for a registered engine", async () => {
    mockConfig();
    mockDepSweep();
    mockSources([]);
    renderAt();
    expect(await screen.findByTestId("accent-picker")).toBeInTheDocument();
  });

  it("hides the accent picker when the engine has never been registered", async () => {
    mockDepSweep();
    mockConfig({}, { status: 404 });
    renderAt();
    await screen.findByTestId("repo-engine-config-empty");
    expect(screen.queryByTestId("accent-picker")).not.toBeInTheDocument();
  });
});

function ConfigWithNavigator({ to }: Readonly<{ to: string }>) {
  const navigate = useNavigate();
  return (
    <div>
      <button
        type="button"
        data-testid="nav-to-repo"
        onClick={() => {
          void navigate(to);
        }}
      >
        navigate
      </button>
      <RepoEngineConfig />
    </div>
  );
}

describe("RepoEngineConfig — accent picker resets across route changes", () => {
  it("does not carry accent picker draft state across a parameter-only route change", async () => {
    const OTHER_OWNER = "other";
    const OTHER_REPO = "gizmo";
    mockConfig();
    mockDepSweep();
    mockSources([]);
    mockConfig({}, { owner: OTHER_OWNER, repo: OTHER_REPO });
    mockDepSweep(OTHER_OWNER, OTHER_REPO);
    mockEngineList([
      { repo: { owner: OWNER, repo: REPO }, posture: "propose" },
      { repo: { owner: OTHER_OWNER, repo: OTHER_REPO }, posture: "propose" },
    ]);
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={[`/repos/${OWNER}/${REPO}/config`]}>
        <Routes>
          <Route
            path="/repos/:owner/:repo/config"
            element={<ConfigWithNavigator to={`/repos/${OTHER_OWNER}/${OTHER_REPO}/config`} />}
          />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByTestId("accent-picker-swatches");
    await user.click(screen.getByTestId("accent-tile-iris"));
    expect(screen.getByTestId("accent-picker-unsaved")).toBeInTheDocument();

    await user.click(screen.getByTestId("nav-to-repo"));
    await screen.findByTestId("accent-picker-swatches");

    expect(screen.queryByTestId("accent-picker-unsaved")).not.toBeInTheDocument();
    expect(screen.getByTestId("accent-tile-iris")).toHaveAttribute("aria-checked", "false");
  });
});

describe("RepoEngineConfig — posture-ceiling note", () => {
  it("shows the note beneath the posture tile for a file-backed engine below autonomous", async () => {
    mockConfig();
    mockDepSweep();
    mockSources([
      {
        slug: ENGINE_KEY,
        in_version_control: true,
        config_path: "config/engines.d/widget.yaml",
        posture_ceiling: "propose",
        export_command: null,
      },
    ]);
    renderAt();
    const note = await screen.findByTestId("repo-engine-config-posture-ceiling-note");
    expect(note).toHaveTextContent("propose");
    await waitFor(() => {
      expect(within(note).getByRole("link")).toHaveAttribute(
        "href",
        `https://github.com/${STUB_DEFAULT_ENGINE_KEY}/blob/main/config/engines.d/widget.yaml`,
      );
    });
  });

  it("names the config file without linking it while no default repository is known", async () => {
    mockConfig();
    mockDepSweep();
    mockSources([
      {
        slug: ENGINE_KEY,
        in_version_control: true,
        config_path: "config/engines.d/widget.yaml",
        posture_ceiling: "propose",
        export_command: null,
      },
    ]);
    server.use(engineListingHandler({ engines: [makeEngineSummaryFor(ENGINE_KEY)] }));
    renderAt();
    const note = await screen.findByTestId("repo-engine-config-posture-ceiling-note");
    expect(within(note).getByText("config/engines.d/widget.yaml")).toBeInTheDocument();
    expect(within(note).queryByRole("link")).not.toBeInTheDocument();
  });

  it.each(DEFAULT_KEY_STATES)(
    "links the config file into the resolved default repository's blob (%s)",
    async (_state, defaultKey) => {
      mockConfig();
      mockDepSweep();
      mockSources([
        {
          slug: ENGINE_KEY,
          in_version_control: true,
          config_path: "config/engines.d/widget.yaml",
          posture_ceiling: "propose",
          export_command: null,
        },
      ]);
      server.use(
        engineListingHandler({ engines: [makeEngineSummaryFor(ENGINE_KEY)], defaultKey }),
      );
      renderAt();
      const note = await screen.findByTestId("repo-engine-config-posture-ceiling-note");
      await waitFor(() => {
        expect(within(note).getByRole("link")).toHaveAttribute(
          "href",
          `https://github.com/${defaultKey}/blob/main/config/engines.d/widget.yaml`,
        );
      });
    },
  );

  it("shows no note when the file ceiling is already autonomous", async () => {
    mockConfig();
    mockDepSweep();
    mockSources([
      {
        slug: ENGINE_KEY,
        in_version_control: true,
        config_path: "config/engines.d/widget.yaml",
        posture_ceiling: "autonomous",
        export_command: null,
      },
    ]);
    renderAt();
    await screen.findByTestId("engine-config-readonly");
    expect(screen.queryByTestId("repo-engine-config-posture-ceiling-note")).not.toBeInTheDocument();
  });

  it("shows no note when the engine is not yet file-backed", async () => {
    mockConfig();
    mockDepSweep();
    mockSources([
      {
        slug: ENGINE_KEY,
        in_version_control: false,
        config_path: null,
        posture_ceiling: "autonomous",
        export_command: "uv run python scripts/export_engine_configs.py acme/widget",
      },
    ]);
    renderAt();
    await screen.findByTestId("engine-config-readonly");
    expect(screen.queryByTestId("repo-engine-config-posture-ceiling-note")).not.toBeInTheDocument();
  });

  it("renders the unknown-source pointer, and no ceiling note, when the sources fetch fails", async () => {
    mockConfig();
    mockDepSweep();
    server.use(http.get("/api/engines/sources", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    renderAt();
    expect(await screen.findByTestId("engine-config-readonly-unknown-source")).toBeInTheDocument();
    expect(screen.queryByTestId("repo-engine-config-posture-ceiling-note")).not.toBeInTheDocument();
  });
});

describe("RepoEngineConfig — document read lifecycle", () => {
  it("lands on the loading skeleton and never renders the previous repository's config when the route changes repository", async () => {
    const OTHER_OWNER = "other";
    const OTHER_REPO = "gizmo";
    const gate = deferredFetch();
    mockConfig();
    mockDepSweep();
    mockSources([]);
    mockDepSweep(OTHER_OWNER, OTHER_REPO);
    mockEngineList([
      { repo: { owner: OWNER, repo: REPO }, posture: "propose" },
      { repo: { owner: OTHER_OWNER, repo: OTHER_REPO }, posture: "propose" },
    ]);
    server.use(
      http.get(`/api/engines/${OTHER_OWNER}/${OTHER_REPO}`, async () => {
        await gate.settled;
        return HttpResponse.json({ ...CONFIG, repo: { owner: OTHER_OWNER, repo: OTHER_REPO } });
      }),
    );
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={[`/repos/${OWNER}/${REPO}/config`]}>
        <Routes>
          <Route
            path="/repos/:owner/:repo/config"
            element={<ConfigWithNavigator to={`/repos/${OTHER_OWNER}/${OTHER_REPO}/config`} />}
          />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByTestId("engine-config-readonly");

    await user.click(screen.getByTestId("nav-to-repo"));

    expect(screen.getByTestId("repo-engine-config-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("engine-config-readonly")).not.toBeInTheDocument();
    gate.release();
    expect(await screen.findByTestId("engine-config-readonly")).toBeInTheDocument();
  });

  it("retry lands on the loading skeleton before the re-read settles", async () => {
    const gate = deferredFetch();
    mockDepSweep();
    mockSources([]);
    mockConfig({}, { status: 500 });
    const user = userEvent.setup();
    renderAt();
    await screen.findByTestId("repo-engine-config-error");

    server.use(
      http.get(`/api/engines/${OWNER}/${REPO}`, async () => {
        await gate.settled;
        return HttpResponse.json(CONFIG);
      }),
    );
    await user.click(screen.getByTestId("repo-engine-config-retry"));

    expect(screen.getByTestId("repo-engine-config-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("repo-engine-config-error")).not.toBeInTheDocument();
    gate.release();
    expect(await screen.findByTestId("engine-config-readonly")).toBeInTheDocument();
  });
});
