import { StrictMode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { CONFIG_PROPOSAL_BASE_PRESETS } from "../../../api";
import type { ConfigProposalResult, EngineConfig, EnginePreflightFinding } from "../../../api";
import { makeConfiguredEngineConfig, type WireEngineConfig } from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import { invalidateStep2Cache, readStep2Cache, seedDraft, writeStep2Cache } from "./connectStep2ConfigureLogic";
import ConnectStep2Configure from "./ConnectStep2Configure";

const OWNER = "acme";
const REPO = "widget";
const ENGINE_KEY = `${OWNER}/${REPO}`;
const BASE = `/api/engines/${OWNER}/${REPO}`;

const PASS_FINDING: EnginePreflightFinding = {
  check: "flow_graph_and_posture_valid",
  status: "pass",
  message: "ok",
  remedy: "",
  remedy_url: "",
};

function proposedFrom(live: WireEngineConfig): WireEngineConfig {
  return {
    ...live,
    gate_commands: [["uv", "run", "pytest", "-q"]],
    base_tools: ["Read", "Grep"],
  };
}

function makeProposal(live: WireEngineConfig, overrides: Partial<ConfigProposalResult> = {}): ConfigProposalResult {
  const proposed = proposedFrom(live);
  return {
    slug: ENGINE_KEY,
    base_preset: "python",
    detected_stacks: ["python"],
    additional_stacks: ["node"],
    proposed_config: proposed,
    diff: [
      { field: "gate_commands", baseline: live.gate_commands, proposed: proposed.gate_commands, changed: true },
      { field: "warmup_commands", baseline: live.warmup_commands, proposed: live.warmup_commands, changed: false },
      { field: "base_tools", baseline: live.base_tools, proposed: proposed.base_tools, changed: true },
      { field: "team_tools", baseline: live.team_tools, proposed: live.team_tools, changed: false },
      { field: "roles", baseline: live.roles, proposed: live.roles, changed: false },
      { field: "transition_graph", baseline: live.transition_graph, proposed: live.transition_graph, changed: false },
      { field: "flow_policy", baseline: live.flow_policy, proposed: live.flow_policy, changed: false },
    ],
    validation: PASS_FINDING,
    ...overrides,
  };
}

function mockLive(overrides: Partial<EngineConfig> = {}): WireEngineConfig {
  const live = makeConfiguredEngineConfig(overrides);
  server.use(http.get(BASE, () => HttpResponse.json(live)));
  return live;
}

function mockProposal(
  live: WireEngineConfig,
  handler: () => ConfigProposalResult | Promise<ConfigProposalResult> = () => makeProposal(live),
) {
  server.use(http.post(`${BASE}/config-proposal`, async () => HttpResponse.json(await handler())));
}

function mock422(finding: EnginePreflightFinding) {
  server.use(http.post(`${BASE}/config-proposal`, () => HttpResponse.json({ detail: finding }, { status: 422 })));
}

function mockValidate(handler: () => EnginePreflightFinding | Promise<EnginePreflightFinding> = () => PASS_FINDING) {
  server.use(http.post(`${BASE}/config-draft-validate`, async () => HttpResponse.json(await handler())));
}

function mockPresets() {
  server.use(
    http.get("/api/engine-presets", () =>
      HttpResponse.json({
        presets: [
          {
            name: "python",
            config: makeConfiguredEngineConfig({ transition_graph: { research: ["architecture"] }, flow_policy: null }),
          },
        ],
      }),
    ),
  );
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{location.pathname}</div>;
}

function renderStep2() {
  return render(
    <MemoryRouter initialEntries={["/connect/acme/widget/configure"]}>
      <LocationProbe />
      <Routes>
        <Route path="/connect/:owner/:repo/:step" element={<ConnectStep2Configure owner={OWNER} repo={REPO} />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderStep2StrictMode() {
  return render(
    <StrictMode>
      <MemoryRouter initialEntries={["/connect/acme/widget/configure"]}>
        <LocationProbe />
        <Routes>
          <Route path="/connect/:owner/:repo/:step" element={<ConnectStep2Configure owner={OWNER} repo={REPO} />} />
        </Routes>
      </MemoryRouter>
    </StrictMode>,
  );
}

beforeEach(() => {
  invalidateStep2Cache(ENGINE_KEY);
  mockValidate();
  mockPresets();
});

describe("ConnectStep2Configure", () => {
  it("renders exactly the seven proposable rows once the proposal settles", async () => {
    const live = mockLive();
    mockProposal(live);
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    const rowFields = [
      "gate-commands",
      "warmup-commands",
      "base-tools",
      "team-tools",
      "roles",
      "transition-graph",
      "flow-policy",
    ];
    for (const field of rowFields) {
      expect(screen.getByTestId(`connect-step2-diff-${field}`)).toBeInTheDocument();
    }
  });

  it("renders code_quality and pool_size as baseline fields, and posture nowhere on the page", async () => {
    const live = mockLive({ code_quality: { project_key: "k", organization: "o", host_url: "h", token_env_var: "t" } });
    mockProposal(live);
    renderStep2();
    await screen.findByTestId("step2-baseline-fields");
    expect(screen.getByTestId("step2-pool-size")).toBeInTheDocument();
    expect(screen.getByTestId("step2-cq-project-key")).toHaveValue("k");
    expect(screen.queryByText(/posture/i)).not.toBeInTheDocument();
  });

  it("shows a named in-flight state before the proposal resolves, not a bare spinner", async () => {
    const live = mockLive();
    let resolveProposal!: (value: ConfigProposalResult) => void;
    const pending = new Promise<ConfigProposalResult>((resolve) => {
      resolveProposal = resolve;
    });
    server.use(http.post(`${BASE}/config-proposal`, async () => HttpResponse.json(await pending)));
    renderStep2();
    expect(screen.getByTestId("step2-phase")).toHaveTextContent(/drafting a config proposal/i);
    resolveProposal(makeProposal(live));
    await waitFor(() => {
      expect(screen.getByTestId("step2-phase")).toHaveTextContent(/proposal ready/i);
    });
  });

  it("degrades to the delivered finding's own message and remedy plus the preset-seed fallback on a 422, without navigating", async () => {
    mockLive();
    mock422({
      check: "config_proposal",
      status: "fail",
      message: "a config-proposal pipeline is already running for 'acme/widget'",
      remedy: "seed the engine configuration from a stack preset manually in the engine configuration editor",
      remedy_url: "",
    });
    renderStep2();
    await screen.findByTestId("step2-fallback");
    expect(screen.getByTestId("step2-fallback-message")).toHaveTextContent(
      "a config-proposal pipeline is already running for 'acme/widget'",
    );
    expect(screen.getByTestId("step2-fallback-remedy")).toHaveTextContent("seed the engine configuration");
    expect(screen.getByTestId("engine-preset-seed-open")).toBeInTheDocument();
    expect(screen.queryByTestId("connect-step2-diff-table")).not.toBeInTheDocument();
    expect(screen.getByTestId("location-probe")).toHaveTextContent("/connect/acme/widget/configure");
  });

  it("renders a distinct message for the second documented 422 cause — never labelling both the same", async () => {
    mockLive();
    mock422({
      check: "config_proposal",
      status: "fail",
      message: "could not obtain a read-only checkout of 'acme/widget': repo unreachable",
      remedy: "seed the engine configuration from a stack preset manually in the engine configuration editor",
      remedy_url: "",
    });
    renderStep2();
    await screen.findByTestId("step2-fallback");
    expect(screen.getByTestId("step2-fallback-message")).toHaveTextContent("repo unreachable");
    expect(screen.getByTestId("step2-fallback-message")).not.toHaveTextContent("already running");
  });

  it("re-validates on every edit, debounced, with the draft-only chip copy", async () => {
    const live = mockLive();
    mockProposal(live);
    let calls = 0;
    server.use(
      http.post(`${BASE}/config-draft-validate`, () => {
        calls += 1;
        return HttpResponse.json(PASS_FINDING);
      }),
    );
    const user = userEvent.setup();
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    await waitFor(
      () => {
        expect(calls).toBe(1);
      },
      { timeout: 3000 },
    );
    expect(screen.getByTestId("step2-validation-caption")).toHaveTextContent(
      "validated as a draft — nothing saved yet",
    );
    const baseToolsEditor = screen.getByTestId("connect-step2-diff-base-tools-now-editor");
    await user.type(baseToolsEditor, "\nWrite");
    await waitFor(
      () => {
        expect(calls).toBe(2);
      },
      { timeout: 3000 },
    );
  });

  it("offers a chip for every registered engine preset, ios included", async () => {
    const live = mockLive();
    mockProposal(live);
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    for (const preset of CONFIG_PROPOSAL_BASE_PRESETS) {
      expect(screen.getByTestId(`step2-preset-${preset}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId("step2-preset-ios")).toBeInTheDocument();
  });

  it("choosing a different base preset re-runs the proposal against it", async () => {
    const live = mockLive();
    mockProposal(live);
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    let lastBody: unknown;
    server.use(
      http.post(`${BASE}/config-proposal`, async ({ request }) => {
        lastBody = await request.json();
        return HttpResponse.json(makeProposal(live, { base_preset: "node" }));
      }),
    );
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step2-preset-node"));
    await waitFor(() => {
      expect(lastBody).toEqual({ base_preset: "node" });
    });
    await screen.findByTestId("connect-step2-diff-table");
  });

  it("clicking the already-selected base preset chip does not re-run the proposal", async () => {
    const live = mockLive();
    mockProposal(live);
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    let calls = 0;
    server.use(
      http.post(`${BASE}/config-proposal`, () => {
        calls += 1;
        return HttpResponse.json(makeProposal(live));
      }),
    );
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step2-preset-python"));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(calls).toBe(0);
    expect(screen.getByTestId("step2-phase")).toHaveTextContent(/proposal ready/i);
  });

  it("keeps draft edits in the in-session cache, surviving an unmount and remount", async () => {
    const live = mockLive();
    mockProposal(live);
    const user = userEvent.setup();
    const { unmount } = renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    const projectKey = screen.getByTestId("step2-cq-project-key");
    await user.type(projectKey, "acme-widget");
    await waitFor(() => {
      expect(readStep2Cache(ENGINE_KEY)?.draft.code_quality?.project_key).toBe("acme-widget");
    });
    unmount();
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    expect(screen.getByTestId("step2-cq-project-key")).toHaveValue("acme-widget");
  });

  it("save carries the engine's live posture through unchanged, even when it was raised via step 4 and the user stepped back", async () => {
    const live = mockLive({ posture: "propose" });
    mockProposal(live);
    let patchBody: EngineConfig | undefined;
    server.use(
      http.patch(BASE, async ({ request }) => {
        patchBody = (await request.json()) as EngineConfig;
        return HttpResponse.json(patchBody);
      }),
    );
    const user = userEvent.setup();
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    await user.click(screen.getByTestId("step2-footer-primary"));
    await waitFor(() => {
      expect(patchBody?.posture).toBe("propose");
    });
  });

  it("save 200 navigates to the dispatchable step", async () => {
    const live = mockLive();
    mockProposal(live);
    server.use(http.patch(BASE, async ({ request }) => HttpResponse.json(await request.json())));
    const user = userEvent.setup();
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    await user.click(screen.getByTestId("step2-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/connect/acme/widget/dispatchable");
    });
  });

  it("a pool-resize 409 on save renders through MutationErrorNotice and does not navigate", async () => {
    const live = mockLive();
    mockProposal(live);
    server.use(
      http.patch(BASE, () =>
        HttpResponse.json({ detail: "engine's pool has active leases and cannot be resized" }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    await user.clear(screen.getByTestId("step2-pool-size"));
    await user.type(screen.getByTestId("step2-pool-size"), "8");
    await user.click(screen.getByTestId("step2-footer-primary"));
    const notice = await screen.findByTestId("step2-save-error");
    expect(notice).toHaveTextContent("worker pool");
    expect(screen.getByTestId("location-probe")).toHaveTextContent("/connect/acme/widget/configure");
  });

  it("shows an error state when the validate call itself fails, distinct from a fail finding", async () => {
    const live = mockLive();
    mockProposal(live);
    server.use(http.post(`${BASE}/config-draft-validate`, () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    expect(await screen.findByTestId("step2-validation-error", {}, { timeout: 3000 })).toHaveTextContent(
      "boom",
    );
  });

  it("renders the fail finding's message when validation itself succeeds but reports a non-pass finding", async () => {
    const live = mockLive();
    mockProposal(live);
    mockValidate(() => ({
      check: "flow_graph_and_posture_valid",
      status: "fail",
      message: "merge team has no predecessors",
      remedy: "",
      remedy_url: "",
    }));
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    expect(
      await screen.findByTestId("step2-validation-message", {}, { timeout: 3000 }),
    ).toHaveTextContent("merge team has no predecessors");
  });

  it("edits a code_quality baseline field", async () => {
    const live = mockLive();
    mockProposal(live);
    const user = userEvent.setup();
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    const projectKey = screen.getByTestId("step2-cq-project-key");
    await user.type(projectKey, "acme-widget");
    expect(projectKey).toHaveValue("acme-widget");
  });

  it("ignores a non-numeric pool-size edit rather than corrupting the field", async () => {
    const live = mockLive({ pool_size: 3 });
    mockProposal(live);
    const user = userEvent.setup();
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    const poolSize = screen.getByTestId("step2-pool-size");
    expect(poolSize).toHaveValue("3");
    await user.type(poolSize, "x");
    expect(poolSize).toHaveValue("3");
  });

  it("renders detected stacks with an em-dash placeholder when none were detected", async () => {
    const live = mockLive();
    mockProposal(live, () => makeProposal(live, { detected_stacks: [] }));
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    expect(screen.getByTestId("step2-evidence-stacks")).toHaveTextContent("Detected: —");
  });

  it("renders a generic load error when the mount-time proposal call fails with a non-Finding error", async () => {
    mockLive();
    server.use(http.post(`${BASE}/config-proposal`, () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    renderStep2();
    expect(await screen.findByTestId("step2-error")).toHaveTextContent("boom");
    expect(screen.queryByTestId("step2-fallback")).not.toBeInTheDocument();
  });

  it("Start from a preset instead seeds transition_graph and flow_policy into the draft without touching other fields", async () => {
    const live = mockLive();
    mockProposal(live);
    const user = userEvent.setup();
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    await user.click(screen.getByTestId("engine-preset-seed-open"));
    await screen.findByTestId("engine-preset-seed-dialog");
    await user.selectOptions(screen.getByTestId("engine-preset-seed-select"), "python");
    await user.click(screen.getByTestId("engine-preset-seed-confirm"));
    expect(screen.queryByTestId("engine-preset-seed-dialog")).not.toBeInTheDocument();
    expect(screen.getByTestId("connect-step2-diff-transition-graph-baseline")).toBeInTheDocument();
  });

  it("a repropose that 422s with a finding degrades to the fallback panel, discarding the diff table", async () => {
    const live = mockLive();
    mockProposal(live);
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    mock422({
      check: "config_proposal",
      status: "fail",
      message: "a config-proposal pipeline is already running for 'acme/widget'",
      remedy: "seed the engine configuration from a stack preset manually in the engine configuration editor",
      remedy_url: "",
    });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step2-preset-node"));
    await screen.findByTestId("step2-fallback");
    expect(screen.queryByTestId("connect-step2-diff-table")).not.toBeInTheDocument();
  });

  it("a repropose that fails with a non-Finding error renders the generic load-error state", async () => {
    const live = mockLive();
    mockProposal(live);
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    server.use(http.post(`${BASE}/config-proposal`, () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step2-preset-node"));
    expect(await screen.findByTestId("step2-error")).toHaveTextContent("boom");
  });

  it("StrictMode's dev-mode double-invoke fires the lock-guarded proposal POST only once", async () => {
    const live = mockLive();
    let proposeCalls = 0;
    server.use(
      http.post(`${BASE}/config-proposal`, () => {
        proposeCalls += 1;
        return HttpResponse.json(makeProposal(live));
      }),
    );
    renderStep2StrictMode();
    await screen.findByTestId("connect-step2-diff-table");
    expect(proposeCalls).toBe(1);
  });

  it("discards a stale mount result that resolves after engineKey has already moved on", async () => {
    const liveA = mockLive();
    let resolveProposalA!: (value: ConfigProposalResult) => void;
    const pendingA = new Promise<ConfigProposalResult>((resolve) => {
      resolveProposalA = resolve;
    });
    server.use(http.post(`${BASE}/config-proposal`, async () => HttpResponse.json(await pendingA)));

    const ui = (owner: string, repo: string) => (
      <MemoryRouter initialEntries={["/connect/acme/widget/configure"]}>
        <Routes>
          <Route path="/connect/:owner/:repo/:step" element={<ConnectStep2Configure owner={owner} repo={repo} />} />
        </Routes>
      </MemoryRouter>
    );
    const { rerender } = render(ui(OWNER, REPO));
    await waitFor(() => {
      expect(screen.getByTestId("step2-phase")).toHaveTextContent(/drafting a config proposal/i);
    });

    const otherOwner = "other-co";
    const otherRepo = "other-repo";
    const otherBase = `/api/engines/${otherOwner}/${otherRepo}`;
    const liveB = makeConfiguredEngineConfig({ repo: { owner: otherOwner, repo: otherRepo } });
    server.use(http.get(otherBase, () => HttpResponse.json(liveB)));
    const proposalB = makeProposal(liveB, { additional_stacks: ["ruby"] });
    server.use(http.post(`${otherBase}/config-proposal`, () => HttpResponse.json(proposalB)));
    server.use(http.post(`${otherBase}/config-draft-validate`, () => HttpResponse.json(PASS_FINDING)));

    rerender(ui(otherOwner, otherRepo));
    await screen.findByTestId("connect-step2-diff-table");
    expect(screen.getByTestId("step2-evidence-stacks")).toHaveTextContent("ruby");

    resolveProposalA(makeProposal(liveA));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByTestId("step2-evidence-stacks")).toHaveTextContent("ruby");
  });

  it("discards a stale successful repropose result that resolves after engineKey has already moved on", async () => {
    const liveA = mockLive();
    mockProposal(liveA);
    const ui = (owner: string, repo: string) => (
      <MemoryRouter initialEntries={["/connect/acme/widget/configure"]}>
        <Routes>
          <Route path="/connect/:owner/:repo/:step" element={<ConnectStep2Configure owner={owner} repo={repo} />} />
        </Routes>
      </MemoryRouter>
    );
    const { rerender } = render(ui(OWNER, REPO));
    await screen.findByTestId("connect-step2-diff-table");

    let resolveRepropose!: (value: ConfigProposalResult) => void;
    const pendingRepropose = new Promise<ConfigProposalResult>((resolve) => {
      resolveRepropose = resolve;
    });
    server.use(http.post(`${BASE}/config-proposal`, async () => HttpResponse.json(await pendingRepropose)));
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step2-preset-node"));
    await waitFor(() => {
      expect(screen.getByTestId("step2-phase")).toHaveTextContent(/drafting a config proposal/i);
    });

    const otherOwner = "other-repropose-co";
    const otherRepo = "other-repropose-repo";
    const otherBase = `/api/engines/${otherOwner}/${otherRepo}`;
    const liveB = makeConfiguredEngineConfig({ repo: { owner: otherOwner, repo: otherRepo } });
    server.use(http.get(otherBase, () => HttpResponse.json(liveB)));
    const proposalB = makeProposal(liveB, { additional_stacks: ["ruby"] });
    server.use(http.post(`${otherBase}/config-proposal`, () => HttpResponse.json(proposalB)));
    server.use(http.post(`${otherBase}/config-draft-validate`, () => HttpResponse.json(PASS_FINDING)));
    rerender(ui(otherOwner, otherRepo));
    await screen.findByTestId("connect-step2-diff-table");
    expect(screen.getByTestId("step2-evidence-stacks")).toHaveTextContent("ruby");

    resolveRepropose(makeProposal(liveA, { base_preset: "node" }));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByTestId("step2-evidence-stacks")).toHaveTextContent("ruby");
  });

  it("discards a stale failed repropose result that rejects after engineKey has already moved on", async () => {
    const liveA = mockLive();
    mockProposal(liveA);
    const ui = (owner: string, repo: string) => (
      <MemoryRouter initialEntries={["/connect/acme/widget/configure"]}>
        <Routes>
          <Route path="/connect/:owner/:repo/:step" element={<ConnectStep2Configure owner={owner} repo={repo} />} />
        </Routes>
      </MemoryRouter>
    );
    const { rerender } = render(ui(OWNER, REPO));
    await screen.findByTestId("connect-step2-diff-table");

    let releaseRepropose!: () => void;
    const gate = new Promise<void>((resolve) => {
      releaseRepropose = resolve;
    });
    server.use(
      http.post(`${BASE}/config-proposal`, async () => {
        await gate;
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      }),
    );
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step2-preset-node"));
    await waitFor(() => {
      expect(screen.getByTestId("step2-phase")).toHaveTextContent(/drafting a config proposal/i);
    });

    const otherOwner = "other-repropose-fail-co";
    const otherRepo = "other-repropose-fail-repo";
    const otherBase = `/api/engines/${otherOwner}/${otherRepo}`;
    const liveB = makeConfiguredEngineConfig({ repo: { owner: otherOwner, repo: otherRepo } });
    server.use(http.get(otherBase, () => HttpResponse.json(liveB)));
    const proposalB = makeProposal(liveB, { additional_stacks: ["ruby"] });
    server.use(http.post(`${otherBase}/config-proposal`, () => HttpResponse.json(proposalB)));
    server.use(http.post(`${otherBase}/config-draft-validate`, () => HttpResponse.json(PASS_FINDING)));
    rerender(ui(otherOwner, otherRepo));
    await screen.findByTestId("connect-step2-diff-table");
    expect(screen.getByTestId("step2-evidence-stacks")).toHaveTextContent("ruby");

    releaseRepropose();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByTestId("step2-evidence-stacks")).toHaveTextContent("ruby");
    expect(screen.queryByTestId("step2-error")).not.toBeInTheDocument();
  });

  it("a warm in-session cache hit skips the config-proposal POST", async () => {
    const live = mockLive();
    const proposal = makeProposal(live);
    let proposeCalls = 0;
    server.use(
      http.post(`${BASE}/config-proposal`, () => {
        proposeCalls += 1;
        return HttpResponse.json(proposal);
      }),
    );
    writeStep2Cache(ENGINE_KEY, { proposal, draft: seedDraft(proposal, live) });
    renderStep2();
    await screen.findByTestId("connect-step2-diff-table");
    expect(proposeCalls).toBe(0);
  });
});
