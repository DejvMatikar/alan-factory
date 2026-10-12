import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { EngineSummary, ProjectListEntry, WorkspaceSummary } from "../../../api";
import { writeControlToken } from "../../../controlToken";
import { SettingsUiContext } from "../../../shell/SettingsUiProvider";
import {
  DEFAULT_KEY_STATES,
  LISTED_DEFAULT_KEY,
  makeEngineSummary,
  makeEngineSummaryFor,
  STUB_DEFAULT_ENGINE_KEY,
} from "../../../test/engineFixtures";
import { makeProjectListEntry } from "../../../test/projectFixtures";
import { server } from "../../../test/server";
import { useConnectStates } from "./useConnectStates";

function engine(owner: string, repo: string): EngineSummary {
  return makeEngineSummary({ repo: { owner, repo } });
}

function secretsResponse(names: string[]) {
  return {
    secrets: names.map((name) => ({
      name,
      fingerprint: "abc123",
      last_verified: null,
      expires_at: null,
      expiry_warned_at: null,
    })),
  };
}

function onboardingResponse(overrides: {
  scaffoldPrNumber?: number | null;
  scaffoldStatus?: "pass" | "fail";
  protectionApplied?: string[];
} = {}) {
  return {
    state: {
      slug: "x",
      stack: null,
      scaffold_pr_number: overrides.scaffoldPrNumber ?? 1,
      scaffold_pr_url: null,
      authoritative_checks: [],
      protection_applied_keys: overrides.protectionApplied ?? ["require_pr_review"],
      protection_deferred: [],
    },
    checklist: [
      {
        check: "review_and_merge_scaffold_pr",
        status: overrides.scaffoldStatus ?? "pass",
        message: "m",
        remedy: "",
        remedy_url: "",
      },
    ],
    overall: "pass",
  };
}

let closedCount = 0;

function Wrapper({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <SettingsUiContext.Provider value={{ isOpen: false, open: vi.fn(), close: vi.fn(), closedCount }}>
      {children}
    </SettingsUiContext.Provider>
  );
}

describe("useConnectStates", () => {
  beforeEach(() => {
    localStorage.clear();
    closedCount = 0;
  });

  it("never fetches onboarding for the default engine, even when it is present in the list", async () => {
    let defaultFetched = false;
    server.use(
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.get("/api/engines/DejvMatikar/alan/onboarding", () => {
        defaultFetched = true;
        return HttpResponse.json(onboardingResponse());
      }),
      http.get("/api/engines/acme/widget/onboarding", () =>
        HttpResponse.json(onboardingResponse({ scaffoldStatus: "fail", protectionApplied: [] })),
      ),
    );
    const { result } = renderHook(
      () =>
        useConnectStates({
          engines: [engine("DejvMatikar", "alan"), engine("acme", "widget")],
          workspaces: [],
          projects: [],
          defaultEngineKey: STUB_DEFAULT_ENGINE_KEY,
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => {
      expect(result.current.resumes).toHaveLength(1);
    });
    expect(defaultFetched).toBe(false);
  });

  it("omits a repo whose onboarding read rejects, without excluding the others", async () => {
    server.use(
      http.get("/api/secrets", () =>
        HttpResponse.json(secretsResponse(["acme/widget:transport", "globex/gizmo:transport"])),
      ),
      http.get("/api/engines/acme/widget/onboarding", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
      http.get("/api/engines/globex/gizmo/onboarding", () =>
        HttpResponse.json(onboardingResponse({ scaffoldStatus: "fail", protectionApplied: [] })),
      ),
    );
    const { result } = renderHook(
      () =>
        useConnectStates({
          engines: [engine("acme", "widget"), engine("globex", "gizmo")],
          workspaces: [],
          projects: [],
          defaultEngineKey: STUB_DEFAULT_ENGINE_KEY,
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => {
      expect(result.current.resumes).toEqual([{ owner: "globex", repo: "gizmo", step: 3 }]);
    });
  });

  it("calls listSecrets even when there are no non-default engines, so the host-setup line still resolves", async () => {
    let secretsFetched = false;
    writeControlToken("t");
    server.use(
      http.get("/api/secrets", () => {
        secretsFetched = true;
        return HttpResponse.json(secretsResponse(["DejvMatikar/alan:transport"]));
      }),
    );
    const { result } = renderHook(
      () =>
        useConnectStates({
          engines: [engine("DejvMatikar", "alan")],
          workspaces: [],
          projects: [],
          defaultEngineKey: STUB_DEFAULT_ENGINE_KEY,
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => {
      expect(secretsFetched).toBe(true);
    });
    await waitFor(() => {
      expect(result.current.hostSetupMessage).toBeNull();
    });
    expect(result.current.resumes).toEqual([]);
  });

  it("keeps the last-known resume list visible while a refetch is in flight (no flash)", async () => {
    let onboardingCall = 0;
    const gate: { resolve: ((value: ReturnType<typeof onboardingResponse>) => void) | null } = {
      resolve: null,
    };
    server.use(
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse(["acme/widget:transport"]))),
      http.get("/api/engines/acme/widget/onboarding", () => {
        onboardingCall += 1;
        if (onboardingCall === 1) {
          return HttpResponse.json(onboardingResponse({ scaffoldStatus: "fail", protectionApplied: [] }));
        }
        return new Promise((resolve) => {
          gate.resolve = (value) => {
            resolve(HttpResponse.json(value));
          };
        });
      }),
    );
    const { result, rerender } = renderHook(
      () =>
        useConnectStates({
          engines: [engine("acme", "widget")],
          workspaces: [],
          projects: [],
          defaultEngineKey: STUB_DEFAULT_ENGINE_KEY,
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => {
      expect(result.current.resumes).toHaveLength(1);
    });

    closedCount = 1;
    rerender();
    await waitFor(() => {
      expect(onboardingCall).toBe(2);
    });
    expect(result.current.resumes).toHaveLength(1);

    gate.resolve?.(onboardingResponse());
    await waitFor(() => {
      expect(result.current.resumes).toHaveLength(0);
    });
  });

  it("sorts multiple resume entries by owner/repo for deterministic e2e ordering", async () => {
    server.use(
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.get("/api/engines/zzz/repo/onboarding", () =>
        HttpResponse.json(onboardingResponse({ scaffoldStatus: "fail", protectionApplied: [] })),
      ),
      http.get("/api/engines/aaa/repo/onboarding", () =>
        HttpResponse.json(onboardingResponse({ scaffoldStatus: "fail", protectionApplied: [] })),
      ),
    );
    const { result } = renderHook(
      () =>
        useConnectStates({
          engines: [engine("zzz", "repo"), engine("aaa", "repo")],
          workspaces: [],
          projects: [],
          defaultEngineKey: STUB_DEFAULT_ENGINE_KEY,
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => {
      expect(result.current.resumes).toHaveLength(2);
    });
    expect(result.current.resumes.map((r) => `${r.owner}/${r.repo}`)).toEqual([
      "aaa/repo",
      "zzz/repo",
    ]);
  });

  it("does not update state after unmount when an in-flight onboarding read resolves late", async () => {
    const gate: { resolve: ((value: ReturnType<typeof onboardingResponse>) => void) | null } = {
      resolve: null,
    };
    server.use(
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse(["acme/widget:transport"]))),
      http.get(
        "/api/engines/acme/widget/onboarding",
        () =>
          new Promise((resolve) => {
            gate.resolve = (value) => {
              resolve(HttpResponse.json(value));
            };
          }),
      ),
    );
    const { unmount } = renderHook(
      () =>
        useConnectStates({
          engines: [engine("acme", "widget")],
          workspaces: [],
          projects: [],
          defaultEngineKey: STUB_DEFAULT_ENGINE_KEY,
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => {
      expect(gate.resolve).not.toBeNull();
    });
    unmount();
    gate.resolve?.(onboardingResponse({ scaffoldStatus: "fail", protectionApplied: [] }));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it("tolerates a rejected listSecrets call without crashing (fail-quiet), naming only the control-token clause", async () => {
    server.use(http.get("/api/secrets", () => HttpResponse.error()));
    const { result } = renderHook(
      () =>
        useConnectStates({
          engines: [engine("acme", "widget")],
          workspaces: [],
          projects: [],
          defaultEngineKey: STUB_DEFAULT_ENGINE_KEY,
        }),
      { wrapper: Wrapper },
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(result.current.resumes).toEqual([]);
    expect(result.current.hostSetupMessage).toBe("Finish host setup — control token not set");
  });

  it("omits the forge-credential clause when listSecrets fails and the token is present — unknown is never read as definitively missing", async () => {
    writeControlToken("t");
    server.use(http.get("/api/secrets", () => HttpResponse.error()));
    const { result } = renderHook(
      () =>
        useConnectStates({
          engines: [engine("acme", "widget")],
          workspaces: [],
          projects: [],
          defaultEngineKey: STUB_DEFAULT_ENGINE_KEY,
        }),
      { wrapper: Wrapper },
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(result.current.hostSetupMessage).toBeNull();
  });

  it("keeps the last-known-missing transport clause when a later listSecrets call fails, rather than resetting to unknown", async () => {
    writeControlToken("t");
    let call = 0;
    server.use(
      http.get("/api/secrets", () => {
        call += 1;
        if (call === 1) return HttpResponse.json(secretsResponse([]));
        return HttpResponse.error();
      }),
    );
    const { result, rerender } = renderHook(
      () =>
        useConnectStates({
          engines: [engine("acme", "widget")],
          workspaces: [],
          projects: [],
          defaultEngineKey: STUB_DEFAULT_ENGINE_KEY,
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => {
      expect(result.current.hostSetupMessage).toBe("Finish host setup — forge credential not set");
    });

    closedCount = 1;
    rerender();
    await waitFor(() => {
      expect(call).toBe(2);
    });
    expect(result.current.hostSetupMessage).toBe("Finish host setup — forge credential not set");
  });

  it("never fetches onboarding for an engine with a bound project — the dropped HTTP request", async () => {
    let operationalFetched = false;
    server.use(
      http.get("/api/secrets", () =>
        HttpResponse.json(secretsResponse(["acme/widget:transport", "globex/gizmo:transport"])),
      ),
      http.get("/api/engines/acme/widget/onboarding", () => {
        operationalFetched = true;
        return HttpResponse.json(onboardingResponse({ scaffoldStatus: "fail", protectionApplied: [] }));
      }),
      http.get("/api/engines/globex/gizmo/onboarding", () =>
        HttpResponse.json(onboardingResponse({ scaffoldStatus: "fail", protectionApplied: [] })),
      ),
    );
    const { result } = renderHook(
      () =>
        useConnectStates({
          engines: [engine("acme", "widget"), engine("globex", "gizmo")],
          workspaces: [],
          projects: [makeProjectListEntry({ id: "p1", engine_key: "acme/widget" })],
          defaultEngineKey: STUB_DEFAULT_ENGINE_KEY,
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => {
      expect(result.current.resumes).toEqual([{ owner: "globex", repo: "gizmo", step: 3 }]);
    });
    expect(operationalFetched).toBe(false);
  });

  it("does not fan out while projects is still null, leaving resumes at their last-known value", async () => {
    let onboardingFetched = false;
    server.use(
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.get("/api/engines/acme/widget/onboarding", () => {
        onboardingFetched = true;
        return HttpResponse.json(onboardingResponse({ scaffoldStatus: "fail", protectionApplied: [] }));
      }),
    );
    const { result } = renderHook(
      () =>
        useConnectStates({
          engines: [engine("acme", "widget")],
          workspaces: [],
          projects: null,
          defaultEngineKey: STUB_DEFAULT_ENGINE_KEY,
        }),
      { wrapper: Wrapper },
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(onboardingFetched).toBe(false);
    expect(result.current.resumes).toEqual([]);
  });

  it("does not fan out on a Settings-close either, while projects is still null", async () => {
    let onboardingFetched = false;
    server.use(
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.get("/api/engines/acme/widget/onboarding", () => {
        onboardingFetched = true;
        return HttpResponse.json(onboardingResponse({ scaffoldStatus: "fail", protectionApplied: [] }));
      }),
    );
    const { result, rerender } = renderHook(
      () =>
        useConnectStates({
          engines: [engine("acme", "widget")],
          workspaces: [],
          projects: null,
          defaultEngineKey: STUB_DEFAULT_ENGINE_KEY,
        }),
      { wrapper: Wrapper },
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(onboardingFetched).toBe(false);

    closedCount = 1;
    rerender();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(onboardingFetched).toBe(false);
    expect(result.current.resumes).toEqual([]);
  });

  it("suppresses a bound engine even while workspaces is still null — the archived-tab decision", async () => {
    let secretsFetched = false;
    let onboardingFetched = false;
    server.use(
      http.get("/api/secrets", () => {
        secretsFetched = true;
        return HttpResponse.json(secretsResponse([]));
      }),
      http.get("/api/engines/acme/widget/onboarding", () => {
        onboardingFetched = true;
        return HttpResponse.json(onboardingResponse({ scaffoldStatus: "fail", protectionApplied: [] }));
      }),
    );
    const { result } = renderHook(
      () =>
        useConnectStates({
          engines: [engine("acme", "widget")],
          workspaces: null,
          projects: [makeProjectListEntry({ id: "p1", engine_key: "acme/widget" })],
          defaultEngineKey: STUB_DEFAULT_ENGINE_KEY,
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => {
      expect(secretsFetched).toBe(true);
    });
    expect(onboardingFetched).toBe(false);
    expect(result.current.resumes).toEqual([]);
  });

  it("does not refetch when workspaces/projects get fresh array identities with unchanged content (SSE-tick storm guard)", async () => {
    let onboardingCalls = 0;
    server.use(
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.get("/api/engines/acme/widget/onboarding", () => {
        onboardingCalls += 1;
        return HttpResponse.json(onboardingResponse({ scaffoldStatus: "fail", protectionApplied: [] }));
      }),
    );
    const stableEngines = [engine("acme", "widget")];
    const { result, rerender } = renderHook(
      (props: { workspaces: WorkspaceSummary[]; projects: ProjectListEntry[] }) =>
        useConnectStates({
          engines: stableEngines,
          workspaces: props.workspaces,
          projects: props.projects,
          defaultEngineKey: STUB_DEFAULT_ENGINE_KEY,
        }),
      { wrapper: Wrapper, initialProps: { workspaces: [], projects: [] } },
    );
    await waitFor(() => {
      expect(result.current.resumes).toHaveLength(1);
    });
    expect(onboardingCalls).toBe(1);

    rerender({ workspaces: [], projects: [] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(onboardingCalls).toBe(1);
  });
});

const WIDGET_KEY = "acme/widget";
const CANDIDATE_POOL = [STUB_DEFAULT_ENGINE_KEY, LISTED_DEFAULT_KEY, WIDGET_KEY];

interface LookupTally {
  readonly onboarding: string[];
  secretsRequests: number;
}

function stubOnboardingLookups(): LookupTally {
  const tally: LookupTally = { onboarding: [], secretsRequests: 0 };
  server.use(
    http.get("/api/secrets", () => {
      tally.secretsRequests += 1;
      return HttpResponse.json(secretsResponse(CANDIDATE_POOL.map((key) => `${key}:transport`)));
    }),
    http.get(/\/api\/engines\/[^/]+\/[^/]+\/onboarding$/, ({ request }) => {
      const [, , , owner, repo] = new URL(request.url).pathname.split("/");
      tally.onboarding.push(`${owner}/${repo}`);
      return HttpResponse.json(onboardingResponse({ scaffoldStatus: "fail", protectionApplied: [] }));
    }),
  );
  return tally;
}

function resumeFor(key: string) {
  const [owner = "", repo = ""] = key.split("/");
  return { owner, repo, step: 3 };
}

function byLocale(a: string, b: string): number {
  return a.localeCompare(b);
}

function otherThan(defaultKey: string): string[] {
  return CANDIDATE_POOL.filter((key) => key !== defaultKey).sort(byLocale);
}

describe("useConnectStates — candidates are computed against the given default key", () => {
  beforeEach(() => {
    localStorage.clear();
    closedCount = 0;
  });

  it.each(DEFAULT_KEY_STATES)(
    "looks up onboarding and resumes for every engine except the default key (%s)",
    async (_state, defaultKey) => {
      const resolved = defaultKey;
      const tally = stubOnboardingLookups();
      const stableEngines = CANDIDATE_POOL.map((key) => makeEngineSummaryFor(key));
      const { result } = renderHook(
        () =>
          useConnectStates({
            engines: stableEngines,
            workspaces: [],
            projects: [],
            defaultEngineKey: resolved,
          }),
        { wrapper: Wrapper },
      );
      await waitFor(() => {
        expect(result.current.resumes).toEqual(otherThan(resolved).map(resumeFor));
      });
      expect([...tally.onboarding].sort(byLocale)).toEqual(otherThan(resolved));
    },
  );

  it("recomputes its candidates when the default key changes for the same engines", async () => {
    const tally = stubOnboardingLookups();
    const stableEngines = [
      makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY),
      makeEngineSummaryFor(LISTED_DEFAULT_KEY),
    ];
    const { result, rerender } = renderHook(
      (props: { defaultEngineKey: string }) =>
        useConnectStates({
          engines: stableEngines,
          workspaces: [],
          projects: [],
          defaultEngineKey: props.defaultEngineKey,
        }),
      { wrapper: Wrapper, initialProps: { defaultEngineKey: STUB_DEFAULT_ENGINE_KEY } },
    );
    await waitFor(() => {
      expect(result.current.resumes).toEqual([resumeFor(LISTED_DEFAULT_KEY)]);
    });

    rerender({ defaultEngineKey: LISTED_DEFAULT_KEY });
    await waitFor(() => {
      expect(result.current.resumes).toEqual([resumeFor(STUB_DEFAULT_ENGINE_KEY)]);
    });
    expect(tally.onboarding).toEqual([LISTED_DEFAULT_KEY, STUB_DEFAULT_ENGINE_KEY]);
  });

  it("never looks up onboarding for an engine that is the given default even when it is the only engine", async () => {
    const tally = stubOnboardingLookups();
    const { result } = renderHook(
      () =>
        useConnectStates({
          engines: [makeEngineSummaryFor(LISTED_DEFAULT_KEY)],
          workspaces: [],
          projects: [],
          defaultEngineKey: LISTED_DEFAULT_KEY,
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => {
      expect(tally.secretsRequests).toBeGreaterThan(0);
    });
    expect(result.current.resumes).toEqual([]);
    expect(tally.onboarding).toEqual([]);
  });
});
