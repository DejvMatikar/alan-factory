import { StrictMode } from "react";
import { fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { EnginePosture, PreflightStatus, WorkspaceSummary } from "../../../api";
import { server } from "../../../test/server";
import { makeWorkspaceSummary } from "../../../test/workspaceFixtures";
import ConnectStep4Posture, { useStep4Mount } from "./ConnectStep4Posture";

const OWNER = "acme";
const REPO = "widget";
const ENGINE_KEY = `${OWNER}/${REPO}`;
const PREFLIGHT_URL = `/api/engines/${OWNER}/${REPO}/preflight`;
const ENGINE_URL = `/api/engines/${OWNER}/${REPO}`;

function preflightResult(overall: PreflightStatus) {
  return { slug: ENGINE_KEY, checked_at: "2026-07-29T00:00:00Z", overall, findings: [] };
}

function workspace(overrides: Partial<WorkspaceSummary> = {}): WorkspaceSummary {
  return makeWorkspaceSummary({ status: "completed", team_runs_total: 1, invocations_total: 1, ...overrides });
}

function mockMount(opts: {
  overall?: PreflightStatus;
  preflightError?: boolean;
  currentPosture?: EnginePosture;
  workspaces?: WorkspaceSummary[];
} = {}) {
  const { overall = "pass", preflightError = false, currentPosture = "observe", workspaces = [] } = opts;
  server.use(
    http.post(PREFLIGHT_URL, () =>
      preflightError
        ? HttpResponse.json({ detail: "boom" }, { status: 500 })
        : HttpResponse.json(preflightResult(overall)),
    ),
    http.get(ENGINE_URL, () =>
      HttpResponse.json({ repo: { owner: OWNER, repo: REPO }, posture: currentPosture }),
    ),
    http.get("/api/workspaces", () => HttpResponse.json(workspaces)),
  );
}

function LocationProbe() {
  const location = useLocation();
  return (
    <div data-testid="location-probe" data-state={JSON.stringify(location.state ?? null)}>
      {location.pathname}
    </div>
  );
}

function Harness({ engineKey, strict }: Readonly<{ engineKey: string; strict?: boolean }>) {
  const tree = (
    <MemoryRouter initialEntries={["/start"]}>
      <LocationProbe />
      <Routes>
        <Route path="/start" element={<ConnectStep4Posture engineKey={engineKey} />} />
        <Route path="/projects/:projectId" element={<div data-testid="project-page" />} />
      </Routes>
    </MemoryRouter>
  );
  return strict ? <StrictMode>{tree}</StrictMode> : tree;
}

function renderStep4(engineKey = ENGINE_KEY, opts: { strict?: boolean } = {}) {
  return render(<Harness engineKey={engineKey} strict={opts.strict ?? false} />);
}

async function waitForChip(text: string): Promise<void> {
  await waitFor(() => {
    expect(screen.getByTestId("step4-readiness-chip")).toHaveTextContent(text);
  });
}

describe("mount", () => {
  it("fires the preflight POST exactly once, even under React.StrictMode's double-invoke", async () => {
    let calls = 0;
    server.use(
      http.post(PREFLIGHT_URL, () => {
        calls += 1;
        return HttpResponse.json(preflightResult("pass"));
      }),
      http.get(ENGINE_URL, () =>
        HttpResponse.json({ repo: { owner: OWNER, repo: REPO }, posture: "observe" }),
      ),
      http.get("/api/workspaces", () => HttpResponse.json([])),
    );
    renderStep4(ENGINE_KEY, { strict: true });
    await waitForChip("ready to dispatch");
    expect(calls).toBe(1);
  });

  it("the mount guard's early-return actually fires under React.StrictMode's synchronous double-invoke (hook-level, mirrors useSessionSwitchReset's precedent)", async () => {
    let calls = 0;
    server.use(
      http.post(PREFLIGHT_URL, () => {
        calls += 1;
        return HttpResponse.json(preflightResult("pass"));
      }),
      http.get(ENGINE_URL, () =>
        HttpResponse.json({ repo: { owner: OWNER, repo: REPO }, posture: "observe" }),
      ),
      http.get("/api/workspaces", () => HttpResponse.json([])),
    );
    const { result } = renderHook(() => useStep4Mount(ENGINE_KEY), { wrapper: StrictMode });
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(calls).toBe(1);
  });

  it("falls back to safe defaults when getEngine and listWorkspaces both fail", async () => {
    server.use(
      http.post(PREFLIGHT_URL, () => HttpResponse.json(preflightResult("pass"))),
      http.get(ENGINE_URL, () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
      http.get("/api/workspaces", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    renderStep4();
    await waitForChip("ready to dispatch");
    expect(screen.getByTestId("step4-posture-observe")).toBeChecked();
    expect(screen.getByTestId("step4-posture-self_merge")).toBeDisabled();
  });

  it("resets the mount guard and refetches when engineKey changes", async () => {
    let widgetCalls = 0;
    let otherCalls = 0;
    server.use(
      http.post(PREFLIGHT_URL, () => {
        widgetCalls += 1;
        return HttpResponse.json(preflightResult("pass"));
      }),
      http.get(ENGINE_URL, () =>
        HttpResponse.json({ repo: { owner: OWNER, repo: REPO }, posture: "observe" }),
      ),
      http.post("/api/engines/other/repo/preflight", () => {
        otherCalls += 1;
        return HttpResponse.json({ ...preflightResult("fail"), slug: "other/repo" });
      }),
      http.get("/api/engines/other/repo", () =>
        HttpResponse.json({ repo: { owner: "other", repo: "repo" }, posture: "observe" }),
      ),
      http.get("/api/workspaces", () => HttpResponse.json([])),
    );
    const { rerender } = renderStep4(ENGINE_KEY);
    await waitForChip("ready to dispatch");
    expect(widgetCalls).toBe(1);

    rerender(<Harness engineKey="other/repo" />);
    await waitForChip("blocked");
    expect(otherCalls).toBe(1);
    expect(widgetCalls).toBe(1);
  });

  it("discards a stale in-flight result that settles after engineKey has already moved on", async () => {
    let resolveWidgetPreflight!: (value: Response) => void;
    server.use(
      http.post(
        PREFLIGHT_URL,
        () =>
          new Promise<Response>((resolve) => {
            resolveWidgetPreflight = resolve;
          }),
      ),
      http.get(ENGINE_URL, () =>
        HttpResponse.json({ repo: { owner: OWNER, repo: REPO }, posture: "observe" }),
      ),
      http.get("/api/workspaces", () => HttpResponse.json([])),
      http.post("/api/engines/other/repo/preflight", () =>
        HttpResponse.json({ ...preflightResult("fail"), slug: "other/repo" }),
      ),
      http.get("/api/engines/other/repo", () =>
        HttpResponse.json({ repo: { owner: "other", repo: "repo" }, posture: "observe" }),
      ),
    );
    const { result, rerender } = renderHook(
      ({ engineKey }: { engineKey: string }) => useStep4Mount(engineKey),
      { initialProps: { engineKey: ENGINE_KEY } },
    );
    rerender({ engineKey: "other/repo" });
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.preflightOverall).toBe("fail");

    resolveWidgetPreflight(HttpResponse.json(preflightResult("pass")));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(result.current.preflightOverall).toBe("fail");
  });
});

describe("readiness chip", () => {
  it("reads pass as ready to dispatch", async () => {
    mockMount({ overall: "pass" });
    renderStep4();
    await waitForChip("ready to dispatch");
  });

  it("reads fail as blocked", async () => {
    mockMount({ overall: "fail" });
    renderStep4();
    await waitForChip("blocked");
  });

  it("reads unknown as partially ready", async () => {
    mockMount({ overall: "unknown" });
    renderStep4();
    await waitForChip("partially ready");
  });

  it("reads a pending/errored preflight as partially ready, never blocked", async () => {
    mockMount({ preflightError: true });
    renderStep4();
    expect(screen.getByTestId("step4-readiness-chip")).toHaveTextContent("partially ready");
    await waitFor(() => {
      expect(screen.getByTestId("step4-readiness-chip")).toHaveTextContent("partially ready");
    });
  });
});

describe("posture radio column", () => {
  it("renders four rows with observe pre-checked", async () => {
    mockMount();
    renderStep4();
    await waitForChip("ready to dispatch");
    expect(screen.getByTestId("step4-posture-observe")).toBeChecked();
    expect(screen.getByTestId("step4-posture-propose")).not.toBeChecked();
    expect(screen.getByTestId("step4-posture-self_merge")).not.toBeChecked();
    expect(screen.getByTestId("step4-posture-autonomous")).not.toBeChecked();
  });

  it("greys self_merge and autonomous with no merged work, and a real user click is a no-op", async () => {
    mockMount({ workspaces: [] });
    renderStep4();
    await waitForChip("ready to dispatch");
    const selfMerge = screen.getByTestId("step4-posture-self_merge");
    const autonomous = screen.getByTestId("step4-posture-autonomous");
    expect(selfMerge).toBeDisabled();
    expect(autonomous).toBeDisabled();
    const user = userEvent.setup();
    await user.click(selfMerge);
    expect(selfMerge).not.toBeChecked();
    expect(screen.getByTestId("step4-posture-observe")).toBeChecked();
  });

  it("un-greys self_merge and autonomous once the engine has merged work", async () => {
    mockMount({ workspaces: [workspace({ engine_key: ENGINE_KEY, pr_number: 7 })] });
    renderStep4();
    await waitForChip("ready to dispatch");
    await waitFor(() => {
      expect(screen.getByTestId("step4-posture-self_merge")).not.toBeDisabled();
    });
    expect(screen.getByTestId("step4-posture-autonomous")).not.toBeDisabled();
  });

  it("hides the unavailable-reason note once merged work makes every rung selectable", async () => {
    mockMount({ workspaces: [workspace({ engine_key: ENGINE_KEY, pr_number: 7 })] });
    renderStep4();
    await waitForChip("ready to dispatch");
    await waitFor(() => {
      expect(screen.getByTestId("step4-posture-self_merge")).not.toBeDisabled();
    });
    expect(document.getElementById("posture-unavailable-reason")).toBeNull();
  });

  it("both greyed inputs' aria-describedby resolve to the single unavailable-reason node", async () => {
    mockMount();
    renderStep4();
    await waitForChip("ready to dispatch");
    const reasonId = "posture-unavailable-reason";
    expect(screen.getByTestId("step4-posture-self_merge")).toHaveAttribute(
      "aria-describedby",
      reasonId,
    );
    expect(screen.getByTestId("step4-posture-autonomous")).toHaveAttribute(
      "aria-describedby",
      reasonId,
    );
    expect(document.getElementById(reasonId)).toHaveTextContent(/merged work/);
    expect(screen.getByTestId("step4-posture-propose")).not.toHaveAttribute("aria-describedby");
  });
});

describe("footer", () => {
  it("renders exactly one button", async () => {
    mockMount();
    renderStep4();
    await waitForChip("ready to dispatch");
    const footer = screen.getByTestId("step4-footer");
    expect(within(footer).getAllByRole("button")).toHaveLength(1);
  });

  it("disables Finish while mount data is still loading, so an early click can't take the stale-observe fast path", async () => {
    let resolveEngine: ((value: Response) => void) | undefined;
    server.use(
      http.post(PREFLIGHT_URL, () => HttpResponse.json(preflightResult("pass"))),
      http.get(
        ENGINE_URL,
        () =>
          new Promise<Response>((resolve) => {
            resolveEngine = resolve;
          }),
      ),
      http.get("/api/workspaces", () => HttpResponse.json([])),
    );
    renderStep4();
    expect(screen.getByTestId("step4-footer-primary")).toBeDisabled();

    await waitFor(() => {
      expect(resolveEngine).toBeDefined();
    });
    resolveEngine?.(
      HttpResponse.json({ repo: { owner: OWNER, repo: REPO }, posture: "propose" }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("step4-footer-primary")).not.toBeDisabled();
    });
  });
});

describe("finish — posture path", () => {
  it("selecting propose and finishing opens the confirm dialog with a locked target and no free <select>", async () => {
    mockMount({ currentPosture: "observe" });
    server.use(http.get("/api/projects", () => HttpResponse.json([])));
    renderStep4();
    await waitForChip("ready to dispatch");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-posture-propose"));
    await user.click(screen.getByTestId("step4-footer-primary"));
    expect(await screen.findByTestId("posture-confirm-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("posture-confirm-target")).toHaveTextContent(/Propose/);
    expect(screen.queryByTestId("posture-confirm-select")).not.toBeInTheDocument();
  });

  it("a manual selection made while data is still loading survives the load completing (no clobber)", async () => {
    let resolveEngine: ((value: Response) => void) | undefined;
    server.use(
      http.post(PREFLIGHT_URL, () => HttpResponse.json(preflightResult("pass"))),
      http.get(
        ENGINE_URL,
        () =>
          new Promise<Response>((resolve) => {
            resolveEngine = resolve;
          }),
      ),
      http.get("/api/workspaces", () => HttpResponse.json([])),
      http.get("/api/projects", () => HttpResponse.json([])),
    );
    renderStep4();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-posture-propose"));
    expect(screen.getByTestId("step4-posture-propose")).toBeChecked();

    await waitFor(() => {
      expect(resolveEngine).toBeDefined();
    });
    resolveEngine?.(HttpResponse.json({ repo: { owner: OWNER, repo: REPO }, posture: "observe" }));
    await waitFor(() => {
      expect(screen.getByTestId("step4-footer-primary")).not.toBeDisabled();
    });
    expect(screen.getByTestId("step4-posture-propose")).toBeChecked();

    await user.click(screen.getByTestId("step4-footer-primary"));
    expect(await screen.findByTestId("posture-confirm-dialog")).toBeInTheDocument();
  });

  it("cancelling the confirm dialog closes it without writing posture or touching projects", async () => {
    let postureWrites = 0;
    let projectPosts = 0;
    mockMount({ currentPosture: "observe" });
    server.use(
      http.post(ENGINE_URL + "/posture", () => {
        postureWrites += 1;
        return HttpResponse.json({ repo: { owner: OWNER, repo: REPO }, posture: "propose" });
      }),
      http.get("/api/projects", () => HttpResponse.json([])),
      http.post("/api/projects", () => {
        projectPosts += 1;
        return HttpResponse.json({ id: "proj-new", title: REPO }, { status: 201 });
      }),
    );
    renderStep4();
    await waitForChip("ready to dispatch");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-posture-propose"));
    await user.click(screen.getByTestId("step4-footer-primary"));
    const dialog = await screen.findByTestId("posture-confirm-dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("posture-confirm-dialog")).not.toBeInTheDocument();
    expect(postureWrites).toBe(0);
    expect(projectPosts).toBe(0);
  });

  it("initializes the radio from the engine's real current posture on revisit, not a hardcoded observe", async () => {
    let postureWriteCalls = 0;
    mockMount({ currentPosture: "propose", workspaces: [] });
    server.use(
      http.post(ENGINE_URL + "/posture", () => {
        postureWriteCalls += 1;
        return HttpResponse.json({ repo: { owner: OWNER, repo: REPO }, posture: "propose" });
      }),
      http.get("/api/projects", () => HttpResponse.json([])),
      http.post("/api/projects", () =>
        HttpResponse.json({ id: "proj-1", title: REPO }, { status: 201 }),
      ),
      http.patch("/api/projects/proj-1", () => HttpResponse.json({ id: "proj-1", title: REPO })),
    );
    renderStep4();
    await waitForChip("ready to dispatch");
    await waitFor(() => {
      expect(screen.getByTestId("step4-posture-propose")).toBeChecked();
    });
    expect(screen.getByTestId("step4-posture-observe")).not.toBeChecked();

    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-footer-primary"));
    expect(screen.queryByTestId("posture-confirm-dialog")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/projects/proj-1");
    });
    expect(postureWriteCalls).toBe(0);
  });

  it("leaving the selection at observe (equal to current) finishes with no modal and no posture write", async () => {
    let postureWriteCalls = 0;
    mockMount({ currentPosture: "observe" });
    server.use(
      http.post(PREFLIGHT_URL, () => HttpResponse.json(preflightResult("pass"))),
      http.post(ENGINE_URL + "/posture", () => {
        postureWriteCalls += 1;
        return HttpResponse.json({ repo: { owner: OWNER, repo: REPO }, posture: "observe" });
      }),
      http.get("/api/projects", () => HttpResponse.json([])),
      http.post("/api/projects", () =>
        HttpResponse.json(
          {
            id: "proj-1",
            title: REPO,
            status: "active",
            doc_ref: null,
            autonomy: "propose",
            budget_usd: null,
            created_at: "2026-07-29T00:00:00Z",
            updated_at: "2026-07-29T00:00:00Z",
          },
          { status: 201 },
        ),
      ),
      http.patch("/api/projects/proj-1", () =>
        HttpResponse.json({
          id: "proj-1",
          title: REPO,
          status: "active",
          doc_ref: null,
          autonomy: "propose",
          budget_usd: null,
          created_at: "2026-07-29T00:00:00Z",
          updated_at: "2026-07-29T00:00:00Z",
        }),
      ),
    );
    renderStep4();
    await waitForChip("ready to dispatch");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-footer-primary"));
    expect(screen.queryByTestId("posture-confirm-dialog")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/projects/proj-1");
    });
    expect(postureWriteCalls).toBe(0);
  });

  it("adversarial: a raw change event dispatched directly at a disabled radio never reaches React state, so finishing still takes the unaffected (observe) path", async () => {
    let projectPosts = 0;
    let postureWrites = 0;
    mockMount({ workspaces: [], currentPosture: "observe" });
    server.use(
      http.post(ENGINE_URL + "/posture", () => {
        postureWrites += 1;
        return HttpResponse.json({ repo: { owner: OWNER, repo: REPO }, posture: "self_merge" });
      }),
      http.get("/api/projects", () => HttpResponse.json([])),
      http.post("/api/projects", () => {
        projectPosts += 1;
        return HttpResponse.json({ id: "proj-1", title: REPO }, { status: 201 });
      }),
      http.patch("/api/projects/proj-1", () =>
        HttpResponse.json({ id: "proj-1", title: REPO }),
      ),
    );
    renderStep4();
    await waitForChip("ready to dispatch");
    const selfMerge = screen.getByTestId("step4-posture-self_merge");
    expect(selfMerge).toBeDisabled();
    fireEvent.change(selfMerge, { target: { checked: true } });
    fireEvent.click(screen.getByTestId("step4-footer-primary"));
    expect(screen.queryByTestId("posture-confirm-dialog")).not.toBeInTheDocument();
    expect(postureWrites).toBe(0);
    await waitFor(() => {
      expect(projectPosts).toBe(1);
    });
  });
});

describe("finish — project chain", () => {
  function mockProjectChain(opts: {
    existingProjects?: unknown[];
    patchStatus?: number;
    patchDetail?: string;
  } = {}) {
    const { existingProjects = [], patchStatus, patchDetail } = opts;
    let createCalls = 0;
    let lastCreateBody: unknown = null;
    server.use(
      http.get("/api/projects", () => HttpResponse.json(existingProjects)),
      http.post("/api/projects", async ({ request }) => {
        createCalls += 1;
        lastCreateBody = await request.json();
        return HttpResponse.json(
          {
            id: "proj-new",
            title: REPO,
            status: "active",
            doc_ref: null,
            autonomy: "propose",
            budget_usd: null,
            created_at: "2026-07-29T00:00:00Z",
            updated_at: "2026-07-29T00:00:00Z",
          },
          { status: 201 },
        );
      }),
      http.patch(/\/api\/projects\/[^/]+$/, () => {
        if (patchStatus !== undefined) {
          return HttpResponse.json({ detail: patchDetail ?? "nope" }, { status: patchStatus });
        }
        return HttpResponse.json({
          id: "proj-new",
          title: REPO,
          status: "active",
          doc_ref: null,
          autonomy: "propose",
          budget_usd: null,
          created_at: "2026-07-29T00:00:00Z",
          updated_at: "2026-07-29T00:00:00Z",
        });
      }),
    );
    return {
      getCreateCalls: () => createCalls,
      getLastCreateBody: () => lastCreateBody,
    };
  }

  it("pass: creates the project, binds it, and navigates with no unbound state — the mint call carries intended_engine_key", async () => {
    mockMount({ overall: "pass" });
    const chain = mockProjectChain();
    renderStep4();
    await waitForChip("ready to dispatch");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/projects/proj-new");
    });
    expect(screen.getByTestId("location-probe")).toHaveAttribute("data-state", "null");
    expect(chain.getLastCreateBody()).toMatchObject({ intended_engine_key: ENGINE_KEY });
  });

  it("fail: creates the project, navigates, carries preflight_blocked, and the mint call carries intended_engine_key", async () => {
    mockMount({ overall: "fail" });
    const chain = mockProjectChain();
    renderStep4();
    await waitForChip("blocked");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/projects/proj-new");
    });
    expect(screen.getByTestId("location-probe")).toHaveAttribute(
      "data-state",
      JSON.stringify({ connectUnboundReason: "preflight_blocked" }),
    );
    expect(chain.getLastCreateBody()).toMatchObject({ intended_engine_key: ENGINE_KEY });
  });

  it("unknown/never-run: creates the project, navigates, carries preflight_unknown, and the mint call carries intended_engine_key", async () => {
    mockMount({ overall: "unknown" });
    const chain = mockProjectChain();
    renderStep4();
    await waitForChip("partially ready");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/projects/proj-new");
    });
    expect(screen.getByTestId("location-probe")).toHaveAttribute(
      "data-state",
      JSON.stringify({ connectUnboundReason: "preflight_unknown" }),
    );
    expect(chain.getLastCreateBody()).toMatchObject({ intended_engine_key: ENGINE_KEY });
  });

  it("422 on bind: carries bind_rejected, and the mint call still carries intended_engine_key", async () => {
    mockMount({ overall: "pass" });
    const chain = mockProjectChain({ patchStatus: 422, patchDetail: "engine preflight not passing" });
    renderStep4();
    await waitForChip("ready to dispatch");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/projects/proj-new");
    });
    expect(screen.getByTestId("location-probe")).toHaveAttribute(
      "data-state",
      JSON.stringify({ connectUnboundReason: "bind_rejected" }),
    );
    expect(chain.getLastCreateBody()).toMatchObject({ intended_engine_key: ENGINE_KEY });
  });

  it("409 on bind: carries bind_conflict, and the mint call still carries intended_engine_key", async () => {
    mockMount({ overall: "pass" });
    const chain = mockProjectChain({ patchStatus: 409, patchDetail: "project_has_live_workspaces" });
    renderStep4();
    await waitForChip("ready to dispatch");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/projects/proj-new");
    });
    expect(screen.getByTestId("location-probe")).toHaveAttribute(
      "data-state",
      JSON.stringify({ connectUnboundReason: "bind_conflict" }),
    );
    expect(chain.getLastCreateBody()).toMatchObject({ intended_engine_key: ENGINE_KEY });
  });

  it("reuses an existing project bound to this engine instead of creating a duplicate", async () => {
    mockMount({ overall: "pass" });
    const chain = mockProjectChain({
      existingProjects: [
        { id: "proj-existing", title: "Widget", status: "active", doc_ref: null, autonomy: "propose", budget_usd: null, engine_key: ENGINE_KEY, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", workspace_count: 0, total_spend_usd: 0 },
      ],
    });
    renderStep4();
    await waitForChip("ready to dispatch");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/projects/proj-existing");
    });
    expect(chain.getCreateCalls()).toBe(0);
  });

  it("reuses a project recording intent toward this engine when nothing is bound", async () => {
    mockMount({ overall: "pass" });
    const chain = mockProjectChain({
      existingProjects: [
        {
          id: "proj-intent",
          title: "Widget",
          status: "active",
          doc_ref: null,
          autonomy: "propose",
          budget_usd: null,
          engine_key: null,
          intended_engine_key: ENGINE_KEY,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          workspace_count: 0,
          total_spend_usd: 0,
        },
      ],
    });
    renderStep4();
    await waitForChip("ready to dispatch");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/projects/proj-intent");
    });
    expect(chain.getCreateCalls()).toBe(0);
  });

  it("never adopts a project bound to a different engine, even one carrying this engine as its intent", async () => {
    mockMount({ overall: "pass" });
    const chain = mockProjectChain({
      existingProjects: [
        {
          id: "proj-other",
          title: "Other",
          status: "active",
          doc_ref: null,
          autonomy: "propose",
          budget_usd: null,
          engine_key: "other/repo",
          intended_engine_key: ENGINE_KEY,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          workspace_count: 0,
          total_spend_usd: 0,
        },
      ],
    });
    renderStep4();
    await waitForChip("ready to dispatch");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-footer-primary"));
    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent("/projects/proj-new");
    });
    expect(chain.getCreateCalls()).toBe(1);
    expect(chain.getLastCreateBody()).toMatchObject({ intended_engine_key: ENGINE_KEY });
  });

  it("a failed posture confirm aborts the chain — no project is created", async () => {
    let createCalls = 0;
    mockMount({ overall: "pass", currentPosture: "observe" });
    server.use(
      http.post(ENGINE_URL + "/posture", () =>
        HttpResponse.json({ detail: "control token mismatch" }, { status: 403 }),
      ),
      http.get("/api/projects", () => HttpResponse.json([])),
      http.post("/api/projects", () => {
        createCalls += 1;
        return HttpResponse.json({ id: "proj-new", title: REPO }, { status: 201 });
      }),
    );
    renderStep4();
    await waitForChip("ready to dispatch");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-posture-propose"));
    await user.click(screen.getByTestId("step4-footer-primary"));
    await user.click(screen.getByTestId("posture-confirm-submit"));
    expect(await screen.findByTestId("posture-confirm-error")).toBeInTheDocument();
    expect(createCalls).toBe(0);
    expect(screen.getByTestId("location-probe")).toHaveTextContent("/start");
  });

  it("an unexpected project-chain failure (listProjects errors) shows an inline error and does not navigate", async () => {
    mockMount({ overall: "pass" });
    server.use(
      http.get("/api/projects", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    renderStep4();
    await waitForChip("ready to dispatch");
    const user = userEvent.setup();
    await user.click(screen.getByTestId("step4-footer-primary"));
    expect(await screen.findByTestId("step4-finish-error")).toHaveTextContent(/500|boom/);
    expect(screen.getByTestId("location-probe")).toHaveTextContent("/start");
  });
});
